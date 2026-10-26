import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.pricing.service import calculate_retail, get_active_policy
from app.modules.suppliers.models import OfferSnapshot, Supplier, SupplierItem
from app.modules.offer_engine.models import CacheState
from app.modules.offer_engine.service import lookup_key
from app.modules.suppliers.schemas import PublicOfferOut


async def list_product_offers(session: AsyncSession, product_id: uuid.UUID) -> list[PublicOfferOut]:
    policy = await get_active_policy(session)
    # Latest snapshot for each supplier item using deterministic order.
    from app.modules.catalog.models import Brand, Product
    product = await session.get(Product, product_id)
    brand = await session.get(Brand,product.brand_id) if product else None
    cached = (await session.scalars(select(CacheState).where(CacheState.product_id==product_id))).all()
    states = {x.supplier_code:x for x in cached if brand and x.lookup_key==lookup_key(brand.name,product.article,x.supplier_code)}
    items = (await session.execute(
        select(SupplierItem, Supplier.code)
        .join(Supplier, Supplier.id == SupplierItem.supplier_id)
        .where(SupplierItem.product_id == product_id, SupplierItem.match_status == "confirmed")
        .order_by(Supplier.code, SupplierItem.external_key)
    )).all()
    now = datetime.now(timezone.utc)
    max_age = get_settings().offer_max_age_seconds
    results = []
    for item, supplier_code in items:
        state=states.get(supplier_code)
        # A completed live search is authoritative about *which* offers still exist.
        # An absent offer must not remain purchasable merely because its old snapshot
        # falls within a generic presentation TTL. Historical snapshots stay in DB.
        if state and state.last_completed_job_id and item.last_search_token != state.last_completed_job_id:
            continue
        snapshot = await session.scalar(
            select(OfferSnapshot).where(OfferSnapshot.supplier_item_id == item.id,OfferSnapshot.supplier_account_id.is_(None))
            .order_by(desc(OfferSnapshot.observed_at), desc(OfferSnapshot.id)).limit(1)
        )
        if snapshot is None:
            continue
        observed = snapshot.observed_at.replace(tzinfo=timezone.utc) if snapshot.observed_at.tzinfo is None else snapshot.observed_at
        age = (now - observed).total_seconds()
        fresh = 0 <= age <= max_age
        retail = (calculate_retail(snapshot.purchase_price, policy.markup_percent)
                  if policy is not None and fresh and snapshot.quantity != 0 else None)
        results.append(PublicOfferOut(
            snapshot_id=snapshot.id, supplier_item_id=item.id, supplier_code=supplier_code, currency=snapshot.currency,
            retail_price=retail, quantity=snapshot.quantity, lead_time_days=snapshot.lead_time_days,
            observed_at=observed, fresh=fresh,
        ))
    return results
