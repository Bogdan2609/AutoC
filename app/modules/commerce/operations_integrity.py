"""Read-only diagnostics that separate a request, a payment and a real purchase.

Never report a local status, cached supplier offer or operator quote as a
successful settlement or fulfillment. These endpoints do not perform HTTP calls.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Order, OrderLine, Job, Procurement
from app.modules.payments.models import Payment, Refund

router = APIRouter(prefix='/api/v1/admin/commerce', tags=['commerce-integrity'],
                   dependencies=[Depends(require_admin)])


@router.get('/readiness')
async def readiness(db: AsyncSession = Depends(get_session)):
    s = get_settings()
    manual = await db.scalar(select(func.count()).select_from(Job).where(Job.status == 'manual_review'))
    uncertain_refunds = await db.scalar(select(func.count()).select_from(Refund).where(
        Refund.status.in_(('uncertain','provider_pending'))))
    open_quotes = await db.scalar(select(func.count()).select_from(Order).where(
        Order.status.in_(('request_awaiting_delivery_quote',
                         'request_awaiting_operator_review','request_quote_offered',
                         'request_quote_accepted'))))
    blockers = [
        'live_sbp_checkout_not_implemented_for_approved_marketplace_quotes',
        'automatic_supplier_procurement_contract_not_verified',
        'shipping_fiscalization_and_final_pricing_contract_not_verified',
        'provider_and_supplier_end_to_end_staging_tests_not_executed',
    ]
    if not s.yookassa_shop_id or not s.yookassa_secret_key or not s.yookassa_vat_code:
        blockers.append('sbp_fiscal_provider_configuration_incomplete')
    if manual:
        blockers.append('manual_review_jobs_require_reconciliation')
    if uncertain_refunds:
        blockers.append('refund_reconciliation_required')
    return {'live_sales_ready': False, 'automatic_supplier_orders_ready': False,
            'blocking_reasons': blockers,
            'counts': {'manual_review_jobs': manual or 0,
                       'unresolved_refunds': uncertain_refunds or 0,
                       'open_operator_requests': open_quotes or 0},
            'flags': {'supplier_api_enabled': s.supplier_api_enabled,
                      'real_supplier_checkout_enabled': s.real_supplier_checkout_enabled,
                      'supplier_procurement_enabled': s.supplier_procurement_enabled},
            'no_external_calls': True}


@router.get('/orders/{order_id}/integrity')
async def order_integrity(order_id: UUID, db: AsyncSession = Depends(get_session)):
    order = await db.get(Order,order_id)
    if not order:
        raise HTTPException(404,'Order not found')
    lines = (await db.scalars(select(OrderLine).where(OrderLine.order_id==order.id))).all()
    payments = (await db.scalars(select(Payment).where(Payment.order_id==order.id))).all()
    procs = (await db.scalars(select(Procurement).join(OrderLine,
        OrderLine.id==Procurement.order_line_id).where(OrderLine.order_id==order.id))).all()
    settled = [p for p in payments if p.status=='succeeded']
    reserved_refunds = Decimal('0.00')
    if settled:
        refunds = (await db.scalars(select(Refund).where(Refund.payment_id.in_(
            [p.id for p in settled])))).all()
        reserved_refunds = sum((Decimal(str(r.amount)) for r in refunds
                                if r.status!='canceled'), Decimal('0.00'))
    blockers = []
    workflow = (order.request_details or {}).get('workflow')
    if workflow!='sbp_checkout':blockers.append('not_an_sbp_checkout')
    if not order.total_is_final:blockers.append('nonfinal_total')
    if not lines:blockers.append('no_order_lines')
    if len(set(l.id for l in lines))!=len(lines):blockers.append('duplicated_order_lines')
    if not settled:blockers.append('no_confirmed_sbp_payment')
    if len(settled)>1:blockers.append('multiple_successful_payments')
    if settled and any(p.amount != order.total for p in settled):
        blockers.append('paid_amount_mismatch')
    if procs and not settled:blockers.append('procurement_without_successful_payment')
    if reserved_refunds>sum((p.amount for p in settled),Decimal('0.00')):
        blockers.append('refunds_exceed_collected_funds')
    if any(p.status in ('uncertain','reconciliation_required') for p in payments):
        blockers.append('payment_reconciliation_required')
    if any(p.status=='manual_review' for p in procs):
        blockers.append('supplier_procurement_manual_review')
    return {'order_id':str(order.id),'status':order.status,'workflow':workflow,
            'line_count':len(lines),'payment_count':len(payments),
            'settled_payment_count':len(settled),'procurement_count':len(procs),
            'confirmed_paid_total':str(sum((p.amount for p in settled),Decimal('0.00'))),
            'refunds_reserved_total':str(reserved_refunds),
            'blocking_reasons':blockers,'read_only':True}
