"""Read-only R7 upgrade smoke; no migrations, stock or supplier calls."""
import asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.main import app
from app.core.db import get_session_factory

async def main():
    assert app.version == '0.23.4', f'Wrong app version: {app.version}'
    async with get_session_factory()() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await session.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0011', revision
        products = (await session.execute(text('SELECT count(*) FROM products'))).scalar_one()
        await session.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        for url in ('/health/live','/operator','/static/catalog_editor.js','/static/operator.js'):
            r=await client.get(url)
            assert r.status_code == 200, (url,r.status_code)
        assert 'v0.23 R7' in (await client.get('/operator')).text
        js=(await client.get('/static/operator.js')).text
        assert 'Array.isArray(e.issues)' in js
        unauth=await client.get('/api/v1/backoffice/catalog-editor/products/00000000-0000-0000-0000-000000000000/readiness')
        assert unauth.status_code == 403, unauth.status_code
    print(f'R7 SMOKE PASS | alembic={revision} | products={products} | operator & readiness auth=PASS')
    print('READ ONLY: no migrations, catalog changes, supplier calls, payments, procurement or order changes.')

if __name__ == '__main__':
    asyncio.run(main())
