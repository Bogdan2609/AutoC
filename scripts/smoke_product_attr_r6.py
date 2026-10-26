"""v0.23.3 R6 smoke. Read-only SQL and in-process HTTP GET, no supplier traffic."""
import asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.main import app
from app.core.db import get_session_factory

async def main():
    assert app.version == '0.23.3', f'Unexpected app version: {app.version}'
    async with get_session_factory()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0011', revision
        attrs = (await db.execute(text('SELECT count(*) FROM catalog_attribute_definitions'))).scalar_one()
        products = (await db.execute(text('SELECT count(*) FROM products'))).scalar_one()
        synthetic = (await db.execute(text("SELECT count(*) FROM products p JOIN brands b ON b.id=p.brand_id WHERE b.name_norm='CATALOG FIXTURE' AND p.status='published'"))).scalar_one()
        belt_problem = (await db.execute(text("SELECT count(*) FROM products p JOIN brands b ON b.id=p.brand_id WHERE b.name_norm='GATES' AND p.article_norm='5631XS' AND NOT EXISTS (SELECT 1 FROM catalog_product_categories pc WHERE pc.product_id=p.id AND pc.is_primary=true)"))).scalar_one()
        await db.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        for url in ('/health/live','/operator','/static/catalog_editor.js'):
            response = await client.get(url)
            assert response.status_code == 200, (url,response.status_code)
        html = (await client.get('/operator')).text
        assert '/static/catalog_editor.js?v=0236' in html
        js = (await client.get('/static/catalog_editor.js')).text
        assert 'attribute-changes' in js and 'Удалить значение только у товара' in js
        r = await client.patch('/api/v1/backoffice/catalog-editor/products/00000000-0000-0000-0000-000000000000/attribute-changes',json={'changes':[]})
        assert r.status_code in (403,422), r.status_code
    print(f'R6 SMOKE PASS | alembic={revision} | products={products} | attributes={attrs} | HTTP GET=PASS')
    if synthetic: print(f'SAFETY WARN: {synthetic} legacy synthetic QA products are still published in database; public endpoints hide them. Review scripts.catalog_safety_r6 --plan')
    if belt_problem: print(f'SAFETY WARN: GATES 5631XS lacks a primary category; review scripts.catalog_safety_r6 --plan')
    print('READ ONLY: no catalog writes, supplier calls, purchases, payments, orders or migrations.')

if __name__ == '__main__':
    asyncio.run(main())
