"""Indications only; never produces checkout-capable supplier snapshots."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.modules.marketplace.models import Merchant, FulfillmentNode, SupplierAccount, SupplierDestination
from app.modules.suppliers.models import Supplier
from .pricing import active_rules_for
from app.modules.pricing.resolver import resolve_supplier_quote
from app.modules.marketplace.routing import routes_to_pickup
from .models import FeedBatch, FeedOffer, FeedSource, FeedWarehouseMap, utc


def aware(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def preliminary_feed_offers(db, product_id, *, pickup_node_id=None, city=None):
    rows = (await db.execute(
        select(FeedOffer, FeedBatch, FeedSource, FeedWarehouseMap,
               SupplierAccount, SupplierDestination, FulfillmentNode, Merchant)
        .join(FeedBatch, FeedBatch.id==FeedOffer.batch_id)
        .join(FeedSource, FeedSource.code==FeedBatch.supplier_code)
        .join(FeedWarehouseMap, (FeedWarehouseMap.supplier_code==FeedSource.code) &
              (FeedWarehouseMap.warehouse_code==FeedOffer.warehouse_code))
        .join(SupplierAccount, SupplierAccount.id==FeedWarehouseMap.account_id)
        .join(SupplierDestination, SupplierDestination.id==FeedWarehouseMap.destination_id)
        .join(Supplier, Supplier.id==SupplierAccount.supplier_id)
        .join(FulfillmentNode, FulfillmentNode.id==SupplierDestination.node_id)
        .join(Merchant, Merchant.id==SupplierAccount.merchant_id)
        .where(FeedOffer.product_id==product_id,FeedOffer.stock_min>0,
               FeedBatch.status=='published',FeedSource.active_batch_id==FeedOffer.batch_id,
               FeedSource.commercial_verified.is_(True),FeedSource.price_kind=='purchase',
               FeedSource.currency=='RUB',
               FeedWarehouseMap.approved.is_(True), Supplier.code==FeedSource.code, SupplierAccount.enabled.is_(True),
               SupplierAccount.read_enabled.is_(True),SupplierDestination.enabled.is_(True),
               SupplierDestination.verified.is_(True),SupplierDestination.account_id==SupplierAccount.id,
               FulfillmentNode.active.is_(True),
               FulfillmentNode.merchant_id==SupplierAccount.merchant_id,
               Merchant.status=='active')
        .order_by(FeedOffer.purchase_price,FeedOffer.id)
        .limit(120))).all()
    keys={(account.id,destination.id,offer.warehouse_code) for offer,batch,source,mapping,account,destination,node,merchant in rows}
    price_rules=await active_rules_for(db,keys)
    out=[];now=utc()
    for offer,batch,source,mapping,account,destination,node,merchant in rows:
        rule=price_rules.get((account.id,destination.id,offer.warehouse_code))
        price_info=resolve_supplier_quote(offer.purchase_price, commercial_rule=rule,
            feed_source=source, warehouse_map_approved=mapping.approved,
            tax_status=source.tax_status)
        if price_info is None:continue  # Feed preview always needs an approved exact rule.
        breakdown=price_info['pricing_breakdown']
        if not batch.generated_at or not 0 <= (now-aware(batch.generated_at)).total_seconds() <= source.max_age_hours*3600:
            continue
        if pickup_node_id:
            routes=await routes_to_pickup(db,account.merchant_id,node.id,pickup_node_id)
            if not routes:
                continue
        else:
            routes=[None]
        for route in routes:
            route_days=route.get('days') if route else 0
            arrival=(offer.lead_days+route_days if offer.lead_days is not None and route_days is not None else None)
            locality=route['city'] if route else node.city
            if city and locality.casefold()!=city.casefold():continue
            fulfillment={
                'node_id':str(node.id), 'node':node.name,'city':locality,
                'stock':offer.stock_min,'stock_display':offer.stock_raw,
                'stock_is_lower_bound':offer.is_lower_bound,
                'days':arrival,'fee':str(route.get('fee','0.00')) if route else '0.00',
                'pickup_node_id':str(pickup_node_id) if pickup_node_id else None,
                'route_node_ids':route['route_node_ids'] if route else [str(node.id)],
            }
            out.append({
                'source_kind':'feed_indicative','offer_id':None,
                'feed_offer_id':str(offer.id),
                'store':merchant.name,'merchant':merchant.name,
                'merchant_id':str(merchant.id),
                'supplier_code':source.code,'warehouse_id':offer.warehouse_code,
                'supplier_account_id':str(account.id),
                'supplier_destination_id':str(destination.id),
                'snapshot_id':None,
                'price':price_info['price'],
                'pricing_basis':price_info['pricing_basis'],
                'pricing_breakdown':breakdown,
                'price_policy_version':price_info['price_policy_version'],
                'feed_batch_id':str(batch.id),
                'currency':'RUB','fulfillment':[fulfillment],
                'requires_live_validation':True,
                'cart_enabled':False,'payment_enabled':False,
                'stock_unverified':True, 'generated_at':batch.generated_at.isoformat(),
                'note':'Предварительная цена по утверждённым затратам, без неподтверждённых вычетов НДС; обязательна живая проверка API перед корзиной и оплатой',
            })
    return out
