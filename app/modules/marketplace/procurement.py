"""Operator-initiated supplier purchasing. Every outward write is deliberately manual.

Never call this module from a public request, payment webhook, or periodic worker.
The first outbound supplier-order request commits an UNKNOWN state beforehand.
An ambiguous timeout MUST be reconciled with the supplier, never replayed.
"""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid, JSON, CheckConstraint, UniqueConstraint

from app.core.config import get_settings
from app.core.db import Base, get_session
from app.core.security import require_admin
from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
from app.modules.marketplace.rossko_checkout_payload import build_rossko_checkout_payload
from app.modules.marketplace.receiving_contact import (
    ReceivingContact, ReceivingContactError, receiving_contact_for_node,
)
from app.modules.marketplace.account_credentials import for_account, ready
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot
from app.modules.offer_engine.models import OfferDetail
from app.modules.offer_engine.providers import PROVIDERS, exact
from app.modules.marketplace.purchase_gateway import PurchaseIntent, build_purchase_plan
from app.modules.marketplace.rossko_requisites import (
    RosskoRequisiteError, resolve_live_requisite, make_payment,
)
from app.modules.marketplace.supplier_delivery_policy import (
    require_rossko_courier, require_live_rossko_courier,
)
from app.modules.commerce.models import Order, OrderLine, Procurement, OrderWorkflowEvent
from app.modules.commerce.sale_lifecycle import verified_paid_order, SaleLineExecution, reconcile_order


def now():
    return datetime.now(timezone.utc)


class SupplierPurchaseAttempt(Base):
    __tablename__ = 'supplier_purchase_attempts'
    __table_args__ = (
        CheckConstraint('quantity > 0', name='purchase_attempt_quantity'),
        CheckConstraint('approved_unit_price > 0', name='purchase_attempt_price'),
        CheckConstraint("status IN ('prepared','validating','dispatch_unknown','accepted','accepted_unverified','reconciling','verified','rejected','manual_review')",
                        name='purchase_attempt_status'),
        UniqueConstraint('order_line_id', name='uq_purchase_attempt_order_line'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_ref: Mapped[str] = mapped_column(String(100), unique=True)
    # Historic unlinked attempts remain readable, but can never be confirmed.
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('order_lines.id', name='fk_purchase_order_line', ondelete='RESTRICT'), nullable=True)
    buyer_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('customer_orders.id', ondelete='RESTRICT'), nullable=True)
    supplier_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_accounts.id', name='fk_purchase_account', ondelete='RESTRICT'), index=True)
    supplier_destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_destinations.id', name='fk_purchase_destination', ondelete='RESTRICT'))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_offer_snapshots.id', name='fk_purchase_snapshot', ondelete='RESTRICT'))
    quantity: Mapped[int] = mapped_column(Integer)
    approved_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    contact_name: Mapped[str] = mapped_column(String(160))
    contact_phone: Mapped[str] = mapped_column(String(40))
    contact_comment: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    payment_id: Mapped[str] = mapped_column(String(40))
    requisite_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='prepared')
    remote_order_ids: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconcile_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconcile_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


router = APIRouter(prefix='/api/v1/admin/supplier-purchases', tags=['supplier-purchases'],
                   dependencies=[Depends(require_admin)])


class PreparePurchase(BaseModel):
    order_line_id: uuid.UUID | None = None
    snapshot_id: uuid.UUID
    destination_id: uuid.UUID
    quantity: int = Field(ge=1, le=1000)
    approved_unit_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    client_ref: str = Field(pattern=r'^[a-zA-Z0-9:_-]{12,100}$')
    # Supplier recipient is resolved from the receiving FulfillmentNode.
    # Buyer/operator-provided contact data is never accepted as the source.
    payment_id: str = Field(min_length=1, max_length=40)
    requisite_id: str | None = Field(default=None, max_length=80)


