"""Database-leased, demand-driven supplier searches with optional hot refresh.

Never call suppliers from an HTML/GET handler. Every network request runs in a
worker and gets its own SQLAlchemy session. Redis is not the queue of record.
"""
import hashlib
import uuid
from datetime import timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.modules.accounts.models import utc
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.commerce.models import Job
from app.modules.offer_engine.models import CacheState, SupplierRuntime

CODES = ('rossko','adeo','partkom','forum-auto')


def aware(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def lookup_key(brand, article, code):
    base = f'{normalize_brand(brand)}:{normalize_article(article)}'
    if code == 'rossko':
        s = get_settings()
        # Stable nonsecret fingerprint of customer delivery context; don't expose address IDs.
        context = f'{s.rossko_delivery_id}:{s.rossko_address_id}'
        marker = hashlib.sha256(context.encode()).hexdigest()[:14]
    else:
        marker = 'default'
    return f'{base}:{marker}'


def credentials_ready(code):
    s=get_settings()
    return {'rossko':bool(s.rossko_key1 and s.rossko_key2 and s.rossko_delivery_id),
            'partkom':bool(s.partkom_login and s.partkom_password),
            'adeo':bool(s.adeo_login and s.adeo_password),
            'forum-auto':bool(s.forum_login and s.forum_password)}[code]


async def runtime(db: AsyncSession, code: str):
    setting = await db.get(SupplierRuntime, code)
    if setting is not None:
        return setting
    if hasattr(db,'begin_nested'):
        try:
            async with db.begin_nested():
                setting = SupplierRuntime(code=code)
                db.add(setting)
                await db.flush()
            return setting
        except IntegrityError:
            return await db.get(SupplierRuntime,code)
    setting=SupplierRuntime(code=code)
    db.add(setting)
    await db.flush()
    return setting


async def enqueue_product(db: AsyncSession, product, brand, *, codes=None, force=False, demand=True):
    now = utc()
    out = []
    from app.modules.marketplace.account_search import available_destinations, enqueue_accounts
    for code in codes or CODES:
        _, configured = await available_destinations(db,code)
        if configured:
            continue
        if code not in CODES:
            continue
        cfg = await runtime(db, code)
        key = lookup_key(brand.name, product.article, code)
        state = await db.scalar(select(CacheState).where(
            CacheState.supplier_code == code, CacheState.lookup_key == key).with_for_update())
        if state is None:
            if hasattr(db,'begin_nested'):
                try:
                    async with db.begin_nested():
                        state=CacheState(supplier_code=code,lookup_key=key,product_id=product.id,
                                         expires_at=now,status='pending')
                        db.add(state)
                        await db.flush()
                except IntegrityError:
                    state=await db.scalar(select(CacheState).where(CacheState.supplier_code==code,
                        CacheState.lookup_key==key).with_for_update())
            else:
                state=CacheState(supplier_code=code,lookup_key=key,product_id=product.id,
                                 expires_at=now,status='pending')
                db.add(state)
                await db.flush()
        state.product_id = product.id
        # Demand must be measured from user requests, not from our own background refresh.
        # Count at most one view per minute, so refreshing a cold page doesn't last forever.
        if demand and (state.last_demand_at is None or
                       (now-aware(state.last_demand_at)).total_seconds()>=60):
            if state.last_demand_at is None or (now-aware(state.last_demand_at)).total_seconds()>1800:
                state.demand_count=0
            state.demand_count += 1
            state.last_demand_at=now
        s=get_settings()
        operational=bool(cfg.enabled and (s.app_env=='test' or (s.supplier_api_enabled and credentials_ready(code))))
        should = operational and (force or aware(state.expires_at) <= now)
        # Manual refreshes cannot flood the API or overwrite an active worker lease.
        if should and state.status == 'refreshing' and aware(state.expires_at) > now:
            should = False
        if should and force and state.last_requested_at and (now-aware(state.last_requested_at)).total_seconds()<30:
            should = False
        if should:
            job = Job(dedup_key='offer-refresh:'+str(uuid.uuid4()),kind='search',
                      payload={'supplier':code,'brand':brand.name,'article':product.article,
                               'product_id':str(product.id),'key':key,
                               'requested_at':now.isoformat()})
            db.add(job)
            state.status = 'refreshing'
            state.last_requested_at = now
            state.expires_at = now+timedelta(minutes=5)  # renewable lease on interrupted worker
        out.append({'supplier':code,'status':'disabled' if not operational else state.status,
                    'expires_at':state.expires_at.isoformat(),
                    'enqueued':bool(should)})
    out.extend(await enqueue_accounts(db,brand.name,product.article,product.id,force=force,codes=codes))
    await db.commit()
    return out


async def hot_refresh(db: AsyncSession, limit=12):
    """Only refresh products requested recently; no full-catalog supplier sweeps."""
    now = utc()
    states = (await db.scalars(select(CacheState).where(
        CacheState.expires_at <= now,
        CacheState.last_demand_at >= now-timedelta(minutes=30),
        CacheState.demand_count >= 2,
        CacheState.product_id.is_not(None))
        .order_by(CacheState.expires_at).limit(limit))).all()
    if not states:
        return 0
    from app.modules.catalog.models import Product,Brand
    seen = set()
    count = 0
    for state in states:
        if state.product_id in seen:
            continue
        seen.add(state.product_id)
        product=await db.get(Product,state.product_id)
        if product is None or product.status!='published':
            continue
        brand=await db.get(Brand,product.brand_id)
        if brand is None:
            continue
        await enqueue_product(db,product,brand,demand=False)
        count += 1
    return count
