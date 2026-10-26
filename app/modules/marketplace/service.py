"""Tenant-safe stock, routing and seller-offer reads.

No HTTP to suppliers, financial operations, or unverified reservations here.
"""
from decimal import Decimal
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import and_, select, update, func

from app.modules.marketplace.offer_rank import fulfillment_rank
from app.modules.marketplace.models import (
    Merchant, Store, StoreMembership, FulfillmentNode, DeliveryRoute, SellerOffer,
    UsedPart, InventoryBalance, InventoryMovement,
)


async def assert_store_access(db, store_id, user_id, allowed=('owner','manager')):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(404, 'Store not found')
    membership = await db.scalar(select(StoreMembership).where(
        StoreMembership.store_id == store_id, StoreMembership.user_id == user_id,
        StoreMembership.role.in_(allowed)))
    if not membership:
        raise HTTPException(403, 'Store permission denied')
    return store


async def store_node(db, merchant_id, node_id):
    node = await db.get(FulfillmentNode, node_id)
    if not node or node.merchant_id != merchant_id or not node.active:
        raise HTTPException(409, 'Node unavailable for this merchant')
    return node


async def offer_check(db, offer_id, active=True):
    offer = await db.get(SellerOffer, offer_id)
    if not offer or (active and offer.status != 'active'):
        raise HTTPException(404, 'Offer not found')
    store = await db.get(Store, offer.store_id)
    merchant = await db.get(Merchant, offer.merchant_id)
    if not store or store.status != 'active' or not merchant or merchant.status != 'active':
        raise HTTPException(409, 'Store not active')
    if offer.used_part_id:
        used = await db.get(UsedPart, offer.used_part_id)
        if not used or used.status != 'published':
            raise HTTPException(409, 'Used part not published')
    return offer


async def options_for_offer(db, offer, pickup_node_id=None):
    """Local available stock plus optional 1-2 hop configured pickup routes."""
    from app.modules.marketplace.routing import routes_to_pickup
    stmt = select(InventoryBalance, FulfillmentNode).join(
        FulfillmentNode, InventoryBalance.node_id == FulfillmentNode.id).where(
        InventoryBalance.seller_offer_id == offer.id,
        FulfillmentNode.active.is_(True), FulfillmentNode.merchant_id == offer.merchant_id)
    balances = (await db.execute(stmt)).all()
    available=[]
    for bal, origin in balances:
        count=bal.on_hand-bal.reserved
        if count<=0:continue
        if pickup_node_id is None:
            available.append({'node_id':str(origin.id),'node':origin.name,'city':origin.city,
                 'stock':count,'days':0,'fee':'0.00',
                 'pickup_node_id':str(origin.id) if origin.kind in ('pickup','warehouse_pickup') else None,
                 'route_node_ids':[str(origin.id)]})
            continue
        paths=await routes_to_pickup(db,offer.merchant_id,origin.id,pickup_node_id)
        for path in paths:
            available.append({'node_id':str(origin.id),'node':origin.name,
                'city':path['city'],'stock':count,'days':path['days'],
                'fee':path['fee'],'pickup_node_id':path['pickup_node_id'],
                'route_node_ids':path['route_node_ids']})
    return sorted(available,key=fulfillment_rank)


async def reserve_stock(db, balance_id: UUID, quantity: int, key: str):
    """Atomically reserves on PostgreSQL; idempotency key protects retries.

    InventoryMovement is an audit record. Callers must own the transaction.
    This is not exposed as a public checkout until expiry/release is supported.
    """
    if quantity < 1 or quantity > 1000 or not key:
        raise ValueError('Invalid reservation')
    existing = await db.scalar(select(InventoryMovement).where(InventoryMovement.idempotency_key == key))
    if existing is not None:
        if existing.balance_id != balance_id or existing.delta_reserved != quantity:
            raise ValueError('Idempotency key collision')
        return existing
    result = await db.scalar(update(InventoryBalance).where(
        InventoryBalance.id == balance_id,
        InventoryBalance.on_hand - InventoryBalance.reserved >= quantity,
    ).values(reserved=InventoryBalance.reserved + quantity).returning(InventoryBalance.id))
    if result is None:
        raise HTTPException(409, 'Not enough available stock')
    movement = InventoryMovement(balance_id=balance_id,delta_reserved=quantity,
                                 idempotency_key=key,reason='reserve')
    db.add(movement)
    await db.flush()
    return movement