async def context(db, snapshot_id, destination_id, quantity, ceiling):
    snap = await db.get(OfferSnapshot, snapshot_id)
    dest = await db.get(SupplierDestination, destination_id)
    if not snap or not dest or snap.supplier_destination_id != dest.id or snap.supplier_account_id != dest.account_id:
        raise HTTPException(409, 'Supplier quote is not bound to the selected destination')
    account = await db.get(SupplierAccount, dest.account_id)
    supplier = await db.get(Supplier, account.supplier_id) if account else None
    node = await db.get(FulfillmentNode, dest.node_id)
    item = await db.get(SupplierItem, snap.supplier_item_id)
    detail = await db.get(OfferDetail, snap.id)
    if not all((account, supplier, node, item, detail)):
        raise HTTPException(409, 'Incomplete supplier quote or fulfillment mapping')
    if supplier.code != 'rossko':
        raise HTTPException(409, 'Live checkout for this supplier is not contract-verified')
    if (not account.enabled or not account.read_enabled or not account.procurement_enabled or
        not dest.enabled or not dest.verified or node.merchant_id != account.merchant_id or
        not node.active or item.supplier_id != supplier.id or item.match_status != 'confirmed'):
        raise HTTPException(409, 'Account/destination/product is not approved for procurement')
    if (snap.quantity is None or snap.quantity < quantity or
        quantity < detail.min_qty or quantity % max(1, detail.pack_multiple) != 0 or
        not detail.verified_live or snap.currency != 'RUB' or snap.purchase_price <= 0 or snap.purchase_price > ceiling):
        raise HTTPException(409, 'Quantity, price or quote is not validated')
    observed = snap.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    if not 0 <= (now()-observed).total_seconds() <= 300:
        raise HTTPException(409, 'Quote is older than five minutes; refresh first')
    # A manually submitted snapshot must belong to the CURRENT successful
    # account+destination search, not a recently aged-out historical offer.
    from app.modules.catalog.models import Product, Brand
    from app.modules.offer_engine.models import CacheState
    from app.modules.marketplace.account_search import account_key
    product=await db.get(Product,item.product_id) if item.product_id else None
    brand=await db.get(Brand,product.brand_id) if product else None
    state=await db.scalar(select(CacheState).where(
        CacheState.supplier_code==supplier.code,
        CacheState.lookup_key==account_key(brand.name,product.article,account.id,dest.id))) if brand else None
    marker=detail.metadata_public.get('_search_job_id') if isinstance(detail.metadata_public,dict) else None
    if (not state or state.status not in ('ready','refreshing') or
        not marker or marker!=state.last_completed_job_id):
        raise HTTPException(409, 'Quote is not from the latest successful account search; refresh first')
    try:
        require_rossko_courier(dest.external_delivery_id, dest.external_address_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if not ready('rossko', account, dest):
        raise HTTPException(409, 'Supplier account credentials are not configured')
    return account, dest, supplier, item, detail


async def site_contact(db: AsyncSession, destination: SupplierDestination, account: SupplierAccount):
    """Load the warehouse/PVZ contact rather than end-customer PII."""
    node = await db.get(FulfillmentNode, destination.node_id)
    if not node or node.merchant_id != account.merchant_id:
        raise HTTPException(409, 'Receiving site is not owned by the supplier account merchant')
    # Explicit refresh matters when operator changes contact after prepare.
    await db.refresh(node)
    try:
        return receiving_contact_for_node(node)
    except ReceivingContactError as exc:
        raise HTTPException(409, str(exc)) from None



async def paid_line_context(db, order_line_id, snapshot_id, quantity, ceiling):
    """A supplier-side charge must be attributable to an actual paid buyer line."""
    line = await db.get(OrderLine, order_line_id)
    order = await db.get(Order, line.order_id) if line else None
    if not order:
        raise HTTPException(409, 'Purchase must reference an existing buyer order line')
    await verified_paid_order(db, order)
    if (line.snapshot_id != snapshot_id or line.quantity != quantity or
            line.supplier_item_id is None or line.unit_purchase <= 0 or
            ceiling < line.unit_purchase or ceiling > line.unit_retail):
        raise HTTPException(409, 'Supplier purchase differs from the paid commercial selection')
    execution = await db.scalar(select(SaleLineExecution).where(
        SaleLineExecution.order_line_id == line.id))
    proc = await db.scalar(select(Procurement).where(Procurement.order_line_id == line.id))
    if (not execution or execution.source_kind != 'supplier' or
            execution.status not in ('procurement_pending', 'manual_review') or
            not proc or proc.status not in ('pending', 'manual_review')):
        raise HTTPException(409, 'Purchase workflow not ready or already resolved')
    return order, line, execution, proc

@router.post('/prepare', status_code=201)
async def prepare(body: PreparePurchase, db: AsyncSession = Depends(get_session)):
    # Must opt in at all three levels even to prepare a real-purchase request.
    s = get_settings()
    if not (s.supplier_procurement_enabled and s.real_supplier_checkout_enabled):
        raise HTTPException(403, 'Real supplier purchasing is globally disabled')
    existing = await db.scalar(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.client_ref == body.client_ref))
    if existing:
        if (existing.order_line_id != body.order_line_id or existing.snapshot_id != body.snapshot_id or existing.quantity != body.quantity or
            existing.supplier_destination_id != body.destination_id or
            existing.approved_unit_price != body.approved_unit_price or
            existing.payment_id != body.payment_id or
            existing.requisite_id != body.requisite_id):
            raise HTTPException(409, 'Purchase idempotency key reused for different items')
        return {'id': str(existing.id), 'status': existing.status, 'reused': True}
    if body.order_line_id is None:
        raise HTTPException(409, 'Paid order line ID is mandatory for new purchases')
    # Requisite ID is resolved later from this destination and a fresh
    # account-specific GetCheckoutDetails. Only validate the payment ID here.
    if (not body.payment_id.isascii() or not body.payment_id.isdecimal() or
            int(body.payment_id) <= 0):
        raise HTTPException(409, 'Invalid ROSSKO supplier payment ID')
    if body.requisite_id is not None and (not body.requisite_id.isascii() or
            not body.requisite_id.isdecimal() or int(body.requisite_id) <= 0):
        raise HTTPException(409, 'Invalid ROSSKO requisite ID')
    order, line, _, _ = await paid_line_context(db, body.order_line_id, body.snapshot_id,
                                                body.quantity, body.approved_unit_price)
    account, destination, _, item, _ = await context(db, body.snapshot_id, body.destination_id,
                                body.quantity, body.approved_unit_price)
    receiver = await site_contact(db, destination, account)
    # No cross-merchant payment may buy stock for a different seller/account.
    if (line.merchant_id is None or line.merchant_id != account.merchant_id or
        line.supplier_item_id != item.id or
        line.fulfillment_node_id is None or
        line.fulfillment_node_id != destination.node_id):
        raise HTTPException(409, 'Paid line belongs to another merchant, pickup node or SKU')
    obj = SupplierPurchaseAttempt(
        client_ref=body.client_ref, order_line_id=body.order_line_id, supplier_account_id=account.id,
        supplier_destination_id=body.destination_id, snapshot_id=body.snapshot_id,
        quantity=body.quantity, approved_unit_price=body.approved_unit_price,
        buyer_order_id=order.id,
        contact_name=receiver.name, contact_phone=receiver.phone, contact_comment=receiver.comment,
        payment_id=body.payment_id, requisite_id=body.requisite_id,
        status='prepared', remote_order_ids=[])
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Duplicate purchase idempotency key') from exc
    return {'id':str(obj.id),'status':'prepared', 'real_order_sent':False,
            'warning':'A separate confirmation will place a chargeable supplier order'}


