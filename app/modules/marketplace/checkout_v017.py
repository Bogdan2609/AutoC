"""v0.17 quote-cart and operator-only checkout requests.

NO charges, procurement, provider HTTP or confirmation of a final price. A request
is a nonbinding operator work item, never a payment-ready customer order.
Catalog product UUIDs remain authoritative; each order line carries immutable
commercial_snapshot JSON for future brand, category, packaging and kit changes.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from app.core.rate_limit import enforce_budget, peer_ip
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.commerce.idempotency import payload_hash
from app.core.security import require_admin
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.catalog.models import Product, Brand
from app.modules.commerce.api import open_cart
from app.modules.commerce.models import Cart, CartLine, Order, OrderLine, Procurement, OrderWorkflowEvent
from app.modules.marketplace.models import (SellerOffer, SellerOrder, InventoryBalance,
                                             InventoryMovement, Merchant)
from app.modules.marketplace.service import offer_check, options_for_offer
from app.modules.marketplace.api import QuotePreviewRequest, preview_supplier_quote
from app.modules.suppliers.models import OfferSnapshot, SupplierItem
from app.modules.offer_engine.models import OfferDetail
from app.modules.payments.models import Payment
from app.modules.marketplace.commercial_guardrails import MAX_CART_LINES
from app.modules.marketplace.price_integrity_v0421 import compare_accepted_price

router = APIRouter(prefix='/api/v1/marketplace/cart', tags=['marketplace-requests'])
admin = APIRouter(prefix='/api/v1/admin/marketplace', tags=['marketplace-requests-admin'],
                  dependencies=[Depends(require_admin)])
MONEY = Decimal('0.01')


def money(value):
    return Decimal(str(value)).quantize(MONEY)


def conflict(message):
    raise HTTPException(409, message)


def as_str(value):
    return str(value) if value is not None else None


class AddQuote(BaseModel):
    product_id:uuid.UUID
    snapshot_id:uuid.UUID
    supplier_account_id:uuid.UUID
    supplier_destination_id:uuid.UUID
    pickup_node_id:uuid.UUID
    route_node_ids:list[uuid.UUID] = Field(min_length=1,max_length=4)
    quantity:int=Field(ge=1,le=1000)


class ChangeQuantity(BaseModel):
    quantity:int=Field(ge=1,le=1000)


class RequestOrder(BaseModel):
    expected_revision:int=Field(ge=0)
    contact:str=Field(min_length=2,max_length=160)
    phone:str=Field(min_length=6,max_length=30)
    acknowledge_estimate:bool


async def find_current(db,line,quantity=None):
    """Reverify exactly the chosen account, address, snapshot and route.

    This is local cache validation, not a guarantee of external supplier stock.
    """
    m=line.quote_metadata or {}
    qty=line.quantity if quantity is None else quantity
    if line.line_kind=='supplier_quote':
        preview=await preview_supplier_quote(line.product_id,QuotePreviewRequest(
            snapshot_id=line.validated_snapshot_id,
            supplier_account_id=uuid.UUID(m['supplier_account_id']),
            supplier_destination_id=line.supplier_destination_id,
            pickup_node_id=line.fulfillment_node_id,
            route_node_ids=[uuid.UUID(x) for x in m['route_node_ids']],
            quantity=qty),db)
        snap=await db.get(OfferSnapshot,line.validated_snapshot_id)
        if not snap or not snap.supplier_account_id or snap.supplier_account_id!=uuid.UUID(m['supplier_account_id']) or snap.supplier_destination_id!=line.supplier_destination_id:
            conflict('Supplier snapshot binding changed')
        item=await db.get(SupplierItem,snap.supplier_item_id)
        if not item or item.product_id!=line.product_id:conflict('Product identity changed')
        return {'source_kind':'supplier_quote','line_id':str(line.id),'quantity':qty,
                'product_id':str(line.product_id),'merchant_id':m['merchant_id'],
                'supplier_code':preview['supplier_code'],
                'snapshot_id':str(snap.id),'supplier_item_id':str(item.id),
                'supplier_account_id':m['supplier_account_id'],
                'supplier_destination_id':str(line.supplier_destination_id),
                'warehouse_id':preview['warehouse_id'],
                'pickup_node_id':str(line.fulfillment_node_id),
                'origin_node_id':str(uuid.UUID(preview['receiving_node_id'])) if preview.get('receiving_node_id') else conflict('Receiving node UUID unavailable'),
                'route_node_ids':preview['route_node_ids'],
                'unit_retail':preview['unit_price'],
                'goods_subtotal':preview['goods_subtotal'],
                'merchant_delivery_fee':preview['merchant_delivery_fee'],
                'known_total':preview['known_components_total'],
                'supplier_fee_known':preview['supplier_delivery_fee_known'],
                'stock':preview['available_stock_lower_bound'],
                'min_qty':m.get('min_qty',1),'pack_multiple':m.get('pack_multiple',1),
                'balance_id':None}
    if line.seller_offer_id:
        offer=await offer_check(db,line.seller_offer_id)
        if offer.source_kind!='own_stock':conflict('Supplier-backed seller offers require warehouse quote flow')
        from app.modules.master_catalog.sale_terms import enforce_seller
        await enforce_seller(db,offer.id,qty)
        opts=await options_for_offer(db,offer,line.fulfillment_node_id)
        selected=next((v for v in opts if v['node_id']==m.get('origin_node_id') and
                       v['route_node_ids']==m.get('route_node_ids') and v['stock']>=qty),None)
        if not selected:conflict('Selected seller stock or route no longer available')
        balance=await db.scalar(select(InventoryBalance).where(
            InventoryBalance.seller_offer_id==offer.id,
            InventoryBalance.node_id==uuid.UUID(selected['node_id'])))
        if not balance:conflict('Selected inventory balance no longer available')
        goods=money(offer.price*qty);fee=money(selected['fee'])
        return {'source_kind':'own_stock','line_id':str(line.id),'quantity':qty,
                'product_id':as_str(offer.product_id),'used_part_id':as_str(offer.used_part_id),
                'seller_offer_id':str(offer.id),'merchant_id':str(offer.merchant_id),
                'supplier_code':'OWN-STOCK','snapshot_id':None,'supplier_item_id':None,
                'pickup_node_id':str(line.fulfillment_node_id),
                'origin_node_id':selected['node_id'],'route_node_ids':selected['route_node_ids'],
                'unit_retail':str(money(offer.price)),
                'goods_subtotal':str(goods),'merchant_delivery_fee':str(fee),
                'known_total':str(goods+fee),'supplier_fee_known':True,
                'stock':selected['stock'],'balance_id':str(balance.id)}
    conflict('Legacy supplier cart line cannot be submitted as an operator request')


async def evaluate(db,cart):
    lines=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id)
                            .order_by(CartLine.id).limit(MAX_CART_LINES+1))).all()
    if len(lines)>MAX_CART_LINES:
        conflict('Cart exceeds item limit; remove items before preview')
    from app.modules.marketplace.quote_cache_v0421 import preview_scope
    with preview_scope():
        return await _evaluate_lines(db, cart, lines)


async def _evaluate_lines(db, cart, lines):
    results=[]
    for line in lines:
        try:
            current=await find_current(db,line)
            was=line.quoted_unit_retail
            stored_fee=(line.quote_metadata or {}).get('merchant_delivery_fee')
            drift=compare_accepted_price(was,current['unit_retail'],stored_fee,current['merchant_delivery_fee'])
            changed=drift['changed']
            current.update(valid=not changed,reason='Price or route tariff changed; reconfirm the item' if changed else None,
                           accepted_unit_price=as_str(was),accepted_merchant_delivery_fee=stored_fee,
                           price_drift=drift,requires_reconfirmation=changed)
        except HTTPException as exc:
            current={'line_id':str(line.id),'source_kind':line.line_kind,
                     'quantity':line.quantity,'valid':False,'reason':str(exc.detail)}
        results.append(current)
    total=sum((money(x['known_total']) for x in results if x['valid']),Decimal('0.00'))
    return {'cart_id':str(cart.id),'revision':cart.revision,'items':results,
            'item_count':len(results),'ready_for_request':bool(results and all(x['valid'] for x in results)),
            'known_components_total':str(total),'grand_total':None,'final_price_complete':False,
            'payment_enabled':False,'procurement_enabled':False,
            'supplier_network_called':False,
            'note':'Only a provisional operator request can be created; no supplier reservation, payment or guaranteed final price.'}


@router.post('/quotes',status_code=201)
async def add_quote(body:AddQuote,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    # Local verified supplier quote. Supplier API must never be called on this route.
    quote=await preview_supplier_quote(body.product_id,QuotePreviewRequest(**body.model_dump(exclude={'product_id'})),db)
    if not quote['pickup_selected'] or str(body.pickup_node_id)!=quote['pickup_node_id']:
        conflict('Explicit pickup point required')
    snap=await db.get(OfferSnapshot,body.snapshot_id)
    item=await db.get(SupplierItem,snap.supplier_item_id) if snap else None
    if not snap or not item or item.product_id!=body.product_id:conflict('Snapshot product identity mismatch')
    from app.modules.marketplace.models import SupplierAccount
    account=await db.get(SupplierAccount,body.supplier_account_id)
    if not account:conflict('Supplier account missing')
    detail=await db.get(OfferDetail,body.snapshot_id)
    if not detail:conflict('Warehouse detail missing')
    meta={'supplier_account_id':str(body.supplier_account_id),
          'supplier_destination_id':str(body.supplier_destination_id),
          'merchant_id':str(account.merchant_id),
          'route_node_ids':[str(x) for x in body.route_node_ids],
          'merchant_delivery_fee':quote['merchant_delivery_fee'],
          'warehouse_id':quote['warehouse_id'],
          'receiving_node_id':str(uuid.UUID(quote['receiving_node_id'])) if quote.get('receiving_node_id') else conflict('Receiving node UUID unavailable'),
          'min_qty':quote['effective_min_qty'],'pack_multiple':quote['effective_pack_multiple'],
          'units_per_package':quote['units_per_package']}
    # Serialize cart creation and all quote edits per user on PostgreSQL.
    await db.scalar(select(User).where(User.id==user.id).with_for_update())
    cart=await open_cart(db,user)
    rows=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id,
             CartLine.line_kind=='supplier_quote',
             CartLine.validated_snapshot_id==body.snapshot_id,
             CartLine.fulfillment_node_id==body.pickup_node_id))).all()
    line=next((x for x in rows if (x.quote_metadata or {}).get('route_node_ids')==meta['route_node_ids']),None)
    if line is None:
        from sqlalchemy import func
        count = await db.scalar(select(func.count()).select_from(CartLine).where(CartLine.cart_id==cart.id))
        if int(count or 0)>=MAX_CART_LINES:
            conflict('Cart item limit reached')
        line=CartLine(cart_id=cart.id,quantity=body.quantity,
            supplier_item_id=None,seller_offer_id=None,
            line_kind='supplier_quote',product_id=body.product_id,
            supplier_destination_id=body.supplier_destination_id,
            fulfillment_node_id=body.pickup_node_id,validated_snapshot_id=body.snapshot_id)
        db.add(line)
    line.quantity=body.quantity
    line.quoted_unit_retail=money(quote['unit_price'])
    line.quote_metadata=meta
    line.validated_at=datetime.now(timezone.utc)
    cart.revision+=1
    await db.commit()
    return {'line_id':str(line.id),'cart_revision':cart.revision,'status':'added',
            'known_total':quote['known_components_total'],'grand_total':None,
            'supplier_reservation_created':False,'payment_created':False}


@router.get('/v017/preview')
async def preview(request:Request,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    await enforce_budget('marketplace_v017_preview',str(user.id)+':'+peer_ip(request),limit=30,period=60)
    cart=await open_cart(db,user,False)
    if not cart:return {'cart_id':None,'revision':0,'items':[],'item_count':0,
                        'ready_for_request':False,'known_components_total':'0.00',
                        'grand_total':None,'final_price_complete':False,'payment_enabled':False}
    return await evaluate(db,cart)


@router.patch('/v017/items/{line_id}')
async def reconfirm(line_id:uuid.UUID,body:ChangeQuantity,
                    user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    await db.scalar(select(User).where(User.id==user.id).with_for_update())
    cart=await open_cart(db,user,False)
    if not cart:raise HTTPException(404,'Open cart not found')
    line=await db.scalar(select(CartLine).where(CartLine.id==line_id,
                                                CartLine.cart_id==cart.id))
    if not line:raise HTTPException(404,'Cart line not found')
    current=await find_current(db,line,body.quantity)
    line.quantity=body.quantity
    line.quoted_unit_retail=money(current['unit_retail'])
    line.quote_metadata={**(line.quote_metadata or {}),
                         'merchant_delivery_fee':current['merchant_delivery_fee']}
    cart.revision+=1
    await db.commit()
    return {'line_id':str(line.id),'cart_revision':cart.revision,
            'accepted_unit_price':current['unit_retail'],
            'accepted_merchant_delivery_fee':current['merchant_delivery_fee'],
            'final_price_complete':False}


async def snapshot_catalog(db, position):
    pid=position.get('product_id')
    if pid:
        product=await db.get(Product,uuid.UUID(pid))
        if not product:conflict('Product has been removed')
        brand=await db.get(Brand,product.brand_id)
        if not brand:conflict('Brand has been removed')
        if product.status!='published':conflict('Product not currently published')
        from app.modules.master_catalog.api import catalog_links
        linked=await catalog_links(db,product.id)
        extra={'family_at_request':linked['family'],'composition_at_request':linked['composition'],
               'factory_packages_at_request':linked['factory_packages']}
        return {'product_id':str(product.id),'brand_id':str(brand.id),
                'brand':brand.name,'article':product.article,'title':product.name,
                'catalog_slug_at_request':product.slug,
                'catalog_status_at_request':product.status,
                'master_catalog_v018':extra}
    from app.modules.marketplace.models import UsedPart
    used=await db.get(UsedPart,uuid.UUID(position['used_part_id']))
    if not used or used.status!='published':conflict('Used item unpublished')
    return {'product_id':None,'brand_id':None,'title':used.title,
            'used_part_id':str(used.id),'condition':used.condition}


def request_state(positions):
    return ('request_awaiting_delivery_quote' if any(not x['supplier_fee_known'] for x in positions)
            else 'request_awaiting_operator_review')


@router.post('/v017/submit',status_code=201)
async def submit(body:RequestOrder,
                 idempotency_key:str=Header(alias='Idempotency-Key',min_length=10,max_length=100),
                 user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    if not body.acknowledge_estimate:
        raise HTTPException(422,'Explicit acknowledgement of provisional prices is required')
    fingerprint=payload_hash({'user':str(user.id),'payload':body.model_dump()})
    # Lock buyer first. This serializes different idempotency keys against one
    # user's cart, including the race between new requests and retries.
    await db.scalar(select(User).where(User.id==user.id).with_for_update())
    old=await db.scalar(select(Order).where(Order.user_id==user.id,
                                           Order.idempotency_key==idempotency_key))
    if old:
        if old.request_fingerprint!=fingerprint or not old.request_details or old.request_details.get('workflow')!='v017':
            conflict('Idempotency key already used for a different request')
        return {'order_id':str(old.id),'status':old.status,'reused':True,
                'known_components_total':str(old.total),'grand_total':None,
                'payment_created':False,'supplier_purchase_created':False}
    cart=await db.scalar(select(Cart).where(Cart.user_id==user.id,Cart.status=='open').with_for_update())
    if not cart or cart.revision!=body.expected_revision:conflict('Cart revision changed; preview again')
    report=await evaluate(db,cart)
    if not report['ready_for_request']:
        conflict({'message':'Cart has unavailable or changed prices; reconfirm affected items',
                  'items':report['items']})
    positions=report['items']
    # Build catalog snapshots. Nonbinding requests NEVER reserve stock.
    identities=[await snapshot_catalog(db,x) for x in positions]
    status=request_state(positions)
    # Provisional total includes *known* merchant route fees only. Never payable.
    total=money(report['known_components_total'])
    # A request cannot be paid and is not a promise to fulfil stock.
    expires_at=None
    request_details={'workflow':'v017','quote_only':True,'price_final':False,
                     'known_total':str(total),'currency':'RUB','requires_operator_approval':True,
                     'supplier_delivery_unpriced':status=='request_awaiting_delivery_quote',
                     'cart_revision':cart.revision,'item_count':len(positions),
                     'own_stock_reservation_expires_at':None,
                     'stock_reserved':False}
    order=Order(user_id=user.id,idempotency_key=idempotency_key,contact=body.contact,
                phone=body.phone,shipping_address='Pickup selected per line in commercial_snapshot',
                status=status,total=total,total_is_final=False,
                request_fingerprint=fingerprint,request_details=request_details,
                request_expires_at=expires_at)
    db.add(order)
    await db.flush()
    grouped={}
    for position,identity in zip(positions,identities):
        mid=uuid.UUID(position['merchant_id'])
        grouped[mid]=grouped.get(mid,Decimal('0.00'))+money(position['known_total'])
        # Freeze an audit-ready commercial selection irrespective of later edits to
        # brand, category, pictures, packaging, supplier warehouses or tariffs.
        frozen={**identity,**{k:v for k,v in position.items()
                              if k not in ('balance_id','stock','valid','reason')},
                'commercial_snapshot_version':1,'final_price_complete':False,
                'payment_enabled':False}
        if position['source_kind']=='own_stock':
            # Preserve the original stock reference for an operator recheck,
            # but never store a reservation_key without an actual reservation.
            frozen['inventory_balance_id']=position['balance_id']
            frozen['stock_reserved']=False
        purchase_cost=(await db.get(OfferSnapshot,uuid.UUID(position['snapshot_id']))).purchase_price if position.get('snapshot_id') else Decimal('0.00')
        db.add(OrderLine(order_id=order.id,
            product_id=uuid.UUID(position['product_id']) if position.get('product_id') else None,
            merchant_id=mid,
            seller_offer_id=uuid.UUID(position['seller_offer_id']) if position.get('seller_offer_id') else None,
            snapshot_id=uuid.UUID(position['snapshot_id']) if position.get('snapshot_id') else None,
            supplier_item_id=uuid.UUID(position['supplier_item_id']) if position.get('supplier_item_id') else None,
            fulfillment_node_id=uuid.UUID(position['pickup_node_id']),
            title=identity['title'],supplier_code=position['supplier_code'],
            quantity=position['quantity'],unit_retail=money(position['unit_retail']),
            unit_purchase=purchase_cost,status=status,commercial_snapshot=frozen))
    for merchant_id,subtotal in grouped.items():
        db.add(SellerOrder(customer_order_id=order.id,merchant_id=merchant_id,
                           total=money(subtotal),status=status))
    cart.status='request_submitted'
    await db.commit()
    return {'order_id':str(order.id),'status':order.status,'reused':False,
            'seller_orders':len(grouped),'line_count':len(positions),
            'known_components_total':str(total),'grand_total':None,
            'total_is_final':False,'payment_created':False,
            'supplier_purchase_created':False,
            'reservation_expires_at':expires_at.isoformat() if expires_at else None,
            'note':'Nonbinding operator request; no charge, invoice or external purchase.'}


async def request_detail(db,order):
    rows=(await db.scalars(select(OrderLine).where(OrderLine.order_id==order.id))).all()
    proposal=(order.request_details or {}).get('operator_quote')
    return {'order_id':str(order.id),'status':order.status,
            'operator_quote_total':proposal.get('total') if proposal else None,
            'operator_quote_revision':proposal.get('revision') if proposal else None,
            'known_components_total':str(order.total),'grand_total':None,
            'total_is_final':False,'payment_enabled':False,'procurement_enabled':False,
            'reservation_expires_at':order.request_expires_at.isoformat() if order.request_expires_at else None,
            'lines':[{'line_id':str(x.id),'title':x.title,'quantity':x.quantity,
                      'source_kind':(x.commercial_snapshot or {}).get('source_kind'),
                      'selection_snapshot':x.commercial_snapshot} for x in rows]}


@router.get('/v017/orders/{order_id}')
async def detail(order_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    order=await db.get(Order,order_id)
    if not order or order.user_id!=user.id or (order.request_details or {}).get('workflow') != 'v017':
        raise HTTPException(404,'Request not found')
    return await request_detail(db,order)


async def cancel_request(db,order,*,expired=False):
    if order.status in ('request_cancelled','request_expired'):return True
    if order.status not in ('request_awaiting_delivery_quote','request_awaiting_operator_review',
                             'request_quote_offered','request_quote_declined',
                             'request_quote_accepted'):
        conflict('Request cannot be cancelled in current status')
    # Defensive isolation from payment and real procurement subsystems.
    if await db.scalar(select(Payment.id).where(Payment.order_id==order.id).limit(1)):
        conflict('Request has a payment record and needs manual reconciliation')
    lines=(await db.scalars(select(OrderLine).where(OrderLine.order_id==order.id))).all()
    if any([await db.scalar(select(Procurement.id).where(Procurement.order_line_id==line.id).limit(1)) for line in lines]):
        conflict('Request has a supplier procurement record and needs manual reconciliation')
    for line in lines:
        m=line.commercial_snapshot or {}
        # Old v0.17 requests could reserve on submission. Release those
        # exactly once; newer nonbinding requests have no reservation_key.
        if m.get('source_kind')=='own_stock' and m.get('reservation_key'):
            balance_id=uuid.UUID(m['inventory_balance_id'])
            original=await db.scalar(select(InventoryMovement).where(
                InventoryMovement.idempotency_key==str(m['reservation_key'])))
            if (not original or original.balance_id!=balance_id or
                    original.delta_reserved!=line.quantity or original.reason!='reserve'):
                conflict('Historical inventory reservation lacks verified movement; manual review required')
            if await db.scalar(select(InventoryMovement.id).where(
                    InventoryMovement.idempotency_key==f'v017-release:{order.id}:{line.id}')):
                conflict('Reservation already released; requires manual reconciliation')
            result=await db.scalar(update(InventoryBalance).where(
                InventoryBalance.id==balance_id,
                InventoryBalance.reserved>=line.quantity)
                .values(reserved=InventoryBalance.reserved-line.quantity)
                .returning(InventoryBalance.id))
            if not result:conflict('Cannot reconcile original stock reservation')
            db.add(InventoryMovement(balance_id=balance_id,delta_reserved=-line.quantity,
                 reason='v017_request_expired' if expired else 'v017_request_cancelled',
                 idempotency_key=f'v017-release:{order.id}:{line.id}'))
        line.status='request_expired' if expired else 'request_cancelled'
    sellers=(await db.scalars(select(SellerOrder).where(SellerOrder.customer_order_id==order.id))).all()
    for seller in sellers:seller.status='request_expired' if expired else 'request_cancelled'
    order.status='request_expired' if expired else 'request_cancelled'
    db.add(OrderWorkflowEvent(order_id=order.id, event_type=order.status,
              actor_type='system' if expired else 'customer', evidence={'release_legacy_reserves':True}))
    return False


@router.post('/v017/orders/{order_id}/cancel')
async def cancel(order_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    order=await db.scalar(select(Order).where(Order.id==order_id).with_for_update())
    if not order or order.user_id!=user.id or (order.request_details or {}).get('workflow')!='v017':
        raise HTTPException(404,'Request not found')
    reused=await cancel_request(db,order)
    await db.commit()
    return {'order_id':str(order.id),'status':order.status,'reused':reused,
            'payment_created':False,'supplier_purchase_created':False}


@admin.get('/order-requests')
async def operator_requests(db:AsyncSession=Depends(get_session)):
    statuses=('request_awaiting_delivery_quote','request_awaiting_operator_review',
              'request_quote_offered','request_quote_declined','request_quote_accepted',
              'request_cancelled')
    orders=(await db.scalars(select(Order).where(Order.status.in_(statuses))
                              .order_by(Order.created_at.desc()).limit(100))).all()
    return [{'order_id':str(o.id),'status':o.status,'known_components_total':str(o.total),
             'total_is_final':False,'created_at':o.created_at.isoformat()} for o in orders
            if (o.request_details or {}).get('workflow')=='v017']


async def expire_reservation(db,order_id):
    """Called only by the worker for scheduled, owned-stock operator requests."""
    order=await db.scalar(select(Order).where(Order.id==order_id).with_for_update())
    if not order or (order.request_details or {}).get('workflow')!='v017':return
    if not order.request_expires_at:return
    now=datetime.now(timezone.utc)
    until=order.request_expires_at
    if until.tzinfo is None:until=until.replace(tzinfo=timezone.utc)
    if until>now:conflict('Reservation expiry job executed too early')
    await cancel_request(db,order,expired=True)
