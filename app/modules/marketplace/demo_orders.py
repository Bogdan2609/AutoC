"""Offline, admin-only seller-order split simulator.

NEVER charges the customer and NEVER creates procurement jobs. Explicitly
requires non-production and MARKETPLACE_DEMO_MODE=true. Used to exercise
stock locking and merchant grouping before payment contracts exist.
"""
import uuid
from decimal import Decimal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.accounts.models import User
from app.modules.commerce.models import Cart,CartLine,Order,OrderLine
from app.modules.marketplace.models import SellerOffer,InventoryBalance,SellerOrder,UsedPart
from app.modules.marketplace.service import offer_check,options_for_offer,reserve_stock

router=APIRouter(prefix='/api/v1/admin/marketplace', tags=['marketplace-demo'],
                 dependencies=[Depends(require_admin)])

class Simulate(BaseModel):
    customer_id: uuid.UUID
    idempotency_key: str = Field(min_length=10,max_length=100)

@router.post('/simulate-order')
async def simulate(body:Simulate,db:AsyncSession=Depends(get_session)):
    s=get_settings()
    if s.app_env=='production' or not s.marketplace_demo_mode or s.supplier_procurement_enabled:
        raise HTTPException(403,'Sandbox order simulations are disabled')
    user=await db.get(User,body.customer_id)
    if not user:raise HTTPException(404,'Unknown customer')
    existing=await db.scalar(select(Order).where(Order.user_id==user.id,
        Order.idempotency_key==body.idempotency_key))
    if existing:
        return {'order_id':str(existing.id),'status':existing.status,'reused':True}
    cart=await db.scalar(select(Cart).where(Cart.user_id==user.id,Cart.status=='open').with_for_update())
    if not cart:raise HTTPException(409,'Cart empty')
    lines=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id))).all()
    if not lines or any(not l.seller_offer_id for l in lines):
        raise HTTPException(409,'Simulation requires only seller-owned stock lines')
    grouped={};positions=[];total=Decimal('0.00')
    # Stable lock acquisition order avoids deadlocks when real PostgreSQL runs two sessions.
    for line in sorted(lines,key=lambda x:str(x.seller_offer_id)):
        offer=await offer_check(db,line.seller_offer_id)
        if offer.source_kind!='own_stock':raise HTTPException(409,'Supplier purchasing disabled')
        opts=await options_for_offer(db,offer,line.fulfillment_node_id)
        if not opts:raise HTTPException(409,'No enabled route')
        # Prefer earliest eligible route and sufficient physical stock.
        origin=next((x for x in opts if x['stock']>=line.quantity),None)
        if not origin:raise HTTPException(409,'Insufficient stock')
        balance=await db.scalar(select(InventoryBalance).where(
            InventoryBalance.seller_offer_id==offer.id,
            InventoryBalance.node_id==uuid.UUID(origin['node_id'])))
        if not balance:raise HTTPException(409,'Inventory disappeared')
        positions.append((line,offer,balance))
        grouped[offer.merchant_id]=grouped.get(offer.merchant_id,Decimal('0'))+offer.price*line.quantity
        total+=offer.price*line.quantity
    order=Order(user_id=user.id,idempotency_key=body.idempotency_key,total=total,
                status='simulation_no_payment',contact=user.display_name or 'Test customer',
                phone='0000000000',shipping_address='TEST ONLY - NO SHIPPING')
    db.add(order)
    await db.flush()
    for merchant_id, amount in grouped.items():
        db.add(SellerOrder(customer_order_id=order.id,merchant_id=merchant_id,
                           total=amount,status='simulation_no_payment'))
    for line,offer,balance in positions:
        await reserve_stock(db,balance.id,line.quantity,
                            f'demo-order:{order.id}:{line.id}')
        used=await db.get(UsedPart,offer.used_part_id) if offer.used_part_id else None
        db.add(OrderLine(order_id=order.id,seller_offer_id=offer.id,merchant_id=offer.merchant_id,
            fulfillment_node_id=line.fulfillment_node_id,supplier_item_id=None,snapshot_id=None,
            title=used.title if used else offer.sku,supplier_code='OWN-STOCK',
            quantity=line.quantity,unit_retail=offer.price,unit_purchase=Decimal('0'),
            status='simulation_no_payment'))
    cart.status='simulated'
    await db.commit()
    return {'order_id':str(order.id),'status':order.status,'total':str(total),
            'seller_orders':len(grouped),'payment_created':False,
            'supplier_purchase_created':False,'simulation_only':True}