class ConfirmPurchase(BaseModel):
    client_ref: str
    confirmation: Literal['PLACE_REAL_SUPPLIER_ORDER']


async def checkout_rossko(config, *, item, detail, dest, request):
    """ROSSKO's published SOAP GetCheckout contract; no synthetic order success.

    The merchant configures payment_id and destination from its own ROSSKO portal.
    """
    require_rossko_courier(dest.external_delivery_id, dest.external_address_id)
    if get_settings().app_env == 'test':
        raise RuntimeError('Live ROSSKO checkout forbidden in test mode; inject fake adapter')
    import httpx
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from zeep.helpers import serialize_object

    wsdl='https://api.rossko.ru/service/v2.1/GetCheckout?wsdl'
    async with httpx.AsyncClient(timeout=httpx.Timeout(25,connect=4)) as http_client:
        with httpx.Client(timeout=httpx.Timeout(10,connect=4)) as wsdl_http:
            client=AsyncClient(wsdl,transport=AsyncTransport(client=http_client,wsdl_client=wsdl_http))
            plan = build_purchase_plan(PurchaseIntent(
                supplier='rossko', buyer_order_line_id=str(request.order_line_id),
                reference=request.client_ref, brand=item.raw_brand,
                article=item.raw_article, quantity=request.quantity,
                purchase_price=request.approved_unit_price,
                maximum_approved_price=request.approved_unit_price,
                supplier_token=detail.external_token, warehouse=detail.warehouse,
                minimum=detail.min_qty, multiple=detail.pack_multiple,
                delivery_mode='courier', courier_route_verified=True))
            parts=plan.payload['PARTS']['Part']
            # Pull only warehouse/PVZ recipient data; NEVER buyer Order.contact.
            # The verified order identity was pinned from the paid line at prepare/confirm.
            payload = build_rossko_checkout_payload(
                keys=(config.rossko_key1,config.rossko_key2),
                delivery_id=dest.external_delivery_id,
                address_id=dest.external_address_id,
                payment_id=request.payment_id,
                requisite_id=request.requisite_id,
                receiving_contact=ReceivingContact(request.contact_name,
                                                   request.contact_phone,
                                                   request.contact_comment),
                parts=parts,
                order_uuid=request.buyer_order_id,
            )
            raw=await client.service.GetCheckout(**payload)
    return serialize_object(raw,target_cls=dict)


