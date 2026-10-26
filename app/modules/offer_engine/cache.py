"""Redis L1 is dispensable; PostgreSQL remains the source of supplier offers.

The L1 has a deliberately short TTL and can be invalidated on any local mutation.
It never authorizes a checkout; cart/checkout verify the supplier independently.
"""
from uuid import UUID
from app.core.config import get_settings

PREFIX = 'apv2:offers:v04:'

_active_clients={}
# Redis.from_url creates a pool lazily; one client is reused per process.
def _make_client(url: str):
    from redis.asyncio import Redis
    if url not in _active_clients:
        _active_clients[url]=Redis.from_url(url,socket_connect_timeout=.3,socket_timeout=.4,
                          decode_responses=True,health_check_interval=30)
    return _active_clients[url]

async def redis_client():
    s=get_settings()
    if s.app_env=='test':return None
    try:return _make_client(s.redis_url)
    except Exception:return None

async def close_client():
    for client in list(_active_clients.values()):
        await client.aclose()
    _active_clients.clear()


async def load(product_id: UUID):
    client = await redis_client()
    if client is None:
        return None
    try:
        return await client.get(PREFIX + str(product_id))
    except Exception:
        return None

async def save(product_id: UUID, value: str):
    client = await redis_client()
    if client is None:
        return
    try:
        await client.set(PREFIX + str(product_id), value, ex=20)
    except Exception:
        pass

async def invalidate(product_id: UUID | None):
    if product_id is None:
        return
    client = await redis_client()
    if client is None:
        return
    try:
        await client.delete(PREFIX + str(product_id))
    except Exception:
        pass

async def invalidate_all_offers():
    """After global markup change: scan keys, don't use Redis KEYS or FLUSHDB."""
    client = await redis_client()
    if client is None:
        return
    try:
        batch=[]
        async for key in client.scan_iter(match=PREFIX+'*',count=200):
            batch.append(key)
            if len(batch)>=200:
                await client.delete(*batch)
                batch.clear()
        if batch:await client.delete(*batch)
    except Exception:
        pass

async def product_offers(session,product_id):
    """Shared L1 entrypoint for JSON API and server-rendered product page."""
    from pydantic import TypeAdapter
    from app.modules.suppliers.schemas import PublicOfferOut
    from app.modules.suppliers.service import list_product_offers
    adapter=TypeAdapter(list[PublicOfferOut])
    hit=await load(product_id)
    if hit is not None:
        try:
            return adapter.validate_json(hit)
        except Exception:
            pass
    offers=await list_product_offers(session,product_id)
    await save(product_id,adapter.dump_json(offers).decode('utf-8'))
    return offers
