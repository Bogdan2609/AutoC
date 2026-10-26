"""Read-only operational readiness and audit views for authenticated operators.

No external provider writes, credential values or buyer PII are returned.
Counts are observational; they do not authorize procurement or financial actions.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job, Order, OrderWorkflowEvent, Procurement
from app.modules.marketplace.models import FulfillmentNode, SupplierAccount, SupplierDestination
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.payments.models import Refund
from app.modules.suppliers.models import Supplier
from app.modules.marketplace.receiving_contact import ReceivingContactError, validate_receiving_contact

router = APIRouter(prefix='/api/v1/backoffice/monitor', tags=['operator-monitor'],
                   dependencies=[Depends(require_admin)])

REVIEW_ORDER_STATUSES = (
    'request_awaiting_operator_review', 'request_awaiting_delivery_quote',
    'request_quote_declined', 'fulfillment_review', 'fulfillment_exception',
    'procurement_manual_review', 'supplier_rejected',
)
REVIEW_PURCHASE_STATUSES = ('manual_review', 'unknown', 'sent_unverified', 'supplier_rejected',
                            'dispatch_unknown', 'accepted_unverified', 'accepted', 'reconciling', 'rejected')
REVIEW_REFUND_STATUSES = ('pending', 'unknown', 'manual_review', 'failed')


def delivery_issues(supplier_code, account, destination, node):
    """Conservative configuration preflight, never live supplier verification."""
    issues = []
    if not account.enabled:
        issues.append('account_disabled')
    if not account.read_enabled:
        issues.append('search_disabled')
    if not destination.enabled:
        issues.append('destination_disabled')
    if not destination.verified:
        issues.append('destination_unverified')
    if not node.active:
        issues.append('node_inactive')
    if node.merchant_id != account.merchant_id:
        issues.append('merchant_mismatch')
    try:
        validate_receiving_contact(node.receiving_contact_name, node.receiving_contact_phone, node.receiving_contact_comment)
    except ReceivingContactError:
        issues.append('receiving_contact_invalid')
    if not (destination.external_address_id or '').strip():
        issues.append('supplier_address_missing')
    if not (destination.external_delivery_id or '').strip():
        issues.append('supplier_delivery_missing')
    # Supplier-wide courier-only policy. Numeric ROSSKO route is known; other
    # suppliers need a contract-verified courier route rather than guessed IDs.
    if supplier_code == 'rossko' and destination.external_delivery_id != '000000002':
        issues.append('rossko_courier_required')
    return issues


@router.get('/configuration')
async def configuration():
    s = get_settings()
    return {
        'environment': s.app_env,
        'supplier_api_enabled': s.supplier_api_enabled,
        'supplier_procurement_enabled': s.supplier_procurement_enabled,
        'real_supplier_checkout_enabled': s.real_supplier_checkout_enabled,
        'sbp_credentials_configured': bool(s.yookassa_shop_id and s.yookassa_secret_key),
        'financial_operations_verified': False,  # not inferred from credentials
        'warning': 'Configuration flags are not permission to send payments or supplier orders.',
    }


@router.get('/receiving-readiness')
async def receiving_readiness(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(SupplierDestination, SupplierAccount, FulfillmentNode, Supplier.code)
        .join(SupplierAccount, SupplierDestination.account_id == SupplierAccount.id)
        .join(FulfillmentNode, SupplierDestination.node_id == FulfillmentNode.id)
        .join(Supplier, SupplierAccount.supplier_id == Supplier.id)
        .order_by(Supplier.code, SupplierAccount.label, FulfillmentNode.city, FulfillmentNode.name)
        .limit(1000)
    )).all()
    items = []
    for dest, account, node, supplier_code in rows:
        issues = delivery_issues(supplier_code, account, dest, node)
        items.append({
            'destination_id': str(dest.id), 'account_id': str(account.id),
            'node_id': str(node.id), 'account': account.label, 'supplier': supplier_code,
            'node': node.name, 'city': node.city,
            'courier_delivery_id': dest.external_delivery_id,
            'contact_configured': 'receiving_contact_invalid' not in issues,
            'requisite_bound': bool(dest.external_requisite_id),
            'requisite_can_be_selected_from_live_api': supplier_code == 'rossko' and not dest.external_requisite_id,
            'status': 'configuration_ready' if not issues else 'action_required',
            'issues': issues,
        })
    unmapped = (await db.execute(
        select(SupplierAccount.id, SupplierAccount.label, Supplier.code)
        .join(Supplier, SupplierAccount.supplier_id == Supplier.id)
        .outerjoin(SupplierDestination, SupplierDestination.account_id == SupplierAccount.id)
        .where(SupplierDestination.id.is_(None))
        .order_by(Supplier.code, SupplierAccount.label).limit(500)
    )).all()
    return {
        'unmapped_accounts': [{'id': str(aid), 'label': label, 'supplier': code}
                              for aid, label, code in unmapped],
        'unmapped_truncated': len(unmapped) == 500,
        'items': items, 'total_shown': len(items),
        'ready': sum(not item['issues'] for item in items),
        'action_required': sum(bool(item['issues']) for item in items),
        'truncated': len(items) == 1000,
        'note': 'Configuration readiness is not proof of live delivery, pricing or supplier acceptance.',
    }


@router.get('/attention')
async def attention(db: AsyncSession = Depends(get_session)):
    async def grouped(model, col):
        return {str(key): int(count) for key, count in
                (await db.execute(select(col, func.count()).select_from(model).group_by(col))).all()}

    order_counts = await grouped(Order, Order.status)
    job_counts = await grouped(Job, Job.status)
    purchase_counts = await grouped(SupplierPurchaseAttempt, SupplierPurchaseAttempt.status)
    refund_counts = await grouped(Refund, Refund.status)
    # A procurement without verified supplier order may need operator review.
    internal_procurement_counts = await grouped(Procurement, Procurement.status)
    pending_jobs = int((await db.scalar(select(func.count()).select_from(Job).where(
        Job.status.in_(['pending', 'retry', 'queued']), Job.due_at < datetime.now(timezone.utc)
    ))) or 0)
    return {
        'orders': order_counts, 'jobs': job_counts, 'supplier_purchase_attempts': purchase_counts,
        'procurements': internal_procurement_counts, 'refunds': refund_counts,
        'review_orders': sum(order_counts.get(k, 0) for k in REVIEW_ORDER_STATUSES),
        'review_purchases': sum(purchase_counts.get(k, 0) for k in REVIEW_PURCHASE_STATUSES),
        'review_refunds': sum(refund_counts.get(k, 0) for k in REVIEW_REFUND_STATUSES),
        'failed_jobs': sum(job_counts.get(k, 0) for k in ('failed', 'error')),
        'overdue_queued_jobs': pending_jobs,
        'note': 'Operational counters are snapshots, not instructions to retry external writes.',
    }


@router.get('/orders')
async def monitored_orders(
    status: str = Query('', max_length=40),
    page: int = Query(1, ge=1, le=10000),
    size: int = Query(30, ge=1, le=100),
    review_only: bool = False,
    db: AsyncSession = Depends(get_session),
):
    query = select(Order)
    if review_only:
        query = query.where(Order.status.in_(REVIEW_ORDER_STATUSES))
    elif status:
        query = query.where(Order.status == status)
    total = int((await db.scalar(select(func.count()).select_from(query.subquery()))) or 0)
    orders = (await db.scalars(query.order_by(Order.created_at.desc(), Order.id.desc())
                               .offset((page-1) * size).limit(size))).all()
    return {
        'total': total, 'page': page, 'size': size,
        'items': [{'id': str(o.id), 'status': o.status,
                   'total': str(o.total), 'total_is_final': o.total_is_final,
                   'created_at': o.created_at.isoformat()} for o in orders],
    }


@router.get('/orders/{order_id}/events')
async def order_events(order_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(404, 'Order not found')
    events = (await db.scalars(select(OrderWorkflowEvent)
                              .where(OrderWorkflowEvent.order_id == order_id)
                              .order_by(OrderWorkflowEvent.created_at.desc(), OrderWorkflowEvent.id.desc())
                              .limit(100))).all()
    # Allowlist: do not expose customer identity or free-form evidence/PII.
    return {'order_id': str(order_id), 'events': [
        {'id': str(event.id), 'type': event.event_type,
         'actor': event.actor_type, 'created_at': event.created_at.isoformat(),
         'line_id': str((event.evidence or {}).get('line_id'))
         if (event.evidence or {}).get('line_id') else None}
        for event in events
    ], 'limit': 100, 'evidence_redacted': True}


@router.get('/purchases')
async def monitored_purchases(
    status: str = Query('', max_length=32),
    order_id: uuid.UUID | None = Query(default=None),
    page: int = Query(1, ge=1, le=10000),
    size: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
):
    query = select(SupplierPurchaseAttempt)
    if order_id is not None:
        query = query.where(SupplierPurchaseAttempt.buyer_order_id == order_id)
    if status:
        query = query.where(SupplierPurchaseAttempt.status == status)
    total = int((await db.scalar(select(func.count()).select_from(query.subquery()))) or 0)
    rows = (await db.scalars(query.order_by(SupplierPurchaseAttempt.created_at.desc(),
                                            SupplierPurchaseAttempt.id.desc())
                             .offset((page-1)*size).limit(size))).all()
    return {'total': total, 'page': page, 'size': size, 'items': [{
        'id': str(a.id), 'order_id': str(a.buyer_order_id) if a.buyer_order_id else None,
        'order_line_id': str(a.order_line_id) if a.order_line_id else None,
        'account_id': str(a.supplier_account_id),
        'destination_id': str(a.supplier_destination_id),
        'status': a.status, 'remote_order_ids': a.remote_order_ids,
        'error_code': a.error_code, 'created_at': a.created_at.isoformat(),
    } for a in rows]}


@router.get('/purchases/{attempt_id}')
async def monitored_purchase_detail(attempt_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Exact purchase attempt diagnostic. Never return buyer or receiving contacts,
    payment credentials, secret requisites, or raw supplier responses.
    No supplier API requests or procurement state changes are made here.
    """
    attempt = await db.get(SupplierPurchaseAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, 'Purchase attempt not found')
    return {
        'id': str(attempt.id),
        'order_id': str(attempt.buyer_order_id) if attempt.buyer_order_id else None,
        'order_line_id': str(attempt.order_line_id) if attempt.order_line_id else None,
        'account_id': str(attempt.supplier_account_id),
        'destination_id': str(attempt.supplier_destination_id),
        'status': attempt.status,
        'remote_order_ids': attempt.remote_order_ids or [],
        'error_code': attempt.error_code,
        'quantity': attempt.quantity,
        'created_at': attempt.created_at.isoformat(),
        'sent_at': attempt.sent_at.isoformat() if attempt.sent_at else None,
        'completed_at': attempt.completed_at.isoformat() if attempt.completed_at else None,
        'manual_reconciliation_required': attempt.status in (
            'dispatch_unknown', 'manual_review', 'rejected', 'accepted_unverified',
            'accepted', 'reconciling',
        ),
        'verified': attempt.verified_at is not None,
        'last_reconciled_at': attempt.last_reconciled_at.isoformat() if attempt.last_reconciled_at else None,
        'cancellation_requested': attempt.cancellation_requested_at is not None,
        'advisory': 'Recorded status is not live supplier confirmation. Do not retry unknown outcomes.',
    }


@router.get('/jobs')
async def monitored_jobs(
    status: str = Query('', max_length=30),
    page: int = Query(1, ge=1, le=10000),
    size: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_session),
):
    query = select(Job)
    if status:
        query = query.where(Job.status == status)
    total = int((await db.scalar(select(func.count()).select_from(query.subquery()))) or 0)
    rows = (await db.scalars(query.order_by(Job.due_at.desc(), Job.id.desc())
                             .offset((page-1)*size).limit(size))).all()
    return {'total': total, 'page': page, 'size': size, 'items': [{
        'id': str(j.id), 'kind': j.kind, 'status': j.status, 'attempts': j.attempts,
        'due_at': j.due_at.isoformat(), 'error_present': bool(j.last_error),
    } for j in rows]}
