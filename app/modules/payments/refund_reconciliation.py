"""Read-only YooKassa refund reconciliation. Never reissue an ambiguous refund.

Only RUB is currently supported; multicurrency is a separate future epic.
All bank write operations are forbidden in this module. Reconciliation runs
only when PAYMENTS_RECONCILIATION_ENABLED was explicitly enabled.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, exists, literal, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.modules.accounts.models import utc
from app.modules.commerce.models import Job
from app.modules.payments.gateway import GatewayError, gateway, money, provider_reference
from app.modules.payments.models import Payment, Refund

_UNSETTLED = ('uncertain', 'provider_pending')
_TERMINAL = ('succeeded', 'canceled')
_MAX_CHECKS = 12


def validate_refund_response(remote: object, *, refund_id: str | None,
                             payment_id: str, amount) -> str:
    """Validate an authoritative refund object or an initial POST acknowledgement."""
    if not isinstance(remote, dict):
        raise HTTPException(409, 'Malformed refund response')
    external_id = remote.get('id')
    amount_object = remote.get('amount')
    try:
        provider_reference(external_id)
    except GatewayError as exc:
        raise HTTPException(409, 'Malformed refund reference') from exc
    if (not isinstance(external_id, str) or not external_id or len(external_id) > 150 or
            (refund_id is not None and external_id != refund_id) or
            remote.get('payment_id') != payment_id or
            not isinstance(amount_object, dict) or amount_object.get('currency') != 'RUB'):
        raise HTTPException(409, 'Refund identity or currency mismatch')
    try:
        valid_amount = money(amount_object.get('value')) == money(amount)
    except GatewayError:
        valid_amount = False
    if not valid_amount:
        raise HTTPException(409, 'Refund amount mismatch')
    if remote.get('status') not in ('pending', *_TERMINAL):
        raise HTTPException(409, 'Unknown provider refund state')
    return remote['status']


async def verify_refund_status(db, refund_id: UUID, *, bank=None) -> str:
    """GET outside transaction locks; revalidate IDs under payment/refund locks."""
    refund = await db.get(Refund, refund_id)
    if refund is None:
        raise HTTPException(404, 'Refund not found')
    if not refund.provider_id:
        raise HTTPException(409, 'Provider refund ID unknown; manual bank audit required')
    if refund.status in _TERMINAL:
        return refund.status
    external_id = refund.provider_id
    try:
        remote = await (bank if bank is not None else gateway()).refund_status(external_id)
    except GatewayError as exc:
        raise HTTPException(503, 'Refund status temporarily unavailable') from exc
    # Lock payment before refund, matching the refund-budget reservation order.
    payment = await db.scalar(select(Payment).where(Payment.id == refund.payment_id).with_for_update())
    refund = await db.scalar(select(Refund).where(Refund.id == refund_id).with_for_update()
                             .execution_options(populate_existing=True))
    if (not payment or payment.status != 'succeeded' or not payment.provider_id or
            not refund or refund.payment_id != payment.id or refund.provider_id != external_id):
        raise HTTPException(409, 'Refund/payment linkage changed')
    new_state = validate_refund_response(remote, refund_id=external_id,
                                         payment_id=payment.provider_id, amount=refund.amount)
    if refund.status in _TERMINAL:
        if refund.status != new_state:
            raise HTTPException(409, 'Terminal refund state contradicts provider response')
        return refund.status
    if refund.status not in _UNSETTLED:
        raise HTTPException(409, 'Refund is not awaiting reconciliation')
    refund.status = 'provider_pending' if new_state == 'pending' else new_state
    return refund.status


def refund_next_check_delay(attempts: int) -> timedelta:
    return timedelta(minutes=min(2 ** min(max(attempts, 0), 8), 240))


async def schedule_refund_reconciliation(db, limit: int = 25) -> int:
    if not get_settings().payments_reconciliation_enabled:
        return 0
    limit = min(max(limit, 1), 100)
    jobkey = literal('refund_reconcile:') + cast(Refund.id, String)
    candidates = (await db.scalars(select(Refund).where(
        Refund.status.in_(_UNSETTLED), Refund.provider_id.is_not(None),
        ~exists(select(Job.id).where(Job.dedup_key == jobkey))
    ).order_by(Refund.id).limit(limit))).all()
    for refund in candidates:
        db.add(Job(dedup_key=f'refund_reconcile:{refund.id}',
                   kind='refund_reconcile', payload={'refund_id': str(refund.id)},
                   status='pending', due_at=utc(), attempts=0))
    if not candidates:
        return 0
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return 0
    return len(candidates)


async def reconcile_refund_job(db, job: Job) -> None:
    if not get_settings().payments_reconciliation_enabled:
        job.status = 'pending'
        job.due_at = utc() + timedelta(minutes=30)
        job.last_error = 'reconciliation_paused'
        return
    try:
        refund_id = UUID(job.payload['refund_id'])
    except (KeyError, ValueError, TypeError, AttributeError):
        job.status = 'manual_review'
        job.last_error = 'invalid_refund_reference'
        return
    refund = await db.get(Refund, refund_id)
    if refund is None:
        job.status = 'manual_review'
        job.last_error = 'refund_missing'
        return
    if refund.status in _TERMINAL:
        job.status = 'done'
        return
    if refund.status not in _UNSETTLED or not refund.provider_id:
        job.status = 'manual_review'
        job.last_error = 'refund_provider_reference_not_verifiable'
        return
    try:
        await verify_refund_status(db, refund_id)
    except HTTPException as exc:
        if exc.status_code not in (502, 503, 504):
            job.status = 'manual_review'
            job.last_error = f'provider_validation_{exc.status_code}'
            return
        job.last_error = 'refund_provider_temporarily_unavailable'
    if refund.status in _TERMINAL:
        job.status = 'done'
        job.last_error = None
    elif job.attempts >= _MAX_CHECKS:
        job.status = 'manual_review'
        job.last_error = 'refund_status_unresolved_after_poll_limit'
    else:
        job.status = 'pending'
        job.due_at = utc() + refund_next_check_delay(job.attempts)
