import uuid
from decimal import Decimal
from fastapi import APIRouter,Depends,HTTPException,Header
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.commerce.idempotency import payload_hash
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.catalog.models import Product,Brand
from app.modules.suppliers.models import Supplier,SupplierItem,OfferSnapshot
from app.modules.pricing.service import get_active_policy,calculate_retail
from app.modules.commerce.models import Cart,CartLine,Order,OrderLine,Procurement,Replacement,Job
from app.modules.payments.models import Payment,Refund
from app.modules.payments.settlement import queue_replacement_refund
from app.modules.payments.gateway import gateway,GatewayError
from app.core.config import get_settings
from app.modules.offer_engine.providers import verify,Unavailable
router=APIRouter(prefix='/api/v1',tags=['cart-orders'])
class Add(BaseModel):
 supplier_item_id:uuid.UUID
 quantity:int=Field(ge=1,le=1000)
class Checkout(BaseModel):
 contact:str=Field(min_length=2,max_length=160)
 phone:str=Field(min_length=6,max_length=30)
 shipping_address:str=Field(min_length=8,max_length=400)
async def open_cart(db,user,create=True):
 # Serialize cart mutations by a stable existing row, even before a cart exists.
 # PostgreSQL partial unique index is the second line of defence.
 if create:
  await db.scalar(select(User).where(User.id==user.id).with_for_update())
 cart=await db.scalar(select(Cart).where(Cart.user_id==user.id,Cart.status=='open'))
 if cart is None and create:cart=Cart(user_id=user.id);db.add(cart);await db.flush()
 return cart
async def offer_context(db,item_id):
 item=await db.get(SupplierItem,item_id)
 if not item or not item.product_id or item.match_status!='confirmed':raise HTTPException(409,'Supplier item is not confirmed')
 product=await db.get(Product,item.product_id)
 if not product or product.status!='published':raise HTTPException(409,'Product not published')
 brand=await db.get(Brand,product.brand_id);supplier=await db.get(Supplier,item.supplier_id)
 snap=await db.scalar(select(OfferSnapshot).where(OfferSnapshot.supplier_item_id==item.id,OfferSnapshot.supplier_account_id.is_(None)).order_by(OfferSnapshot.observed_at.desc(),OfferSnapshot.id.desc()))
 if not snap or snap.currency!='RUB':raise HTTPException(409,'RUB offer required')
 return item,product,brand,supplier,snap
@router.get('/cart')
async def view_cart(user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 cart=await open_cart(db,user,create=False)
 if cart is None:return {'id':None,'revision':0,'items':[],'live_validation_required_before_payment':True}
 items=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id))).all()
 rows=[]
 for line in items:
  if line.line_kind=='supplier_quote':
   rows.append({'id':str(line.id),'source_kind':'supplier_quote','product_id':str(line.product_id),
                'snapshot_id':str(line.validated_snapshot_id),'quantity':line.quantity,
                'unit_price':str(line.quoted_unit_retail),'payment_enabled':False})
   continue
  if line.seller_offer_id:
   from app.modules.marketplace.models import SellerOffer
   offer=await db.get(SellerOffer,line.seller_offer_id)
   rows.append({'id':str(line.id),'seller_offer_id':str(offer.id) if offer else None,
                'quantity':line.quantity,'unit_price':str(offer.price) if offer else None,
                'merchant_id':str(offer.merchant_id) if offer else None,
                'requires_live_validation':True})
   continue
  item,product,brand,supplier,snap=await offer_context(db,line.supplier_item_id)
  rows.append({'id':str(line.id),'offer_id':str(item.id),'product':product.name,'supplier':supplier.code,'quantity':line.quantity,'cached_at':snap.observed_at.isoformat()})
 return {'id':str(cart.id),'revision':cart.revision,'items':rows,'live_validation_required_before_payment':True}
