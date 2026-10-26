"""Read-only production smoke for v0.23: never orders, payments, suppliers, or DB writes."""
import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.core.db import get_session_factory
from app.main import app


async def main():
    assert app.version == '0.23.0', f'Wrong app version {app.version}'
    async with get_session_factory()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0009', f'Wrong database revision {revision}'
        count = (await db.execute(text('SELECT count(*) FROM products'))).scalar_one()
        assert count >= 0
        changed = (await db.execute(text('SELECT count(*) FROM catalog_product_change_log'))).scalar_one()
        await db.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        routes = ['/health/live','/operator','/static/catalog_editor.js','/static/catalog_editor.css']
        for endpoint in routes:
            res = await client.get(endpoint)
            assert res.status_code == 200, (endpoint,res.status_code,res.text[:100])
            if endpoint == '/static/catalog_editor.js':
                for text_item in ('slug-availability','Товар и контент','Структура и связи','Цены и предложения'):
                    assert text_item in res.text, f'Missing editor section {text_item}'
    print(f'v0.23 SMOKE PASS | alembic={revision} | products={count} | product_edit_events={changed} | http_get=4')
    print('No supplier requests, procurement, payments, orders, or writes performed.')


if __name__=='__main__':
    asyncio.run(main())
