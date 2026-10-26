"""Read cache for public supplier-backed SellerOffers; live GET on cart addition only.

Never creates supplier baskets, orders or payment authorizations.
"""
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select
from fastapi import HTTPException
from app.core.config import get_settings
from app.modules.catalog.models import Brand, Product
from app.modules.marketplace.models import SupplierAccount,SupplierDestination,FulfillmentNode
from app.modules.marketplace.account_credentials import for_account,ready
from app.modules.marketplace.routing import routes_to_pickup, describe_supplier_route
from app.modules.marketplace.account_search import account_key
from app.modules.marketplace.offer_rank import fulfillment_rank
from app.modules.offer_engine.models import CacheState
from app.modules.offer_engine.providers import PROVIDERS, exact
from app.modules.offer_engine.discovery import claim_supplier_budget
from app.modules.offer_engine.models import OfferDetail
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot


def age_seconds(instant):
    ts=instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc)-ts).total_seconds()


async def account_quote_options(db,offer,pickup_node_id=None):
    if not offer.supplier_account_id or not offer.supplier_item_id:return []
    account=await db.get(SupplierAccount,offer.supplier_account_id)
    item=await db.get(SupplierItem,offer.supplier_item_id)
    if not account or not item or not account.enabled or not account.read_enabled or account.merchant_id!=offer.merchant_id:
        return []
    supplier=await db.get(Supplier,account.supplier_id)
    if not supplier or item.supplier_id!=supplier.id or item.product_id!=offer.product_id or item.match_status!='confirmed':
        return []
    product=await db.get(Product,offer.product_id)
    brand=await db.get(Brand,product.brand_id) if product else None
    if not brand:return []
    mappings=(await db.scalars(select(SupplierDestination).where(
        SupplierDestination.account_id==account.id,SupplierDestination.enabled.is_(True),
        SupplierDestination.verified.is_(True)))).all()
    options=[]
    for dest in mappings:
        node=await db.get(FulfillmentNode,dest.node_id)
        if not node or not node.active or node.merchant_id!=offer.merchant_id:continue
        state=await db.scalar(select(CacheState).where(
            CacheState.supplier_code==supplier.code,
            CacheState.lookup_key==account_key(brand.name,product.article,account.id,dest.id)))
        if not state or state.status not in ('ready','refreshing') or not state.last_completed_job_id:continue
        snap=await db.scalar(select(OfferSnapshot).join(OfferDetail,OfferDetail.snapshot_id==OfferSnapshot.id).where(
            OfferSnapshot.supplier_item_id==item.id,
            OfferSnapshot.supplier_account_id==account.id,
            OfferSnapshot.supplier_destination_id==dest.id,
            OfferDetail.metadata_public['_search_job_id'].as_string()==state.last_completed_job_id)
            .order_by(OfferSnapshot.observed_at.desc(),OfferSnapshot.id.desc()).limit(1))
        if (not snap or snap.currency!='RUB' or snap.quantity is None or snap.quantity<=0 or
            not 0<=age_seconds(snap.observed_at)<=get_settings().offer_max_age_seconds):continue
        detail=await db.get(OfferDetail,snap.id)
        if not detail or not detail.verified_live:continue
        from app.modules.master_catalog.sale_terms import supplier_constraints
        sale=await supplier_constraints(db,item.id,account.id,dest.id,detail.warehouse,
                                        detail.min_qty,detail.pack_multiple)
        if pickup_node_id:
            routes=await routes_to_pickup(db,offer.merchant_id,node.id,pickup_node_id)
        else:
            routes=[None]
        for route in routes:
            route_info=describe_supplier_route(snap,detail,node,route,pickup_selected=pickup_node_id is not None)
            options.append({**route_info,'node_id':str(node.id),'node':node.name,'stock':snap.quantity,
                'supplier_destination_id':str(dest.id),'snapshot_id':str(snap.id),
                'min_qty':sale['minimum_units'],'pack_multiple':sale['step_units'],
                'units_per_package':sale['units_per_package'],
                'requires_live_validation':True})
    return sorted(options,key=fulfillment_rank)


async def validate_for_cart(db,offer,pickup_id,qty):
    options=await account_quote_options(db,offer,pickup_id)
    candidates=[o for o in options if o['stock']>=qty]
    if not candidates:
        raise HTTPException(409,'No fresh supplier quote for selected pickup')
    account=await db.get(SupplierAccount,offer.supplier_account_id)
    supplier=await db.get(Supplier,account.supplier_id)
    item=await db.get(SupplierItem,offer.supplier_item_id)
    p=await db.get(Product,offer.product_id)
    brand=await db.get(Brand,p.brand_id) if p else None
    if not p or not brand or not supplier or supplier.code not in PROVIDERS:
        raise HTTPException(409,'Supplier matching information is incomplete')
    # Price, stock and account/destination cannot be borrowed from any other login.
    for option in candidates:
        dest=await db.get(SupplierDestination,UUID(option['supplier_destination_id']))
        snap=await db.get(OfferSnapshot,UUID(option['snapshot_id']))
        detail=await db.get(OfferDetail,snap.id)
        # Reject invalid customer quantities before hitting live supplier API.
        if qty<int(option.get('min_qty') or 1) or qty % int(option.get('pack_multiple') or 1):
            continue
        if not ready(supplier.code,account,dest):continue
        if not await claim_supplier_budget(supplier.code,str(account.id),account.requests_per_hour):
            raise HTTPException(503,'Supplier rate limit or Redis unavailable')
        config=for_account(supplier.code,account,dest)
        await db.commit() # never hold transaction locks during external HTTP
        try:
            offers=await PROVIDERS[supplier.code].search(brand.name,p.article,config)
        except Exception:
            raise HTTPException(503,'Supplier verification temporarily unavailable') from None
        match=next((o for o in offers if o.key==item.external_key and
                    o.warehouse==detail.warehouse and exact(o,brand.name,p.article)),None)
        if (not match or match.qty is None or match.qty<qty or
            qty<match.minimum or qty%max(1,match.multiple) or
            match.price > snap.purchase_price):
            continue
        return option,snap
    raise HTTPException(409,'Supplier availability or price changed; refresh the offers')
