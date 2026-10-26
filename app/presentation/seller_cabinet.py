"""Minimal tenant-scoped seller workbench backed by the same API services."""
import uuid
from fastapi import APIRouter,Depends,Form,HTTPException,Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.marketplace.models import Store,StoreMembership,SellerOffer,InventoryBalance,FulfillmentNode,UsedPart
from app.modules.marketplace.api import SetOfferStatus,StockSet,offer_status,set_stock
from app.presentation.browser_security import browser_user,check_csrf
from app.presentation.shop import html,go,must_login

router=APIRouter(include_in_schema=False)

@router.get('/seller',response_class=HTMLResponse)
async def cabinet(request:Request,db:AsyncSession=Depends(get_session)):
    user=await browser_user(request,db)
    if redirect:=must_login(user,'/seller'):return redirect
    members=(await db.execute(select(StoreMembership,Store).join(Store,Store.id==StoreMembership.store_id)
                              .where(StoreMembership.user_id==user.id))).all()
    result=[]
    for membership,store in members:
        offers=(await db.scalars(select(SellerOffer).where(SellerOffer.store_id==store.id).order_by(SellerOffer.sku).limit(100))).all()
        rows=[]
        for offer in offers:
            used=await db.get(UsedPart,offer.used_part_id) if offer.used_part_id else None
            balances=(await db.execute(select(InventoryBalance,FulfillmentNode).join(
                FulfillmentNode,FulfillmentNode.id==InventoryBalance.node_id).where(
                InventoryBalance.seller_offer_id==offer.id))).all()
            rows.append({'offer':offer,'used':used,'balances':balances})
        result.append({'store':store,'membership':membership,'offers':rows})
    return html(request,'seller.html',user=user,stores=result,title='Кабинет продавца')


@router.post('/seller/offers/{offer_id}/status')
async def status_update(offer_id:uuid.UUID,request:Request,csrf:str=Form(),
                        status:str=Form(),db:AsyncSession=Depends(get_session)):
    user=await browser_user(request,db)
    if redirect:=must_login(user,'/seller'):return redirect
    check_csrf(request,csrf,user)
    await offer_status(offer_id,SetOfferStatus(status=status),user=user,db=db)
    return go('/seller')


@router.post('/seller/offers/{offer_id}/stock')
async def stock_update(offer_id:uuid.UUID,request:Request,csrf:str=Form(),
                       node_id:uuid.UUID=Form(),on_hand:int=Form(),db:AsyncSession=Depends(get_session)):
    user=await browser_user(request,db)
    if redirect:=must_login(user,'/seller'):return redirect
    check_csrf(request,csrf,user)
    await set_stock(offer_id,StockSet(node_id=node_id,on_hand=on_hand),user=user,db=db)
    return go('/seller')