@router.post('/cart/items',status_code=201)
async def add(body:Add,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 item,product,brand,supplier,snap=await offer_context(db,body.supplier_item_id)
 if snap.quantity is not None and snap.quantity<body.quantity:raise HTTPException(409,'Not enough cached stock')
 # Validate once at addition when supplier integration is enabled. This never creates
 # supplier baskets or orders; a second check remains mandatory at checkout.
 validated_live=False
 if get_settings().supplier_api_enabled:
  try:await verify(supplier.code,item.external_key,brand.name,product.article,body.quantity)
  except Unavailable as exc:raise HTTPException(409,{'supplier':supplier.code,'error':str(exc)}) from exc
  validated_live=True
 cart=await open_cart(db,user)
 line=await db.scalar(select(CartLine).where(CartLine.cart_id==cart.id,CartLine.supplier_item_id==item.id))
 if line:line.quantity=body.quantity
 else:db.add(CartLine(cart_id=cart.id,supplier_item_id=item.id,quantity=body.quantity))
 cart.revision+=1;await db.commit()
 return {'state':'added','checked_live':validated_live,'note':'No supplier reservation. Checkout always revalidates.'}
@router.delete('/cart/items/{line_id}',status_code=204)
async def remove(line_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 cart=await open_cart(db,user,False);line=await db.get(CartLine,line_id)
 if not cart or not line or line.cart_id!=cart.id:raise HTTPException(404,'Cart line not found')
 await db.delete(line);cart.revision+=1;await db.commit()
@router.post('/checkout')
async def checkout(body:Checkout,idempotency_key:str=Header(alias='Idempotency-Key',min_length=8,max_length=100),
                   user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 # Fail closed: external procurement of every supplier is not yet contract-verified.
 # This release supports fake gateways only in tests, never charges real customers.
 if get_settings().app_env != 'test':
  raise HTTPException(503,'Live SBP checkout disabled until supplier purchase contracts and fiscal setup are verified')
 # Do not create a buyer order unless an SBP payment can be initiated.
 try:gateway().config()
 except GatewayError as exc:raise HTTPException(503,str(exc)) from exc
 request_fingerprint=payload_hash({'user':str(user.id),'workflow':'sbp_checkout',
                                   'payload':body.model_dump()})
 existing=await db.scalar(select(Order).where(Order.user_id==user.id,Order.idempotency_key==idempotency_key))
 if existing:
  if ((existing.request_fingerprint is not None and existing.request_fingerprint!=request_fingerprint) or
      (existing.request_details or {}).get('workflow')=='v017' or
      (existing.request_fingerprint is None and (
          existing.contact!=body.contact or existing.phone!=body.phone or
          existing.shipping_address!=body.shipping_address))):
   raise HTTPException(409,'Idempotency key already belongs to a different checkout request')
  pay=await db.scalar(select(Payment).where(Payment.order_id==existing.id))
  return {'order_id':str(existing.id),'status':existing.status,'sbp_url':pay.payment_url if pay else None,'reused':True}
 cart=await open_cart(db,user,False)
 if cart is None:raise HTTPException(409,'Empty cart')
 items=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id))).all()
 if not items:raise HTTPException(409,'Empty cart')
 if any(line.seller_offer_id or line.line_kind=='supplier_quote' for line in items):
  raise HTTPException(503,'Marketplace checkout disabled; use the operator request workflow')
 policy=await get_active_policy(db)
 if not policy:raise HTTPException(409,'Price policy not configured')
 revision=cart.revision
 # Verify only explicitly chosen supplier offers before payment; fail closed.
 validated=[]
 changed=[]
 for line in items:
  item,product,brand,supplier,snap=await offer_context(db,line.supplier_item_id)
  try:live=await verify(supplier.code,item.external_key,brand.name,product.article,line.quantity)
  except Unavailable as exc:raise HTTPException(409,{'offer':str(item.id),'reason':str(exc),'action':'choose_alternative'}) from exc
  price=calculate_retail(live.price,policy.markup_percent)
  # Never silently charge a different amount after the user viewed cached pricing.
  if live.price!=snap.purchase_price:
   db.add(OfferSnapshot(supplier_item_id=item.id,purchase_price=live.price,quantity=live.qty,
      lead_time_days=live.days,tax_status='included' if supplier.code=='rossko' else 'unknown'))
   changed.append({'offer_id':str(item.id),'new_retail_price':str(price),'reason':'supplier_price_changed'})
  validated.append((line,item,product,supplier,snap,live,price))
 if changed:
  await db.commit() # Refresh cache, no buyer order or payment created.
  raise HTTPException(409,{'message':'Price changed. Review prices and confirm checkout again.','changes':changed})
 # Lock cart only when committing; external HTTP calls have already completed.
 locked=await db.scalar(select(Cart).where(Cart.id==cart.id).with_for_update())
 if not locked or locked.revision!=revision or locked.status!='open':raise HTTPException(409,'Cart changed; revalidate')
 total=sum((x[-1]*x[0].quantity for x in validated),Decimal('0'))
 order=Order(user_id=user.id,idempotency_key=idempotency_key,total=total,
             request_fingerprint=request_fingerprint,
             request_details={'workflow':'sbp_checkout','payment_contract':'test_only'},
             **body.model_dump());db.add(order);await db.flush()
 fiscal_items=[]
 for line,item,product,supplier,snap,live,price in validated:
  db.add(OrderLine(order_id=order.id,supplier_item_id=item.id,snapshot_id=snap.id,title=product.name,
      supplier_code=supplier.code,quantity=line.quantity,unit_retail=price,unit_purchase=live.price))
  fiscal_items.append((product.name,line.quantity,price))
 payment=Payment(order_id=order.id,amount=total);db.add(payment);locked.status='payment_pending';await db.commit()
 try:
  response=await gateway().create(order.id,total,fiscal_items,user.email)
  if response.get('payment_method',{}).get('type') not in (None,'sbp'):raise GatewayError('Not an SBP response')
  payment.provider_id=response['id'];payment.status=response.get('status','pending');payment.payment_url=response.get('confirmation',{}).get('confirmation_url');await db.commit()
 except GatewayError as exc:
  payment.status='reconciliation_required';await db.commit()
  raise HTTPException(503,'SBP provider status uncertain; do not repeat with a new key') from exc
 return {'order_id':str(order.id),'status':order.status,'total':str(total),'sbp_url':payment.payment_url}
