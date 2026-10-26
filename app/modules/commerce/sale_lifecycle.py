"""Single local state authority for paid orders, fulfilment and customer delivery.

This module never calls a bank, supplier or courier. An acknowledgement from
any of those systems must be verified by its dedicated adapter first; only
verified evidence may advance the state. External writes are NEVER retried here.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, get_session
from app.core.security import require_admin
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.commerce.models import Order, OrderLine, Procurement, Job, OrderWorkflowEvent
from app.modules.payments.models import Payment, Refund
from app.modules.marketplace.models import InventoryBalance, InventoryMovement, SellerOffer, FulfillmentNode


class SaleLineExecution(Base):
    """Exactly one fulfilment record per order line, tied to paid order evidence."""
    __tablename__ = 'sale_line_executions'
    __table_args__ = (
        UniqueConstraint('order_line_id', name='uq_sale_execution_order_line'),
        Index('ix_sale_execution_status', 'status'),
        CheckConstraint("source_kind IN ('supplier','own_stock')", name='sale_execution_source'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('order_lines.id', ondelete='RESTRICT'), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default='payment_verified')
    fulfillment_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operator_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


EXECUTION_FLOW = {
    'supplier': {
        'payment_verified': {'procurement_pending'},
        'procurement_pending': {'supplier_confirmed', 'manual_review', 'supplier_rejected'},
        'manual_review': {'supplier_confirmed', 'supplier_rejected'},
        'supplier_confirmed': {'fulfillment_ready'},
        'supplier_rejected': {'refund_pending'},
        'fulfillment_ready': {'dispatched'},
        'dispatched': {'delivered'},
    },
    'own_stock': {
        'payment_verified': {'inventory_review', 'inventory_reserved'},
        'inventory_review': {'inventory_reserved', 'refund_pending'},
        'inventory_reserved': {'fulfillment_ready'},
        'fulfillment_ready': {'dispatched'},
        'dispatched': {'delivered'},
    },
}


def permitted_transition(source: str, old: str, new: str) -> bool:
    return new in EXECUTION_FLOW.get(source, {}).get(old, set())


def now():
    return datetime.now(timezone.utc)


async def verified_paid_order(db, order: Order) -> Payment:
    """Payment status is local evidence, not a replacement for provider verification.

    The only writer allowed to mark Payment.succeeded is the provider
    re-query handler; always apply this check before fulfilment transitions.
    """
    if (not order or not order.total_is_final or order.total <= 0 or
            (order.request_details or {}).get('workflow') != 'sbp_checkout'):
        raise HTTPException(409, 'Only a verified final SBP order can be fulfilled')
    payments = (await db.scalars(select(Payment).where(
        Payment.order_id == order.id, Payment.status == 'succeeded'))).all()
    if len(payments) != 1 or payments[0].amount != order.total or not payments[0].provider_id:
        raise HTTPException(409, 'Exactly one matching settled SBP payment required')
    return payments[0]


async def record_paid_fulfillment(db, order: Order, lines: list[OrderLine]):
    """Called under a payment row lock only after provider-confirmed success.

    All writes are local database inserts. Supplier procurement worker remains
    manual-review only until external account-specific write contracts pass QA.
    """
    if not lines or any(line.order_id != order.id or line.quantity < 1 for line in lines):
        raise HTTPException(409, 'Incomplete order lines')
    if sum((Decimal(str(x.unit_retail)) * x.quantity for x in lines), Decimal('0')) != order.total:
        # Legacy test-only checkout does not have shipping; final money must match exactly.
        raise HTTPException(409, 'Frozen line amounts do not match the payment')
    for line in lines:
        existing = await db.scalar(select(SaleLineExecution).where(
            SaleLineExecution.order_line_id == line.id))
        if existing:
            continue
        snapshot = line.commercial_snapshot or {}
        source = 'own_stock' if snapshot.get('source_kind') == 'own_stock' else 'supplier'
        execution = SaleLineExecution(order_line_id=line.id, source_kind=source,
                                      status='payment_verified')
        db.add(execution)
        await db.flush()
        if source == 'supplier':
            execution.status = 'procurement_pending'
            proc = await db.scalar(select(Procurement).where(Procurement.order_line_id == line.id))
            if proc is None:
                proc = Procurement(order_line_id=line.id, supplier_code=line.supplier_code)
                db.add(proc)
                await db.flush()
            job = await db.scalar(select(Job).where(Job.dedup_key == f'procurement:{proc.id}'))
            if job is None:
                db.add(Job(dedup_key=f'procurement:{proc.id}', kind='procurement',
                           payload={'id': str(proc.id)}))
            line.status = 'procurement_pending'
        else:
            # Never reserve buyer's stock in a transaction unless source-level
            # inventory and merchant bindings have been revalidated. That belongs
            # to a separate inventory settlement handler, not the payment webhook.
            execution.status = 'inventory_review'
            line.status = 'inventory_review'
        db.add(OrderWorkflowEvent(order_id=order.id, event_type='paid_line_recorded',
            actor_type='system', evidence={'line_id': str(line.id), 'source_kind': source,
                                            'execution_id': str(execution.id)}))


async def summarize_order(db, order):
    lines = (await db.scalars(select(OrderLine).where(OrderLine.order_id == order.id)
                              .order_by(OrderLine.id))).all()
    executions = (await db.scalars(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id.in_([l.id for l in lines])))).all() if lines else []
    by_line = {execution.order_line_id: execution for execution in executions}
    quote = (order.request_details or {}).get('operator_quote')
    paid = await db.scalar(select(Payment.id).where(Payment.order_id == order.id,
                                                   Payment.status == 'succeeded'))
    tasks = [
        {'line_id': str(line.id), 'title': line.title, 'quantity': line.quantity,
         'source_kind': by_line[line.id].source_kind if line.id in by_line else None,
         'status': by_line[line.id].status if line.id in by_line else line.status,
         'fulfillment_reference': by_line[line.id].fulfillment_reference if line.id in by_line else None}
        for line in lines
    ]
    return {'order_id': str(order.id), 'workflow': (order.request_details or {}).get('workflow'),
            'status': order.status, 'total': str(order.total) if order.total_is_final else None,
            'provisional_amount': str(order.total) if not order.total_is_final else None,
            'operator_quote_total': quote.get('total') if quote else None,
            'payment_verified': bool(paid), 'line_count': len(lines),
            'all_delivered': bool(lines and len(executions) == len(lines) and
                                  all(e.status == 'delivered' for e in executions)),
            'lines': tasks}


async def reconcile_order(db, order: Order):
    """Derive order status from completed execution rows, never from operator intent."""
    lines = (await db.scalars(select(OrderLine.id).where(OrderLine.order_id == order.id))).all()
    if not lines:
        return
    executions = (await db.scalars(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id.in_(lines)))).all()
    if len(executions) != len(lines):
        return
    states = {x.status for x in executions}
    if states == {'delivered'}:
        order.status = 'fulfilled'
    elif 'refund_pending' in states or 'supplier_rejected' in states:
        order.status = 'fulfillment_exception'
    elif 'manual_review' in states or 'inventory_review' in states:
        order.status = 'fulfillment_review'
    elif states.intersection({'supplier_confirmed', 'fulfillment_ready', 'dispatched'}):
        order.status = 'fulfillment_in_progress'
    # Do not overwrite paid_procurement_pending while supplier work is unconfirmed.


admin = APIRouter(prefix='/api/v1/admin/sales', tags=['sales-lifecycle'],
                  dependencies=[Depends(require_admin)])
buyer = APIRouter(prefix='/api/v1/sales', tags=['sales-lifecycle'])


class ReconcileLine(BaseModel):
    target: Literal['supplier_confirmed', 'supplier_rejected', 'fulfillment_ready', 'dispatched', 'delivered', 'refund_pending']
    evidence: str = Field(min_length=12, max_length=500)
    reference: str | None = Field(default=None, max_length=200)


@buyer.get('/orders/{order_id}')
async def buyer_sale(order_id: uuid.UUID, user: User = Depends(current_user), db=Depends(get_session)):
    order = await db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, 'Order not found')
    return await summarize_order(db, order)


@admin.get('/orders/{order_id}')
async def operator_sale(order_id: uuid.UUID, db=Depends(get_session)):
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(404, 'Order not found')
    return await summarize_order(db, order)



@admin.post('/orders/{order_id}/lines/{line_id}/reserve-own-stock')
async def reserve_own_stock(order_id: uuid.UUID, line_id: uuid.UUID, db=Depends(get_session)):
    """Atomically reserve only post-payment, at the immutable selected seller node."""
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    line = await db.get(OrderLine, line_id)
    if not order or not line or line.order_id != order.id:
        raise HTTPException(404, 'Order line not found')
    await verified_paid_order(db, order)
    execution = await db.scalar(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id == line.id).with_for_update())
    if not execution or execution.source_kind != 'own_stock':
        raise HTTPException(409, 'Selected line is not own-stock')
    if execution.status == 'inventory_reserved':
        return {'status': execution.status, 'reused': True}
    if execution.status != 'inventory_review':
        raise HTTPException(409, 'Own stock is not reservable in this state')
    snap = line.commercial_snapshot or {}
    try:
        balance_id = uuid.UUID(snap['inventory_balance_id'])
        node_id = uuid.UUID(snap['origin_node_id'])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(409, 'Immutable inventory source missing') from exc
    balance = await db.get(InventoryBalance, balance_id)
    offer = await db.get(SellerOffer, line.seller_offer_id) if line.seller_offer_id else None
    node = await db.get(FulfillmentNode, node_id)
    if (not balance or not offer or not node or not node.active or
            balance.seller_offer_id != offer.id or balance.node_id != node.id or
            offer.merchant_id != line.merchant_id or node.merchant_id != line.merchant_id or
            offer.source_kind != 'own_stock' or offer.status != 'active' or
            snap.get('source_kind') != 'own_stock' or line.quantity < 1):
        raise HTTPException(409, 'Seller, node or inventory source no longer matches')
    updated = await db.scalar(update(InventoryBalance).where(
        InventoryBalance.id == balance_id,
        InventoryBalance.on_hand - InventoryBalance.reserved >= line.quantity)
        .values(reserved=InventoryBalance.reserved + line.quantity)
        .returning(InventoryBalance.id))
    if not updated:
        raise HTTPException(409, 'Insufficient unreserved seller stock; refund or substitution review needed')
    db.add(InventoryMovement(balance_id=balance_id, delta_reserved=line.quantity,
        idempotency_key=f'sale-reserve:{line.id}', reason='paid_sale_reserved'))
    execution.status = 'inventory_reserved'
    line.status = 'inventory_reserved'
    execution.updated_at = now()
    db.add(OrderWorkflowEvent(order_id=order.id, event_type='own_stock_reserved',
        actor_type='admin', evidence={'line_id': str(line.id), 'balance_id': str(balance_id),
                                       'quantity': line.quantity}))
    await reconcile_order(db, order)
    await db.commit()
    return {'status': 'inventory_reserved', 'reused': False}


def frozen_balance_id(line):
    try:
        return uuid.UUID((line.commercial_snapshot or {})['inventory_balance_id'])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(409, 'Original inventory balance evidence missing') from exc


async def consume_reserved_stock(db, line):
    """Atomic reserved-to-shipped transfer; never consume more than reserved."""
    balance_id = frozen_balance_id(line)
    updated = await db.scalar(update(InventoryBalance).where(
        InventoryBalance.id == balance_id,
        InventoryBalance.reserved >= line.quantity,
        InventoryBalance.on_hand >= line.quantity)
        .values(on_hand=InventoryBalance.on_hand - line.quantity,
                reserved=InventoryBalance.reserved - line.quantity)
        .returning(InventoryBalance.id))
    if not updated:
        raise HTTPException(409, 'Reserved stock cannot be reconciled for shipment')
    db.add(InventoryMovement(balance_id=balance_id, delta_on_hand=-line.quantity,
        delta_reserved=-line.quantity, idempotency_key=f'sale-ship:{line.id}',
        reason='paid_sale_dispatched'))

@admin.post('/orders/{order_id}/lines/{line_id}/reconcile')
async def operator_transition(order_id: uuid.UUID, line_id: uuid.UUID, body: ReconcileLine,
                              db=Depends(get_session)):
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    line = await db.get(OrderLine, line_id)
    if not order or not line or line.order_id != order.id:
        raise HTTPException(404, 'Order line not found')
    await verified_paid_order(db, order)
    execution = await db.scalar(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id == line.id).with_for_update())
    if not execution:
        raise HTTPException(409, 'Paid order line has not been initialized')
    if execution.status == body.target:
        if execution.operator_evidence == body.evidence and execution.fulfillment_reference == body.reference:
            return {'status': execution.status, 'reused': True}
        raise HTTPException(409, 'Conflicting duplicate reconciliation evidence')
    if not permitted_transition(execution.source_kind, execution.status, body.target):
        raise HTTPException(409, 'Invalid fulfillment state transition')
    if body.target in ('supplier_confirmed', 'dispatched', 'delivered') and not body.reference:
        raise HTTPException(409, 'Verified external reference required for this transition')
    if execution.source_kind == 'own_stock' and body.target == 'dispatched':
        await consume_reserved_stock(db, line)
    if body.target == 'supplier_confirmed':
        proc = await db.scalar(select(Procurement).where(
            Procurement.order_line_id == line.id).with_for_update())
        if not proc or proc.status not in ('manual_review', 'pending', 'accepted'):
            raise HTTPException(409, 'Supplier procurement must exist and be reviewed')
        # Reconciliation requires human-supplied supplier evidence; this does not
        # claim that the supplier API verified it automatically.
        proc.status = 'accepted'
        proc.supplier_order_id = body.reference
        proc.error = None
    if body.target == 'supplier_rejected':
        proc = await db.scalar(select(Procurement).where(
            Procurement.order_line_id == line.id).with_for_update())
        if not proc:
            raise HTTPException(409, 'Supplier procurement absent')
        proc.status = 'rejected'
    if body.target == 'refund_pending':
        from app.modules.commerce.models import Replacement
        linked_refund = await db.scalar(select(Refund.id).join(Replacement,
            Refund.replacement_id == Replacement.id).where(
            Replacement.order_line_id == line.id, Refund.status != 'canceled'))
        if not linked_refund:
            raise HTTPException(409, 'No reserved refund for this line; obtain customer decision first')
    execution.status = body.target
    execution.operator_evidence = body.evidence
    execution.fulfillment_reference = body.reference
    execution.updated_at = now()
    line.status = body.target
    db.add(OrderWorkflowEvent(order_id=order.id,
        event_type='fulfillment_' + body.target, actor_type='admin',
        evidence={'line_id': str(line.id), 'execution_id': str(execution.id),
                  'reference': body.reference, 'evidence': body.evidence}))
    await reconcile_order(db, order)
    await db.commit()
    return {'line_id': str(line.id), 'status': body.target, 'order_status': order.status,
            'reused': False, 'external_side_effect': False}
