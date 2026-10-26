"""Seller-offer cart support; not a reservation and not a payment."""
import uuid
from decimal import Decimal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.commerce.api import open_cart
from app.modules.commerce.models import CartLine
from app.modules.marketplace.models import SellerOffer
from app.modules.marketplace.service import offer_check,options_for_offer

router=APIRouter(prefix='/api/v1/marketplace/cart',tags=['marketplace-cart'])

class AddSeller(BaseModel):
    seller_offer_id: uuid.UUID
    pickup_node_id: uuid.UUID
    quantity: int = Field(ge=1,le=1000)

async def add_seller_line(body, user, db):
    offer=await offer_check(db,body.seller_offer_id)
    from app.modules.master_catalog.sale_terms import enforce_seller
    await enforce_seller(db,offer.id,body.quantity)
    chosen=None;validated_snapshot=None
    if offer.source_kind=='supplier':
        from app.modules.marketplace.supplier_selling import validate_for_cart
        chosen,validated_snapshot=await validate_for_cart(db,offer,body.pickup_node_id,body.quantity)
    else:
        opts=await options_for_offer(db,offer,body.pickup_node_id)
        if not any(isinstance(x, dict) and isinstance(x.get('stock'), int) and
                   x['stock'] >= body.quantity for x in (opts or [])):
            raise HTTPException(409,'Insufficient stock or no configured pickup route')
    # open_cart takes a stable User row FOR UPDATE, serializing cart writes.
    # Do not hold this lock during a live supplier HTTP verification.
    cart=await open_cart(db,user)
    line=await db.scalar(select(CartLine).where(
        CartLine.cart_id==cart.id,CartLine.seller_offer_id==offer.id,
        CartLine.fulfillment_node_id==body.pickup_node_id))
    from app.modules.marketplace.procurement import now
    if line:
        line.quantity=body.quantity
    else:
        line=CartLine(cart_id=cart.id,seller_offer_id=offer.id,
                      supplier_item_id=None,fulfillment_node_id=body.pickup_node_id,
                      quantity=body.quantity)
        db.add(line)
    line.line_kind='seller'
    line.product_id=offer.product_id
    line.quoted_unit_retail=offer.price
    if offer.source_kind=='own_stock':
        selected=next(x for x in opts if isinstance(x,dict) and isinstance(x.get('stock'),int) and x['stock']>=body.quantity)
        line.quote_metadata={'source_kind':'own_stock','origin_node_id':selected['node_id'],
                             'route_node_ids':selected['route_node_ids'],
                             'merchant_delivery_fee':selected['fee']}
    if chosen:
        line.quote_metadata={'source_kind':'supplier','route_node_ids':chosen['route_node_ids'],
                             'merchant_delivery_fee':chosen['fee']}
        line.supplier_destination_id=uuid.UUID(chosen['supplier_destination_id'])
        line.validated_snapshot_id=validated_snapshot.id
        line.validated_at=now()
    cart.revision+=1
    await db.commit()
    return {'status':'added','seller_offer_id':str(offer.id),
            'reserved':False,'payment_enabled':False,'supplier_checked_live':bool(chosen)}

@router.post('/items',status_code=201)
async def add_seller(body:AddSeller,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    return await add_seller_line(body,user,db)

@router.get('/preview')
async def preview(user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    cart=await open_cart(db,user,False)
    if not cart:return {'items':[],'total':'0.00','payment_enabled':False}
    lines=(await db.scalars(select(CartLine).where(CartLine.cart_id==cart.id))).all()
    results=[];total=Decimal('0')
    for line in lines:
        if line.seller_offer_id:
            try:
                offer=await offer_check(db,line.seller_offer_id)
                from app.modules.master_catalog.sale_terms import enforce_seller
                await enforce_seller(db,offer.id,line.quantity)
                if offer.source_kind=='supplier':
                    from app.modules.marketplace.supplier_selling import account_quote_options,age_seconds
                    opts=await account_quote_options(db,offer,line.fulfillment_node_id)
                    valid=bool(line.supplier_destination_id and line.validated_snapshot_id and
                      line.validated_at and age_seconds(line.validated_at)<=300 and
                      any(x['snapshot_id']==str(line.validated_snapshot_id) and
                          x['supplier_destination_id']==str(line.supplier_destination_id) and
                          x['stock']>=line.quantity and
                          line.quantity>=int(x.get('min_qty') or 1) and
                          line.quantity%int(x.get('pack_multiple') or 1)==0 for x in opts))
                else:
                    opts=await options_for_offer(db,offer,line.fulfillment_node_id)
                    valid=any(isinstance(x,dict) and isinstance(x.get('stock'),int) and x['stock']>=line.quantity for x in (opts or []))
                total += offer.price*line.quantity
                results.append({'line_id':str(line.id),'seller_offer_id':str(offer.id),
                                'quantity':line.quantity,'unit_price':str(offer.price),
                                'merchant_id':str(offer.merchant_id),'in_stock':valid})
            except HTTPException:
                results.append({'line_id':str(line.id),'in_stock':False})
        else:
            results.append({'line_id':str(line.id),'legacy_supplier_item_id':str(line.supplier_item_id),
                            'requires_live_validation':True})
    return {'items':results,'total':str(total),'payment_enabled':False,
            'note':'Preview only. No checkout/payment/supplier purchase on marketplace lines.'}
