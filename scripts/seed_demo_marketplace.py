"""Opt-in DEMO-only two-store/warehouse/PVZ/used listing fixture.

Never uses supplier APIs or issues buyer orders; requires explicit demo mode.
"""
import asyncio
from decimal import Decimal
from sqlalchemy import select
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.modules.catalog.models import Product,Brand
from app.modules.marketplace.models import (
    Merchant,Store,FulfillmentNode,DeliveryRoute,UsedPart,SellerOffer,InventoryBalance,
)
from scripts.seed_demo_catalog import main as seed_base

async def ensure(db, model, condition, values):
    obj=await db.scalar(select(model).where(condition))
    if obj is None:
        obj=model(**values);db.add(obj);await db.flush()
    return obj

async def main():
    settings=get_settings()
    if (settings.app_env!='development' or not settings.marketplace_demo_mode or
        settings.supplier_api_enabled or settings.supplier_procurement_enabled):
        raise SystemExit('DEMO seed requires development, demo mode, and disabled supplier API/purchases')
    await seed_base()
    async with get_session_factory()() as db:
        brand=await db.scalar(select(Brand).where(Brand.slug=='demo-parts'))
        product=await db.scalar(select(Product).where(Product.brand_id==brand.id))
        merchants=[]
        for name,slug in [('DEMO — Мой магазин','demo-owner'),('DEMO — Внешний продавец','demo-partner')]:
            merchant=await ensure(db,Merchant,Merchant.slug==slug,
                {'name':name,'slug':slug,'is_platform_owner':slug=='demo-owner','status':'active'})
            store=await ensure(db,Store,Store.slug==slug,
                {'merchant_id':merchant.id,'name':name,'slug':slug,'status':'active'})
            merchants.append((merchant,store))
        primary,store=merchants[0]
        warehouse=await ensure(db,FulfillmentNode,FulfillmentNode.code=='demo-kzn-warehouse',
            {'merchant_id':primary.id,'store_id':store.id,'code':'demo-kzn-warehouse',
             'name':'DEMO склад Казань','kind':'warehouse','city':'Казань','address':'Казань, демонстрационная ул. 1'})
        pickup=await ensure(db,FulfillmentNode,FulfillmentNode.code=='demo-kzn-pickup',
            {'merchant_id':primary.id,'store_id':store.id,'code':'demo-kzn-pickup',
             'name':'DEMO ПВЗ Казань','kind':'pickup','city':'Казань','address':'Казань, демонстрационная ул. 2'})
        await ensure(db,DeliveryRoute,(DeliveryRoute.source_node_id==warehouse.id)&(DeliveryRoute.target_node_id==pickup.id),
            {'source_node_id':warehouse.id,'target_node_id':pickup.id,'transit_days':1,'fee':Decimal('99.00')})
        offer=await ensure(db,SellerOffer,(SellerOffer.store_id==store.id)&(SellerOffer.sku=='DEMO-NEW-01'),
            {'merchant_id':primary.id,'store_id':store.id,'product_id':product.id,
             'sku':'DEMO-NEW-01','price':Decimal('2700.00'),'source_kind':'own_stock','status':'active'})
        await ensure(db,InventoryBalance,(InventoryBalance.seller_offer_id==offer.id)&(InventoryBalance.node_id==warehouse.id),
            {'seller_offer_id':offer.id,'node_id':warehouse.id,'on_hand':4,'reserved':0})
        partner,ps=merchants[1]
        pnode=await ensure(db,FulfillmentNode,FulfillmentNode.code=='demo-partner-pickup',
            {'merchant_id':partner.id,'store_id':ps.id,'code':'demo-partner-pickup',
             'name':'DEMO ПВЗ второго магазина','kind':'warehouse_pickup','city':'Казань',
             'address':'Казань, демонстрационная ул. 3'})
        second=await ensure(db,SellerOffer,(SellerOffer.store_id==ps.id)&(SellerOffer.sku=='DEMO-SECOND-01'),
            {'merchant_id':partner.id,'store_id':ps.id,'product_id':product.id,
             'sku':'DEMO-SECOND-01','price':Decimal('2490.00'),'source_kind':'own_stock','status':'active'})
        await ensure(db,InventoryBalance,(InventoryBalance.seller_offer_id==second.id)&(InventoryBalance.node_id==pnode.id),
            {'seller_offer_id':second.id,'node_id':pnode.id,'on_hand':2,'reserved':0})
        used=await ensure(db,UsedPart,(UsedPart.merchant_id==partner.id)&(UsedPart.title=='DEMO Б/У деталь №1'),
            {'merchant_id':partner.id,'original_product_id':product.id,'title':'DEMO Б/У деталь №1',
             'description':'Только для тестирования интерфейса','condition':'used_fair',
             'mileage_km':120000,'defects':'DEMO потертости','status':'published'})
        used_offer=await ensure(db,SellerOffer,(SellerOffer.store_id==ps.id)&(SellerOffer.sku=='DEMO-USED-01'),
            {'merchant_id':partner.id,'store_id':ps.id,'used_part_id':used.id,
             'sku':'DEMO-USED-01','price':Decimal('650.00'),'source_kind':'own_stock','status':'active'})
        await ensure(db,InventoryBalance,(InventoryBalance.seller_offer_id==used_offer.id)&(InventoryBalance.node_id==pnode.id),
            {'seller_offer_id':used_offer.id,'node_id':pnode.id,'on_hand':1,'reserved':0})
        await db.commit()
        print('DEMO ready: /product/'+product.slug+'?city=Казань')
        print('DEMO pickup ID:',pickup.id)
        print('These are fictional goods and merchants. Orders/payments are disabled.')

if __name__=='__main__':asyncio.run(main())
