"""Admin-only read-back reconciliation and cancellation review for supplier orders.

No endpoint in this module sends a supplier purchase or cancellation. A write
that timed out is never retried; missing identifiers require supplier support.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Order, OrderLine, Procurement, OrderWorkflowEvent
from app.modules.commerce.sale_lifecycle import SaleLineExecution, reconcile_order, verified_paid_order
from app.modules.marketplace.account_credentials import for_account, ready
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.marketplace.purchase_lifecycle import (
    capability_list, read_rossko_orders, verify_rossko_orders,
)
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot


router = APIRouter(prefix='/api/v1/admin/supplier-purchases', tags=['supplier-purchase-lifecycle'],
                   dependencies=[Depends(require_admin)])


def utcnow():
    return datetime.now(timezone.utc)


@router.get('/capabilities')
async def supplier_capabilities():
    return {'suppliers': [asdict(x) for x in capability_list()],
            'supplier_writes_triggered': False}


@router.get('/{attempt_id}')
async def purchase_detail(attempt_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    attempt = await db.get(SupplierPurchaseAttempt, attempt_id)
    if not attempt:
        raise HTTPException(404, 'Purchase attempt not found')
    return {'id': str(attempt.id), 'status': attempt.status,
            'order_line_id': str(attempt.order_line_id) if attempt.order_line_id else None,
            'order_id': str(attempt.buyer_order_id) if attempt.buyer_order_id else None,
            'remote_order_ids': attempt.remote_order_ids or [],
            'error_code': attempt.error_code, 'verified': attempt.verified_at is not None,
            'last_reconciled_at': attempt.last_reconciled_at,
            'cancellation_requested': attempt.cancellation_requested_at is not None,
            'manual_cancellation_only': True}


async def _reconcile_context(db, attempt):
    """Pin foreign keys to the immutable paid line and exact supplier account."""
    line = await db.get(OrderLine, attempt.order_line_id) if attempt.order_line_id else None
    order = await db.get(Order, line.order_id) if line else None
    account = await db.get(SupplierAccount, attempt.supplier_account_id)
    dest = await db.get(SupplierDestination, attempt.supplier_destination_id)
    supplier = await db.get(Supplier, account.supplier_id) if account else None
    snapshot = await db.get(OfferSnapshot, attempt.snapshot_id)
    item = await db.get(SupplierItem, snapshot.supplier_item_id) if snapshot else None
    if (not all((line, order, account, dest, supplier, snapshot, item)) or
            supplier.code != 'rossko' or dest.account_id != account.id or
            snapshot.supplier_account_id != account.id or
            snapshot.supplier_destination_id != dest.id or
            line.snapshot_id != snapshot.id or line.supplier_item_id != item.id or
            line.order_id != attempt.buyer_order_id or line.quantity != attempt.quantity or
            line.merchant_id != account.merchant_id or
            line.fulfillment_node_id != dest.node_id or
            item.supplier_id != supplier.id):
        raise HTTPException(409, 'Supplier order is not bound to the paid buyer selection')
    await verified_paid_order(db, order)
    return order, line, account, dest, item


@router.post('/{attempt_id}/reconcile')
async def reconcile_supplier_purchase(attempt_id: uuid.UUID,
                                     db: AsyncSession = Depends(get_session)):
    """One account-scoped read-only GetOrders; no re-submit after ambiguous checkout."""
    if not get_settings().supplier_api_enabled:
        raise HTTPException(403, 'Supplier API reads are disabled')
    attempt = await db.scalar(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.id == attempt_id).with_for_update())
    if not attempt:
        raise HTTPException(404, 'Purchase attempt not found')
    if attempt.status == 'verified':
        return {'id': str(attempt.id), 'status': 'verified', 'reused': True,
                'supplier_write': False}
    allowed = {'accepted', 'accepted_unverified', 'dispatch_unknown', 'manual_review', 'reconciling'}
    if attempt.status not in allowed:
        raise HTTPException(409, 'Attempt is not eligible for supplier reconciliation')
    if attempt.status == 'dispatch_unknown' and attempt.sent_at:
        sent = attempt.sent_at.replace(tzinfo=timezone.utc) if attempt.sent_at.tzinfo is None else attempt.sent_at
        if utcnow() - sent < timedelta(minutes=2):
            raise HTTPException(409, 'Supplier dispatch may still be running; retry READ later')
    if attempt.status == 'reconciling' and attempt.reconcile_started_at:
        started = (attempt.reconcile_started_at.replace(tzinfo=timezone.utc)
                   if attempt.reconcile_started_at.tzinfo is None else attempt.reconcile_started_at)
        if utcnow() - started < timedelta(minutes=10):
            raise HTTPException(409, 'Reconciliation already in progress')
    previous_status = attempt.status
    order, line, account, dest, item = await _reconcile_context(db, attempt)
    # Unknown dispatch with no external ID MUST NOT be interpreted as a rejected
    # order, even if a GetOrders page does not contain a matching SKU.
    ids = attempt.remote_order_ids or []
    if not ids:
        attempt.status = 'manual_review'
        attempt.error_code = 'supplier_order_id_missing'
        attempt.last_reconciled_at = utcnow()
        await db.commit()
        return {'id': str(attempt.id), 'status': 'manual_review',
                'reason': 'supplier_order_id_missing', 'supplier_write': False,
                'support_required': True}
    if not account.read_enabled or not ready('rossko', account, dest):
        raise HTTPException(409, 'Account credentials unavailable for read-only reconciliation')
    from app.modules.offer_engine.discovery import claim_supplier_budget
    if not await claim_supplier_budget('rossko', str(account.id), account.requests_per_hour):
        raise HTTPException(429, 'Supplier read quota unavailable')
    # Fence concurrent reconciliations with a persisted lease token. The lease
    # contains no credentials. Expired leases can be read again safely.
    token = str(uuid.uuid4())
    attempt.status = 'reconciling'
    attempt.reconcile_token = token
    attempt.reconcile_started_at = utcnow()
    await db.commit()
    raw = None
    error = None
    try:
        config = for_account('rossko', account, dest)
        raw = await read_rossko_orders(config)
    except Exception:
        error = 'supplier_lookup_unavailable'  # Never leak SOAP or credential details.
    result = None
    if error is None:
        result = verify_rossko_orders(raw, external_ids=ids, brand=item.raw_brand,
             article=item.raw_article, quantity=attempt.quantity,
             approved_unit_price=attempt.approved_unit_price, buyer_order_id=order.id)
    record = await db.scalar(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.id == attempt_id).with_for_update())
    if not record or record.status != 'reconciling' or record.reconcile_token != token:
        raise HTTPException(409, 'Reconciliation lease changed; result was discarded')
    record.reconcile_token = None
    record.last_reconciled_at = utcnow()
    if error:
        record.status = 'manual_review'
        record.error_code = error
    elif result.result == 'verified' and previous_status in ('accepted', 'accepted_unverified'):
        record.status = 'verified'
        record.verified_at = utcnow()
        record.completed_at = utcnow()
        record.error_code = None
        proc = await db.scalar(select(Procurement).where(
            Procurement.order_line_id == line.id).with_for_update())
        execution = await db.scalar(select(SaleLineExecution).where(
            SaleLineExecution.order_line_id == line.id).with_for_update())
        if not proc or not execution or execution.source_kind != 'supplier':
            await db.rollback()
            raise HTTPException(409, 'Missing paid supplier execution; cannot finalize')
        if execution.status in ('dispatched', 'delivered', 'refund_pending', 'supplier_rejected'):
            await db.rollback()
            raise HTTPException(409, 'Sale line status conflicts with supplier verification')
        proc.status = 'accepted'
        proc.supplier_order_id = ids[0]
        proc.error = None
        execution.status = 'supplier_confirmed'
        execution.fulfillment_reference = ids[0]
        line.status = 'supplier_confirmed'
        db.add(OrderWorkflowEvent(order_id=order.id, event_type='supplier_order_confirmed',
            actor_type='system', evidence={'line_id': str(line.id), 'attempt_id': str(record.id),
                'supplier_order_id': ids[0], 'source': 'rossko_getorders'}))
        await reconcile_order(db, order)
    else:
        # A partial/rejected/ambiguous checkout must NEVER become confirmed
        # solely because some remote ID exists. Human supplier review required.
        record.status = ('accepted_unverified' if result.result == 'pending' and
                         previous_status in ('accepted', 'accepted_unverified')
                         else 'manual_review')
        record.error_code = ('manual_confirmation_required' if result.result == 'verified'
                             else result.reason)
    await db.commit()
    return {'id': str(record.id), 'status': record.status,
            'reason': record.error_code, 'supplier_write': False,
            'support_required': record.status == 'manual_review'}


@router.post('/{attempt_id}/request-cancellation')
async def request_supplier_cancellation(attempt_id: uuid.UUID,
                                        db: AsyncSession = Depends(get_session)):
    """Escalate cancellation; never claim it was accepted by a supplier."""
    attempt = await db.scalar(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.id == attempt_id).with_for_update())
    if not attempt:
        raise HTTPException(404, 'Purchase attempt not found')
    if attempt.status != 'verified' or not attempt.verified_at:
        raise HTTPException(409, 'Only verified supplier orders can request cancellation here')
    if attempt.cancellation_requested_at is not None:
        return {'id': str(attempt.id), 'cancellation_requested': True,
                'reused': True, 'supplier_cancelled': False}
    order, line, *_ = await _reconcile_context(db, attempt)
    execution = await db.scalar(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id == line.id).with_for_update())
    if not execution or execution.status not in ('supplier_confirmed', 'manual_review'):
        raise HTTPException(409, 'Line cannot be considered for cancellation in its current state')
    attempt.cancellation_requested_at = utcnow()
    db.add(OrderWorkflowEvent(order_id=order.id, event_type='supplier_cancellation_requested',
        actor_type='admin', evidence={'attempt_id': str(attempt.id), 'line_id': str(line.id),
                                      'requires_supplier_confirmation': True}))
    await db.commit()
    return {'id': str(attempt.id), 'cancellation_requested': True,
            'reused': False, 'supplier_cancelled': False, 'manual_supplier_action_required': True}
