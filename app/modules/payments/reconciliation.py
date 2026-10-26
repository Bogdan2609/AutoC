"""Durable read-only reconciliation of uncertain YooKassa SBP payments.

A provider GET is always authoritative. Never create another payment or run a
supplier operation directly. Uses existing unique jobs.dedup_key (no migration).
Disabled until PAYMENTS_RECONCILIATION_ENABLED=true is explicitly configured.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import String, cast, exists, literal, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.modules.accounts.models import utc
from app.modules.commerce.models import Job
from app.modules.payments.models import Payment

_UNSETTLED = ('creating', 'pending', 'waiting_for_capture', 'reconciliation_required')
_TERMINAL = ('succeeded', 'canceled')
_MAX_CHECKS = 12


def next_check_delay(attempts: int) -> timedelta:
    """Exponential polling interval, bounded to prevent excessive provider GETs."""
    return timedelta(minutes=min(2 ** min(max(attempts, 0), 8), 240))


async def schedule_payment_reconciliation(db, limit: int = 25) -> int:
    """Create a durable polling job once per unsettled provider payment.

    Unresolved *creation* with no external ID cannot be safely queried: it
    requires manual portal reconciliation, never a second charge attempt.
    """
    s = get_settings()
    if not s.payments_reconciliation_enabled:
        return 0
    limit = min(max(limit, 1), 100)
    jobkey = literal('payment_reconcile:') + cast(Payment.id, String)
    candidates = (await db.scalars(
        select(Payment).where(
            Payment.status.in_(_UNSETTLED),
            Payment.provider_id.is_not(None),
            Payment.created_at <= utc() - timedelta(seconds=60),
            ~exists(select(Job.id).where(Job.dedup_key == jobkey)),
        ).order_by(Payment.created_at, Payment.id).limit(limit)
    )).all()
    for payment in candidates:
        db.add(Job(dedup_key=f'payment_reconcile:{payment.id}',
                   kind='payment_reconcile', payload={'payment_id': str(payment.id)},
                   status='pending', due_at=utc(), attempts=0))
    if not candidates:
        return 0
    # A unique index ensures concurrent worker refresh loops cannot enqueue
    # two jobs. In conflict the other worker won; a future scan can continue.
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return 0
    return len(candidates)


async def schedule_missing_payment_attention(db, limit: int = 25) -> int:
    """Persist operator attention for unknown external create outcomes.

    A missing provider ID cannot be queried or recreated safely. The job is
    informational and is deliberately created in manual_review, never executed.
    """
    limit = min(max(limit, 1), 100)
    dedup = literal('payment_manual:') + cast(Payment.id, String)
    rows = (await db.scalars(select(Payment).where(
        Payment.status.in_(_UNSETTLED), Payment.provider_id.is_(None),
        Payment.created_at <= utc() - timedelta(minutes=2),
        ~exists(select(Job.id).where(Job.dedup_key == dedup)),
    ).order_by(Payment.created_at, Payment.id).limit(limit))).all()
    for payment in rows:
        db.add(Job(dedup_key=f'payment_manual:{payment.id}',
                   kind='payment_manual_review', status='manual_review', attempts=0,
                   due_at=utc(), payload={'payment_id': str(payment.id)},
                   last_error='external_payment_id_missing_portal_reconciliation_required'))
    if not rows:
        return 0
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return 0
    return len(rows)


async def reconcile_payment_job(db, job: Job) -> None:
    """Re-query bank status and let the existing locked handler materialize once."""
    from uuid import UUID
    from app.modules.payments.api import verify_remote

    if not get_settings().payments_reconciliation_enabled:
        # Pause without stranding a deduplicated job permanently.
        job.status = 'pending'
        job.due_at = utc() + timedelta(minutes=30)
        job.last_error = 'reconciliation_paused'
        return
    try:
        payment_id = UUID(job.payload['payment_id'])
    except (KeyError, ValueError, TypeError, AttributeError):
        job.status = 'manual_review'
        job.last_error = 'invalid_payment_reference'
        return
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if payment is None:
        job.status = 'manual_review'
        job.last_error = 'payment_missing'
        return
    if payment.status in _TERMINAL:
        job.status = 'done'
        return
    if payment.status not in _UNSETTLED or not payment.provider_id:
        job.status = 'manual_review'
        job.last_error = 'provider_reference_not_verifiable'
        return
    try:
        await verify_remote(db, payment)
    except HTTPException as exc:
        # Unknown payment identity, wrong RUB amount, wrong workflow and other
        # 409s are NOT transient network faults. No second charge is attempted.
        if exc.status_code not in (502, 503, 504):
            job.status = 'manual_review'
            job.last_error = f'provider_validation_{exc.status_code}'
            return
        job.last_error = 'provider_temporarily_unavailable'
    if payment.status in _TERMINAL:
        job.status = 'done'
        job.last_error = None
    elif job.attempts >= _MAX_CHECKS:
        job.status = 'manual_review'
        job.last_error = 'payment_status_unresolved_after_poll_limit'
    else:
        job.status = 'pending'
        job.due_at = utc() + next_check_delay(job.attempts)