@router.get('/orders')
async def orders(user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 values=(await db.scalars(select(Order).where(Order.user_id==user.id).order_by(Order.created_at.desc()))).all()
 return [{'id':str(o.id),'status':o.status,'total':str(o.total),
          'total_is_final':o.total_is_final,
          'known_components_total':str(o.total) if not o.total_is_final else None,
          'grand_total':str(o.total) if o.total_is_final else None} for o in values]
@router.get('/orders/{order_id}')
async def order_detail(order_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 o=await db.get(Order,order_id)
 if not o or o.user_id!=user.id:raise HTTPException(404,'Order not found')
 lines=(await db.scalars(select(OrderLine).where(OrderLine.order_id==o.id))).all()
 rs=[]
 for l in lines:
  reps=(await db.scalars(select(Replacement).where(Replacement.order_line_id==l.id))).all()
  rs.extend({'id':str(r.id),'status':r.status,'line_id':str(l.id),'reason':r.reason,'alternative_id':str(r.alternative_item_id) if r.alternative_item_id else None} for r in reps)
 return {'id':str(o.id),'total':str(o.total),'status':o.status,
         'total_is_final':o.total_is_final,
         'known_components_total':str(o.total) if not o.total_is_final else None,
         'grand_total':str(o.total) if o.total_is_final else None,
         'lines':[{'id':str(l.id),'title':l.title,'supplier':l.supplier_code,'quantity':l.quantity,'unit_price':str(l.unit_retail),'status':l.status} for l in lines],
         'replacements':rs}
@router.post('/orders/{order_id}/replacements/{replacement_id}/decline')
async def decline(order_id:uuid.UUID,replacement_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 order=await db.get(Order,order_id)
 if not order or order.user_id!=user.id:raise HTTPException(404,'Order not found')
 r=await db.scalar(select(Replacement).where(Replacement.id==replacement_id).with_for_update())
 l=await db.get(OrderLine,r.order_line_id) if r else None
 if not l or l.order_id!=order.id:raise HTTPException(404,'Replacement not found')
 if r.status!='awaiting_customer':raise HTTPException(409,'Already resolved')
 pay=await db.scalar(select(Payment).where(Payment.order_id==order.id,Payment.status=='succeeded'))
 if not pay:raise HTTPException(409,'Paid SBP payment not found')
 ref=await queue_replacement_refund(db,pay.id,r.id,l.unit_retail*l.quantity)
 r.status='declined';l.status='refund_queued'
 await db.commit()
 return {'status':'refund_queued','refund_id':str(ref.id)}
@router.post('/orders/{order_id}/replacements/{replacement_id}/accept')
async def accept(order_id:uuid.UUID,replacement_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 order=await db.get(Order,order_id)
 if not order or order.user_id!=user.id:raise HTTPException(404,'Order not found')
 r=await db.scalar(select(Replacement).where(Replacement.id==replacement_id).with_for_update())
 l=await db.get(OrderLine,r.order_line_id) if r else None
 if not l or l.order_id!=order.id or r.status!='awaiting_customer':raise HTTPException(409,'No pending replacement')
 if not r.alternative_item_id:raise HTTPException(409,'No proposed offer')
 if r.suggested_price is None or r.suggested_price<=0 or r.suggested_price>l.unit_retail:raise HTTPException(409,'Replacement must have a positive price no higher than the agreed unit price')
 pay=await db.scalar(select(Payment.id).where(Payment.order_id==order.id,Payment.status=='succeeded'))
 if not pay:raise HTTPException(409,'Paid SBP payment not found')
 r.status='accepted_pending_live_validation';l.status='replacement_pending'
 db.add(Job(dedup_key=f'replacement:{r.id}',kind='replacement',payload={'id':str(r.id)}));await db.commit()
 return {'status':r.status}