async def verified_supplier_quotes(db, product, pickup_node_id=None, city=None):
    """Only snapshots produced by the last SUCCESSFUL account+destination search.

    Legacy snapshots without an owning search-job marker are intentionally not
    published after v0.15 deployment; refresh the configured account once.
    This prevents an offer missing from the supplier's newest result from
    reappearing just because its older snapshot is still within a generic TTL.
    """
    from app.core.config import get_settings
    from app.modules.marketplace.models import SupplierAccount, SupplierDestination
    from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot
    from app.modules.offer_engine.models import CacheState, OfferDetail
    from app.modules.offer_engine.service import aware
    from app.modules.marketplace.account_search import account_key
    from app.modules.marketplace.routing import routes_to_pickup, describe_supplier_route
    from app.modules.catalog.models import Brand
    from app.modules.pricing.service import get_active_policy
    from app.modules.pricing.resolver import resolve_supplier_quote
    from app.modules.accounts.models import utc

    brand=await db.get(Brand,product.brand_id)
    if not brand:return []
    policy=await get_active_policy(db)
    items=(await db.execute(select(SupplierAccount,SupplierDestination,FulfillmentNode,Supplier)
        .join(SupplierDestination,SupplierDestination.account_id==SupplierAccount.id)
        .join(FulfillmentNode,FulfillmentNode.id==SupplierDestination.node_id)
        .join(Supplier,Supplier.id==SupplierAccount.supplier_id)
        .where(SupplierAccount.enabled.is_(True),SupplierAccount.read_enabled.is_(True),
               SupplierDestination.enabled.is_(True),SupplierDestination.verified.is_(True),
               FulfillmentNode.active.is_(True),
               FulfillmentNode.merchant_id==SupplierAccount.merchant_id).limit(150))).all()
    # Batch merchant and cache-state reads rather than per-destination N+1 lookups.
    merchant_ids={account.merchant_id for account,_,_,_ in items}
    merchants={m.id:m for m in (await db.scalars(select(Merchant).where(
        Merchant.id.in_(merchant_ids),Merchant.status=='active'))).all()} if merchant_ids else {}
    keys={account_key(brand.name,product.article,account.id,destination.id)
          for account,destination,_,_ in items if account.merchant_id in merchants}
    states={(x.supplier_code,x.lookup_key):x for x in (await db.scalars(
        select(CacheState).where(CacheState.lookup_key.in_(keys)))).all()} if keys else {}
    eligible=[]
    for account,destination,node,supplier in items:
        merchant=merchants.get(account.merchant_id)
        if merchant is None:continue
        state=states.get((supplier.code,account_key(brand.name,product.article,account.id,destination.id)))
        if state and state.status in ('ready','refreshing') and state.last_completed_job_id:
            eligible.append((account,destination,node,supplier,merchant,state))
    # Preserve the 250-most-recent-snapshots-per-destination semantics with a
    # SQL window, while reading all eligible accounts in a single query.
    snapshots_by_scope={}
    if eligible:
        account_ids={a.id for a,_,_,_,_,_ in eligible}
        destination_ids={d.id for _,d,_,_,_,_ in eligible}
        job_ids={st.last_completed_job_id for *_,st in eligible}
        ranked=(select(OfferSnapshot.id.label('snapshot_id'),
            func.row_number().over(partition_by=(OfferSnapshot.supplier_account_id,
               OfferSnapshot.supplier_destination_id),
               order_by=(OfferSnapshot.observed_at.desc(),OfferSnapshot.id.desc())).label('rn'))
            .join(SupplierItem,SupplierItem.id==OfferSnapshot.supplier_item_id)
            .join(OfferDetail,OfferDetail.snapshot_id==OfferSnapshot.id)
            .join(SupplierAccount,SupplierAccount.id==OfferSnapshot.supplier_account_id)
            .where(SupplierItem.supplier_id==SupplierAccount.supplier_id,
                   SupplierItem.product_id==product.id,SupplierItem.match_status=='confirmed',
                   OfferSnapshot.supplier_account_id.in_(account_ids),
                   OfferSnapshot.supplier_destination_id.in_(destination_ids),
                   OfferDetail.verified_live.is_(True),
                   OfferDetail.metadata_public['_search_job_id'].as_string().in_(job_ids))).subquery()
        snaps=(await db.execute(select(OfferSnapshot,OfferDetail)
            .join(OfferDetail,OfferDetail.snapshot_id==OfferSnapshot.id)
            .join(ranked,ranked.c.snapshot_id==OfferSnapshot.id)
            .where(ranked.c.rn<=250))).all()
        for snap,detail in snaps:
            marker=detail.metadata_public.get('_search_job_id') if isinstance(detail.metadata_public,dict) else None
            snapshots_by_scope.setdefault((snap.supplier_account_id,snap.supplier_destination_id,marker),[]).append((snap,detail))
    # Approved purchase-cost rules can also replace the legacy markup in real
    # API snapshot *previews*, when its exact account/receiving/warehouse scope
    # is mapped. Missing/unknown rules retain a clearly marked legacy preview;
    # neither kind is a checkout price without independent live validation.
    from app.modules.supplier_feeds.models import FeedSource,FeedWarehouseMap
    from app.modules.supplier_feeds.pricing import active_rules_for
    scopes={(a.id,d.id,detail.warehouse)
            for a,d,_,_,_,state in eligible
            for snap,detail in snapshots_by_scope.get((a.id,d.id,state.last_completed_job_id),[])}
    commercial_rules=await active_rules_for(db,scopes)
    source_codes={supplier.code for _,_,_,supplier,_,_ in eligible}
    feed_sources={src.code:src for src in (await db.scalars(select(FeedSource).where(
        FeedSource.code.in_(source_codes)))).all()} if source_codes else {}
    approved_maps={(m.account_id,m.destination_id,m.warehouse_code) for m in (await db.scalars(
        select(FeedWarehouseMap).where(FeedWarehouseMap.approved.is_(True),
         FeedWarehouseMap.account_id.in_({a for a,d,w in scopes})))).all()} if scopes else set()
    out=[]
    now=utc()
    max_age=get_settings().offer_max_age_seconds
    for account,destination,node,supplier,merchant,state in eligible:
        if pickup_node_id:
            routes=await routes_to_pickup(db,account.merchant_id,node.id,pickup_node_id)
            if not routes:continue
        else:
            routes=[None]
        # Distinct account+destination+job scoped offers, not a global item token.
        snapshots=snapshots_by_scope.get((account.id,destination.id,state.last_completed_job_id),[])
        for snap,detail in snapshots:
            if snap.currency!='RUB' or snap.quantity is None or snap.quantity<=0:continue
            if not (0<=(now-aware(snap.observed_at)).total_seconds()<=max_age):continue
            stock_meta=detail.metadata_public if isinstance(detail.metadata_public,dict) else {}
            lower_bound=bool(stock_meta.get('stock_is_lower_bound',False))
            raw_stock=str(stock_meta.get('stock_raw',''))
            stock_display=raw_stock if lower_bound and raw_stock.startswith('>') else str(snap.quantity)
            scope=(account.id,destination.id,detail.warehouse)
            rule=commercial_rules.get(scope)
            price_info=resolve_supplier_quote(snap.purchase_price,legacy_policy=policy,
                commercial_rule=rule,feed_source=feed_sources.get(supplier.code),
                warehouse_map_approved=scope in approved_maps,tax_status=snap.tax_status)
            if price_info is None:
                continue  # No approved exact cost rule and no fallback policy.
            for route in routes:
                route_info=describe_supplier_route(snap,detail,node,route,
                    pickup_selected=pickup_node_id is not None)
                if city and route_info['city'].casefold()!=city.casefold():continue
                out.append({'offer_id':None,
                    'supplier_account_id':str(account.id),
                    'supplier_destination_id':str(destination.id),
                    'source_kind':'supplier_quote', 'store':merchant.name,
                    'merchant':merchant.name,'merchant_id':str(merchant.id),
                    'supplier_code':supplier.code,'snapshot_id':str(snap.id),
                    'warehouse_id':detail.warehouse,
                    'price':price_info['price'],
                    'pricing_basis':price_info['pricing_basis'],
                    'pricing_breakdown':price_info['pricing_breakdown'],
                    'price_policy_version':price_info['price_policy_version'],
                    'cart_enabled':False,'payment_enabled':False,
                    'currency':'RUB','requires_live_validation':True,
                    'fulfillment':[{'node_id':str(node.id),'node':node.name,
                        'stock':snap.quantity,'stock_display':stock_display,
                        'stock_is_lower_bound':lower_bound,**route_info}]})
    return out