def normalize_checkout_result(response):
    """Fail closed on unknown or partial SOAP responses."""
    if not isinstance(response,dict):return 'manual_review',[], 'unknown_response'
    result=response.get('CheckoutResult',response)
    if not isinstance(result,dict):return 'manual_review',[], 'unknown_response'
    success=str(result.get('success',result.get('Success',''))).lower() in ('true','1')
    order_ids=(result.get('OrderIDS') or {})
    if not isinstance(order_ids,dict):return 'manual_review',[], 'unknown_response'
    ids=order_ids.get('id') or []
    if not isinstance(ids,list):ids=[ids]
    if (len(ids) > 20 or any(isinstance(x, bool) or not str(x).isascii() or
        not str(x).isdecimal() or int(str(x)) < 1 for x in ids)):
        return 'manual_review',[], 'malformed_remote_order_ids'
    ids=[str(x) for x in ids]
    if len(ids) != len(set(ids)):
        return 'manual_review',[], 'duplicate_remote_order_ids'
    errors=result.get('ItemsErrorList')
    if errors and (not isinstance(errors,dict) or errors.get('ItemError')):
        return 'manual_review',ids,'supplier_partial_rejection'
    if success and ids:return 'accepted',ids,None
    return 'manual_review',ids,'supplier_no_confirmed_order_ids'


@router.get('/attention')
async def unresolved_supplier_attempts(db: AsyncSession = Depends(get_session)):
    # Read-only evidence, with no network, refund, or outbound supplier write.
    records = (await db.scalars(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.status.in_((
            'validating', 'dispatch_unknown', 'manual_review',
            'accepted_unverified', 'reconciling')))
        .order_by(SupplierPurchaseAttempt.created_at).limit(100))).all()
    return [{'attempt_id': str(a.id), 'status': a.status,
             'supplier_account_id': str(a.supplier_account_id),
             'destination_id': str(a.supplier_destination_id),
             'created_at': a.created_at.isoformat() if a.created_at else None,
             'sent_at': a.sent_at.isoformat() if a.sent_at else None,
             'error_code': a.error_code, 'external_write_retry_allowed': False,
             'operator_action': 'Verify worker and supplier read-only; never blind resubmit'}
            for a in records]


