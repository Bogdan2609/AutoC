"""Operator price proposal and explicit customer consent for v0.17 requests.

This is NOT a payment authorization or a supplier reservation. A proposal can be
agreed with a customer, but remains blocked from SBP until the separate live
price/fulfilment/fiscalization contract is implemented and verified.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.commerce.idempotency import payload_hash
from app.modules.commerce.models import Order, OrderLine, Procurement, OrderWorkflowEvent
from app.modules.marketplace.models import SellerOrder
from app.modules.payments.models import Payment

admin = APIRouter(prefix='/api/v1/admin/marketplace/order-requests',
                  tags=['operator-quotes'], dependencies=[Depends(require_admin)])
buyer = APIRouter(prefix='/api/v1/marketplace/order-requests', tags=['operator-quotes'])
CENT = Decimal('0.01')


def now():
    return datetime.now(timezone.utc)


def money(value):
    return Decimal(str(value)).quantize(CENT)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def refuse(message):
    raise HTTPException(409, message)


class QuoteLine(BaseModel):
    line_id: uuid.UUID
    unit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    # Delivery charge is per order line (not per item); shipping still cannot
    # be charged before its fiscal tax/payment contract is verified.
    delivery_fee: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    cost_evidence: str = Field(min_length=8, max_length=200)


class OfferQuote(BaseModel):
    lines: list[QuoteLine] = Field(min_length=1, max_length=100)
    valid_for_minutes: int = Field(ge=5, le=120)
    operator_reference: str = Field(min_length=8, max_length=200)
    currency: Literal['RUB'] = 'RUB'


class BuyerDecision(BaseModel):
    revision: int = Field(ge=1)
    quote_digest: str = Field(min_length=64, max_length=64, pattern='^[a-f0-9]{64}$')
    decision: Literal['accept', 'decline']


async def assert_nonbinding(db, order):
    if (order.request_details or {}).get('workflow') != 'v017' or order.total_is_final:
        refuse('Only provisional operator requests can be quoted')
    if await db.scalar(select(Payment.id).where(Payment.order_id == order.id).limit(1)):
        refuse('Payment exists; manual financial reconciliation required')
    if await db.scalar(select(Procurement.id).join(OrderLine,
            Procurement.order_line_id == OrderLine.id).where(OrderLine.order_id == order.id).limit(1)):
        refuse('Supplier purchase exists; manual reconciliation required')


async def order_lines(db, order):
    return (await db.scalars(select(OrderLine).where(OrderLine.order_id == order.id)
                              .order_by(OrderLine.id))).all()


def quote_view(order):
    proposal = (order.request_details or {}).get('operator_quote')
    if not proposal:
        return {'order_id': str(order.id), 'status': order.status,
                'quote': None, 'payment_enabled': False, 'procurement_enabled': False}
    return {'order_id': str(order.id), 'status': order.status,
            'quote': {'revision': proposal['revision'], 'digest': proposal['digest'],
                      'expires_at': proposal['expires_at'], 'currency': 'RUB',
                      'total': proposal['total'], 'goods_total': proposal['goods_total'],
                      'delivery_total': proposal['delivery_total'],
                      'lines': [{k: v for k, v in line.items() if k != 'cost_evidence'}
                                for line in proposal['lines']],
                      'buyer_accepted_at': proposal.get('buyer_accepted_at'),
                      'nonbinding': True},
            'payment_enabled': False, 'procurement_enabled': False}


@admin.post('/{order_id}/quote')
async def offer(order_id: uuid.UUID, body: OfferQuote, db: AsyncSession = Depends(get_session)):
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order:
        raise HTTPException(404, 'Order request not found')
    await assert_nonbinding(db, order)
    if order.status not in ('request_awaiting_delivery_quote',
                            'request_awaiting_operator_review',
                            'request_quote_offered', 'request_quote_declined'):
        refuse('Cannot quote this request in its current state')
    lines = await order_lines(db, order)
    indexed = {line.id: line for line in lines}
    submitted = {part.line_id: part for part in body.lines}
    if len(submitted) != len(body.lines) or set(submitted) != set(indexed):
        refuse('Quote must cover every order line exactly once')
    price_rows = []
    goods_total = Decimal('0.00')
    delivery_total = Decimal('0.00')
    for line in lines:
        selected = submitted[line.id]
        if not line.commercial_snapshot:
            refuse('Missing immutable commercial selection')
        snap = line.commercial_snapshot
        if snap.get('source_kind') not in ('own_stock', 'supplier_quote'):
            refuse('Unsupported commercial source')
        if line.quantity <= 0 or money(selected.unit_price) < money(line.unit_purchase):
            refuse('Quantity invalid or retail price below recorded purchase cost')
        # Reject proposals that claim an active stock reservation or supplier
        # purchase: neither is granted merely by an operator proposal.
        if snap.get('reservation_key'):
            refuse('Legacy reserved request requires separate review')
        goods_total += money(selected.unit_price) * line.quantity
        delivery_total += money(selected.delivery_fee)
        price_rows.append({'line_id': str(line.id), 'quantity': line.quantity,
                           'title': line.title, 'unit_price': str(money(selected.unit_price)),
                           'delivery_fee': str(money(selected.delivery_fee)),
                           'source_kind': snap['source_kind'],
                           'cost_evidence': selected.cost_evidence})
    grand_total = money(goods_total + delivery_total)
    if grand_total <= 0:
        refuse('Quote total must be positive')
    digest = payload_hash({'order_id': str(order.id), 'operator_reference': body.operator_reference,
                           'positions': price_rows, 'total': str(grand_total)})
    previous = (order.request_details or {}).get('operator_quote') or {}
    if order.status == 'request_quote_offered' and previous.get('digest') == digest:
        # Exact retry is read-only: cannot extend expiry without a new quote.
        return quote_view(order)
    revision = int(previous.get('revision', 0)) + 1
    data = dict(order.request_details or {})
    data['operator_quote'] = {
        'revision': revision, 'digest': digest, 'operator_reference': body.operator_reference,
        'goods_total': str(money(goods_total)), 'delivery_total': str(money(delivery_total)),
        'total': str(grand_total), 'currency': 'RUB', 'lines': price_rows,
        'created_at': now().isoformat(),
        'expires_at': (now() + timedelta(minutes=body.valid_for_minutes)).isoformat(),
        'buyer_accepted_at': None,
        'payment_contract': 'not_verified', 'supplier_reservation': False}
    order.request_details = data
    order.status = 'request_quote_offered'
    db.add(OrderWorkflowEvent(order_id=order.id, event_type='operator_quote_offered',
        actor_type='admin', evidence={'revision':revision,'quote_digest':digest,
        'total':str(grand_total),'expires_at':data['operator_quote']['expires_at']}))
    sellers = (await db.scalars(select(SellerOrder).where(
        SellerOrder.customer_order_id == order.id))).all()
    for seller in sellers:
        seller.status = 'request_quote_offered'
    await db.commit()
    return quote_view(order)


@buyer.get('/{order_id}/quote')
async def get_quote(order_id: uuid.UUID, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_session)):
    order = await db.get(Order, order_id)
    if not order or order.user_id != user.id or (order.request_details or {}).get('workflow') != 'v017':
        raise HTTPException(404, 'Order request not found')
    return quote_view(order)


@buyer.post('/{order_id}/decision')
async def decision(order_id: uuid.UUID, body: BuyerDecision,
                   user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order or order.user_id != user.id or (order.request_details or {}).get('workflow') != 'v017':
        raise HTTPException(404, 'Order request not found')
    await assert_nonbinding(db, order)
    if order.status != 'request_quote_offered':
        refuse('Quote is not awaiting customer decision')
    details = dict(order.request_details or {})
    proposal = dict(details.get('operator_quote') or {})
    if (proposal.get('revision') != body.revision or
            proposal.get('digest') != body.quote_digest):
        refuse('Quote was changed; refresh and make a new decision')
    if not proposal.get('expires_at') or aware(datetime.fromisoformat(proposal['expires_at'])) <= now():
        refuse('Quote has expired; operator must prepare a fresh proposal')
    if body.decision == 'accept':
        order.status = 'request_quote_accepted'
        proposal['buyer_accepted_at'] = now().isoformat()
    else:
        order.status = 'request_quote_declined'
    details['operator_quote'] = proposal
    order.request_details = details
    db.add(OrderWorkflowEvent(order_id=order.id,
        event_type='customer_quote_accepted' if body.decision=='accept' else 'customer_quote_declined',
        actor_type='customer', evidence={'revision':body.revision,'quote_digest':body.quote_digest}))
    sellers = (await db.scalars(select(SellerOrder).where(
        SellerOrder.customer_order_id == order.id))).all()
    for seller in sellers:
        seller.status = order.status
    await db.commit()
    return quote_view(order)


@admin.get('/{order_id}/audit')
async def audit_events(order_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    order = await db.get(Order, order_id)
    if not order or (order.request_details or {}).get('workflow') != 'v017':
        raise HTTPException(404, 'Order request not found')
    events = (await db.scalars(select(OrderWorkflowEvent).where(
        OrderWorkflowEvent.order_id == order.id).order_by(OrderWorkflowEvent.created_at,
        OrderWorkflowEvent.id))).all()
    return [{'type': x.event_type, 'actor': x.actor_type, 'created_at': x.created_at.isoformat(),
             'evidence': x.evidence} for x in events]
