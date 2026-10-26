"""On-demand discovery of unknown SKUs, with strict input and rate budgets.

Only exact brand + article triggers external reads. Unknown keys never create
catalog records until a supplier returns a matching part. The worker does not
create orders, and there is no outbound HTTP in the FastAPI request process.
"""
import hashlib
import re
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.modules.accounts.models import utc
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import make_slug, normalize_article, normalize_brand
from app.modules.commerce.models import Job
from app.modules.offer_engine.models import CacheState, SupplierRuntime
from app.modules.offer_engine.service import (
    CODES, aware, credentials_ready, lookup_key, runtime,
)

# Strict exact-SKU intent. Generic natural-language searches stay local.
BRAND_RE = re.compile(r"^[\w .&+\-]{2,60}$", re.UNICODE)
ARTICLE_RE = re.compile(r"^[\w ./\-]{4,70}$", re.UNICODE)
SIMPLE_QUERY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9.&+-]{1,39})\s+([A-Za-z0-9 ./_-]{4,70})$")


def precise_query(text: str):
    """Conservative auto-detection; separate form fields cover other brands."""
    match = SIMPLE_QUERY_RE.fullmatch(text.strip())
    if not match or not any(ch.isdigit() for ch in match.group(2)):
        return None
    try:
        return validate_pair(match.group(1), match.group(2))
    except HTTPException:
        return None


def validate_pair(brand: str, article: str):
    brand = brand.strip()
    article = article.strip()
    if not BRAND_RE.fullmatch(brand) or not ARTICLE_RE.fullmatch(article):
        raise HTTPException(422, 'Укажите конкретный бренд и артикул без управляющих символов')
    normalized = normalize_article(article)
    if len(normalized) < 4 or len(normalized) > 70 or len(normalize_brand(brand)) > 60:
        raise HTTPException(422, 'Недопустимый артикул')
    if not any(ch.isdigit() for ch in normalized):
        # Many SKUs are numeric/alphanumeric. Letter-only vendor SKUs can still
        # be imported through admin but cannot trigger unauthenticated searches.
        raise HTTPException(422, 'Для внешнего поиска артикул должен содержать цифру')
    return brand, article


# Atomic fixed windows (IP minute + IP hour + global minute). By testing all
# keys first, denied requests consume no quota. Only *new* lookups are charged.
_BUDGET_LUA = """
local limits = {tonumber(ARGV[1]),tonumber(ARGV[2]),tonumber(ARGV[3])}
for i=1,3 do
 if tonumber(redis.call('GET',KEYS[i]) or '0') >= limits[i] then return 0 end
end
for i=1,3 do
 local n=redis.call('INCR',KEYS[i]); if n == 1 then redis.call('EXPIRE',KEYS[i],tonumber(ARGV[i+3])) end
end
return 1
"""


async def claim_public_budget(client_ip: str):
    """Fail closed if Redis is unavailable; never let an outage flood APIs."""
    s = get_settings()
    if s.app_env == 'test':
        return
    from app.modules.offer_engine.cache import redis_client
    digest = hashlib.sha256(client_ip.encode('utf-8')).hexdigest()[:24]
    now = int(utc().timestamp())
    keys = [f'apv2:discovery:ip:m:{digest}:{now//60}',
            f'apv2:discovery:ip:h:{digest}:{now//3600}',
            f'apv2:discovery:global:m:{now//60}']
    try:
        client = await redis_client()
        if client is None:
            raise RuntimeError('Redis quota client unavailable')
        result = await client.eval(_BUDGET_LUA, 3, *keys,
            s.discovery_ip_per_minute, s.discovery_ip_per_hour, s.discovery_global_per_minute,
            120, 7200, 120)
    except Exception as exc:
        raise HTTPException(503, 'Поиск у поставщиков временно недоступен') from exc
    if result != 1:
        raise HTTPException(429, 'Слишком много новых поисков. Повторите позднее',
                            headers={'Retry-After': '60'})