@router.post('/{attempt_id}/confirm')
async def confirm(attempt_id:uuid.UUID, body:ConfirmPurchase,
                  db:AsyncSession=Depends(get_session)):
    s=get_settings()
    if not (s.supplier_procurement_enabled and s.real_supplier_checkout_enabled):
        raise HTTPException(403, 'Real supplier purchasing is globally disabled')
    attempt=await db.scalar(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.id==attempt_id).with_for_update())
    if not attempt:raise HTTPException(404, 'Purchase attempt not found')
    if attempt.client_ref!=body.client_ref:raise HTTPException(409,'Purchase reference mismatch')
    if attempt.status!='prepared':
        return {'id':str(attempt.id),'status':attempt.status,'sent_again':False}
    if not attempt.order_line_id:
        raise HTTPException(409,'Legacy unlinked purchase is forbidden; reconcile manually')
    order, line, execution, proc = await paid_line_context(
        db, attempt.order_line_id, attempt.snapshot_id, attempt.quantity, attempt.approved_unit_price)
    account,dest,supplier,item,detail=await context(db,attempt.snapshot_id,
        attempt.supplier_destination_id,attempt.quantity,attempt.approved_unit_price)
    if attempt.buyer_order_id is not None and attempt.buyer_order_id != order.id:
        raise HTTPException(409, 'Stored purchase order UUID differs from paid buyer order')
    attempt.buyer_order_id = order.id  # Upgrade legacy prepared attempts from their verified paid order line.
    if (account.id != attempt.supplier_account_id or
        line.merchant_id != account.merchant_id or line.supplier_item_id != item.id or
        line.fulfillment_node_id != dest.node_id):
        raise HTTPException(409, 'Supplier account, seller or fulfillment source changed')
    # Auto-resolve operational contacts, never purchaser contacts, for every checkout.
    receiver = await site_contact(db, dest, account)
    attempt.contact_name, attempt.contact_phone, attempt.contact_comment = receiver.name, receiver.phone, receiver.comment
    # Take a durable validation lease. A repeated confirm must not dispatch twice.
    attempt.status='validating'
    attempt.error_code=None
    await db.commit()
    from app.modules.offer_engine.discovery import claim_supplier_budget
    try:
        allowed=await claim_supplier_budget('rossko',str(account.id),account.requests_per_hour)
        if not allowed:
            raise ValueError('quota_or_redis_unavailable')
        config=for_account('rossko',account,dest)
        offers=await PROVIDERS['rossko'].search(item.raw_brand,item.raw_article,config)
        match=next((o for o in offers if o.key==item.external_key and
            o.warehouse==detail.warehouse and exact(o,item.raw_brand,item.raw_article)),None)
        if (not match or match.qty is None or match.qty<attempt.quantity or
            attempt.quantity<match.minimum or attempt.quantity%max(1,match.multiple)!=0 or
            match.price > attempt.approved_unit_price):
            attempt.status='rejected'
            attempt.error_code='live_quote_changed'
            attempt.completed_at=now()
            await db.commit()
            return {'id':str(attempt.id),'status':attempt.status,'order_sent':False}
    except Exception as exc:
        # A failed read-only re-quote never implies an order was created.
        await db.rollback()
        record=await db.get(SupplierPurchaseAttempt,attempt_id)
        if record and record.status=='validating':
            record.status='prepared'
            record.error_code='pre_dispatch_validation_failed'
            await db.commit()
        return {'id':str(attempt_id),'status':'prepared','order_sent':False,
                'reason':'pre_dispatch_validation_failed','sent_again':False}
    # Final read-only check against ROSSKO's current delivery and address data.
    # This is BEFORE the irreversible external write fence; errors do not
    # create orders or trigger automatic retries.
    try:
        from app.modules.marketplace.account_probe import (
            live_rossko_details, normalize_checkout_details,
        )
        raw_details = await live_rossko_details(config)
        details = normalize_checkout_details(raw_details)
        require_live_rossko_courier(dest.external_delivery_id,
                                    dest.external_address_id, details,
                                    payment_id=attempt.payment_id)
        attempt.requisite_id = resolve_live_requisite(
            raw_details, getattr(dest, 'external_requisite_id', None), attempt.requisite_id)
    except Exception:
        await db.rollback()
        record = await db.get(SupplierPurchaseAttempt, attempt_id)
        if record and record.status == 'validating':
            record.status = 'prepared'
            record.error_code = 'courier_or_requisite_pre_dispatch_validation_failed'
            await db.commit()
        return {'id': str(attempt_id), 'status': 'prepared', 'order_sent': False,
                'reason': 'courier_or_requisite_pre_dispatch_validation_failed',
                'sent_again': False}
    # Re-read the node immediately before the irreversible external write fence;
    # persist the exact warehouse contact used by this attempt for audit.
    receiver = await site_contact(db, dest, account)
    attempt.contact_name, attempt.contact_phone, attempt.contact_comment = receiver.name, receiver.phone, receiver.comment
    # Commit UNKNOWN immediately before the one and only externally mutating POST.
    # On lost acknowledgement DO NOT automatically re-submit the order.
    attempt.status='dispatch_unknown'
    attempt.sent_at=now()
    await db.commit()
    try:
        response=await checkout_rossko(config,item=item,detail=detail,dest=dest,request=attempt)
        status,ids,error=normalize_checkout_result(response)
        # A checkout ACK is not proof of a verified order. The independent
        # GetOrders reconciliation must confirm the SKU, quantity and price.
        attempt.status='accepted_unverified' if status == 'accepted' else status
        attempt.remote_order_ids=ids
        attempt.error_code=error
        attempt.completed_at=now()
        if status == 'accepted':
            proc.status = 'manual_review'
            execution.status = 'manual_review'
            db.add(OrderWorkflowEvent(order_id=order.id,
                event_type='supplier_checkout_acknowledged', actor_type='system',
                evidence={'line_id': str(line.id), 'attempt_id': str(attempt.id),
                          'requires_readback': True}))
            await reconcile_order(db, order)
        else:
            proc.status = 'manual_review'
            execution.status = 'manual_review'
            await reconcile_order(db, order)
        await db.commit()
        return {'id':str(attempt.id),'status':attempt.status,'remote_order_ids':ids,
                'reconciliation_required':True}
    except Exception:
        await db.rollback()
        record=await db.get(SupplierPurchaseAttempt,attempt_id)
        if record and record.status=='dispatch_unknown':
            record.status='manual_review'
            record.error_code='uncertain_outbound_operation'
            record.completed_at=now()
            # Do not dispatch again, even if supplier may have accepted the write.
            execution=await db.scalar(select(SaleLineExecution).where(
                SaleLineExecution.order_line_id==record.order_line_id))
            if execution:execution.status='manual_review'
            await db.commit()
        return {'id':str(attempt_id),'status':'manual_review',
                'reconciliation_required':True,'sent_again':False}


@router.get('')
async def list_attempts(db:AsyncSession=Depends(get_session)):
    records=(await db.scalars(select(SupplierPurchaseAttempt)
        .order_by(SupplierPurchaseAttempt.created_at.desc()).limit(100))).all()
    return [{'id':str(a.id),'client_ref':a.client_ref,'status':a.status,
             'account_id':str(a.supplier_account_id), 'destination_id':str(a.supplier_destination_id),
             'amount_ceiling':str(a.approved_unit_price*a.quantity),
             'remote_order_ids':a.remote_order_ids,'error_code':a.error_code} for a in records]
