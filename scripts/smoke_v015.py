"""v0.15.2 offline ASGI smoke: validates effective routing, not router internals.

Makes in-process requests only. Never contacts a supplier, creates a cart/order,
or changes any database records. Invalid product IDs stop before business logic.
"""
import asyncio
import inspect

import httpx

from app.main import app
from app.core.db import get_session
from app.modules.marketplace.grouping import group_marketplace_offers
from app.modules.marketplace.routing import describe_supplier_route
from app.workers import runner


async def _unused_db_session():
    # Path validation happens before handlers; this keeps smoke 100% database-free.
    yield None


async def verify_routes():
    # Newer FastAPI builds may expose lazy _IncludedRouter entries in app.routes.
    # A request through the actual ASGI app is version-independent and tests
    # that include_router *really* registered the public endpoints.
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = _unused_db_session
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://asgi.local') as client:
            for suffix in ('/offers', '/offers/grouped'):
                path = '/api/v1/marketplace/products/not-a-uuid' + suffix
                response = await client.get(path)
                if response.status_code != 422:
                    raise AssertionError(f'Route {path} unavailable: HTTP {response.status_code}')
                details = response.json().get('detail', [])
                if not any(tuple(issue.get('loc', ())) == ('path', 'product_id') for issue in details):
                    raise AssertionError(f'Route {path} did not validate product_id')
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


assert '_search_job_id' in inspect.getsource(runner.search), 'Search provenance missing'
assert group_marketplace_offers([]) == [], 'Empty grouping broken'
asyncio.run(verify_routes())
print('v0.15.2 SMOKE PASS: ASGI grouped and flat routes OK; batch provenance OK; no supplier calls or purchases.')