async def claim_supplier_budget(code: str, account_id: str | None = None, account_limit: int | None = None):
    """Distributed hard cap on outbound supplier traffic across worker replicas."""
    s = get_settings()
    if s.app_env == 'test':
        return True
    from app.modules.offer_engine.cache import redis_client
    now = int(utc().timestamp())
    key = f'apv2:supplier:quota:{code}:{now//3600}'
    script = """
    local provider=tonumber(redis.call('GET',KEYS[1]) or '0')
    local account=tonumber(redis.call('GET',KEYS[2]) or '0')
    if provider>=tonumber(ARGV[1]) or account>=tonumber(ARGV[2]) then return 0 end
    for i=1,2 do
        local n=redis.call('INCR',KEYS[i])
        if n==1 then redis.call('EXPIRE',KEYS[i],7200) end
    end
    return 1
    """
    try:
        client = await redis_client()
        if client is None:
            return False
        account_key= f'apv2:supplier:account:{account_id or "legacy:"+code}:{now//3600}'
        value = await client.eval(script, 2, key, account_key,
                                  s.supplier_requests_per_hour, account_limit or s.supplier_requests_per_hour)
        return value == 1
    except Exception:
        return False  # do not contact supplier if quota cannot be enforced


async def current_product(db, brand: str, article: str):
    b = await db.scalar(select(Brand).where(Brand.name_norm == normalize_brand(brand)))
    if not b:
        return None
    return await db.scalar(select(Product).where(
        Product.brand_id == b.id, Product.article_norm == normalize_article(article)))


@asynccontextmanager
async def nested(db):
    if hasattr(db, 'begin_nested'):
        async with db.begin_nested():
            yield
    else:
        # The synchronous SQLite facade is used by fast unit tests only.
        yield


async def create_verified_product(db, brand: str, article: str):
    """Called only after a read-only supplier response passed exact matching.

    Automatically discovered identities are always PRIVATE DRAFTS. Unique constraints and savepoints
    make simultaneous replies from multiple suppliers safe.
    """
    item = await current_product(db, brand, article)
    if item is not None:
        return item
    b = await db.scalar(select(Brand).where(Brand.name_norm == normalize_brand(brand)))
    if b is None:
        normalized = normalize_brand(brand)
        short = hashlib.sha256(normalized.encode()).hexdigest()[:12]
        slug = re.sub(r'[^a-z0-9-]+', '-', normalized.lower()).strip('-')[:55] or 'brand'
        slug = f'{slug}-{short}'
        try:
            async with nested(db):
                b = Brand(name=brand, name_norm=normalized, slug=slug)
                db.add(b)
                await db.flush()
        except IntegrityError:
            b = await db.scalar(select(Brand).where(Brand.name_norm == normalized))
            if b is None:
                raise
    item = await db.scalar(select(Product).where(
        Product.brand_id == b.id, Product.article_norm == normalize_article(article)))
    if item is not None:
        return item
    normalized_article = normalize_article(article)
    # Slug stays stable even if we enrich the placeholder name later.
    slug = make_slug(b.slug, normalized_article)
    try:
        async with nested(db):
            item = Product(brand_id=b.id, article=article, article_norm=normalized_article,
                           name=f'{b.name} {article}', status='draft', seo_noindex=True, slug=slug)
            db.add(item)
            await db.flush()
    except IntegrityError:
        item = await db.scalar(select(Product).where(
            Product.brand_id == b.id, Product.article_norm == normalized_article))
        if item is None:
            raise
    return item


