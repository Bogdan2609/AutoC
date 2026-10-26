"""ASGI-only v0.17 smoke. No database, supplier HTTP, cart/order writes or payments."""
import asyncio
import httpx
from app.main import app
from app.core.db import get_session
from app.modules.marketplace.checkout_v017 import payload_hash
from app.modules.commerce.models import CartLine, Order, OrderLine

async def _no_db():
    yield None

async def check():
    old=app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session]=_no_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url='http://smoke.local',timeout=4) as c:
            checks=[('GET','/api/v1/marketplace/cart/v017/preview',None,401),
                    ('POST','/api/v1/marketplace/cart/quotes',{},401),
                    ('PATCH','/api/v1/marketplace/cart/v017/items/00000000-0000-4000-8000-000000000001',{},401),
                    ('POST','/api/v1/marketplace/cart/v017/submit',{},401),
                    ('GET','/api/v1/admin/marketplace/order-requests',None,403),
                    ('GET','/api/v1/marketplace/products/not-a-uuid/offers/grouped',None,422)]
            for method,path,data,expected in checks:
                result=await c.request(method,path,json=data)
                if result.status_code!=expected:
                    raise AssertionError(f'{method} {path}: expected {expected} got {result.status_code}')
    finally:
        if old is None:app.dependency_overrides.pop(get_session,None)
        else:app.dependency_overrides[get_session]=old

assert app.version in ('0.17.0','0.18.0','0.21.0','0.22.0')
assert 'product_id' in CartLine.__table__.c
assert 'quote_metadata' in CartLine.__table__.c
assert 'request_expires_at' in Order.__table__.c
assert 'commercial_snapshot' in OrderLine.__table__.c
assert payload_hash({'x':1})==payload_hash({'x':1})
asyncio.run(check())
print('v0.17 SMOKE PASS: ASGI cart/requests/admin routes, catalog snapshot & reservation schema; no network/purchases/payments/writes.')