async def status(db, brand: str, article: str):
    brand, article = validate_pair(brand, article)
    p = await current_product(db, brand, article)
    visible = bool(p and p.status == 'published')
    values = []
    from app.modules.marketplace.account_search import available_destinations,account_key
    for code in CODES:
        rows,configured = await available_destinations(db,code)
        if configured:
            account_states=[]
            for account,destination,node in rows:
                key=account_key(brand,article,account.id,destination.id)
                st=await db.scalar(select(CacheState).where(
                    CacheState.supplier_code==code,CacheState.lookup_key==key))
                account_states.append(st)
            statuses=[x.status for x in account_states if x]
            resolved='refreshing' if 'refreshing' in statuses else (
                'ready' if 'ready' in statuses else statuses[0] if statuses else 'not_requested')
            values.append({'supplier':code,'status':resolved,
                           'offers':sum(x.result_count for x in account_states if x),
                           'accounts':len(rows)})
            continue
        cfg = await db.get(SupplierRuntime, code)
        key = lookup_key(brand, article, code)
        state = await db.scalar(select(CacheState).where(
            CacheState.supplier_code == code, CacheState.lookup_key == key))
        operational = bool(cfg and cfg.enabled and get_settings().supplier_api_enabled and credentials_ready(code))
        values.append({'supplier': code,
                       'status': (state.status if state else 'not_requested') if operational else 'disabled',
                       'offers': state.result_count if state else 0,
                       })
    return {'brand': brand, 'article': article,
            'product_url': f'/product/{quote(p.slug)}' if visible else None,
            'suppliers': values,
            'pending': any(v['status'] in ('pending', 'refreshing') for v in values)}


async def enqueue_discovery(db, brand: str, article: str, client_ip: str, city: str | None = None):
    """Discover one exact SKU. Enqueue at most one job per supplier/cache key/TTL."""
    brand, article = validate_pair(brand, article)
    s = get_settings()
    now = utc()
    product = await current_product(db, brand, article)
    from app.modules.marketplace.account_search import available_destinations, enqueue_accounts
    eligible = []
    for code in CODES:
        _, configured = await available_destinations(db,code,city=city)
        if configured:
            continue  # do not accidentally query a legacy global account
        cfg = await runtime(db, code)
        if not cfg.enabled or not (s.supplier_api_enabled and credentials_ready(code)):
            continue
        key = lookup_key(brand, article, code)
        state = await db.scalar(select(CacheState).where(
            CacheState.supplier_code == code, CacheState.lookup_key == key).with_for_update())
        if state and aware(state.expires_at) > now:
            continue
        eligible.append((code, key, state))
    from app.modules.marketplace.account_search import pending_account_refreshes
    account_count=await pending_account_refreshes(db, brand, article, city=city)
    if eligible or account_count:
        # DB backpressure: avoid accumulating unlimited work under mass scanning.
        queued = await db.scalar(select(func.count()).select_from(Job).where(
            Job.kind == 'search', Job.status.in_(['pending', 'running'])))
        if int(queued or 0) + len(eligible) + account_count > s.discovery_max_pending_jobs:
            raise HTTPException(503, 'Очередь поиска перегружена, повторите позднее')
        await claim_public_budget(client_ip)
    for code, key, state in eligible:
        if state is None:
            try:
                async with nested(db):
                    state = CacheState(supplier_code=code, lookup_key=key,
                                       product_id=product.id if product else None,
                                       status='pending', expires_at=now)
                    db.add(state)
                    await db.flush()
            except (IntegrityError, AttributeError):
                state = await db.scalar(select(CacheState).where(
                    CacheState.supplier_code == code, CacheState.lookup_key == key).with_for_update())
        if state is None or aware(state.expires_at) > now:
            continue
        state.status = 'refreshing'
        state.product_id = product.id if product else None
        state.last_requested_at = now
        state.expires_at = now + timedelta(minutes=5)
        db.add(Job(dedup_key='discovery:' + str(uuid.uuid4()), kind='search',
                   payload={'supplier': code, 'brand': brand, 'article': article,
                            'product_id': str(product.id) if product else None,
                            'key': key, 'requested_at': now.isoformat(), 'discovery': True}))
    await enqueue_accounts(db,brand,article,product.id if product else None,city=city)
    await db.commit()
    return await status(db, brand, article)
