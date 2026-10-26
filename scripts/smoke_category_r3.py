"""Read-only post-migration smoke for v0.23 R3 category templates and facets."""
import asyncio

from sqlalchemy import text
from httpx import ASGITransport, AsyncClient
from app.core.db import get_session_factory
from app.main import app


async def main():
    assert app.version == '0.23.1', f'Unexpected app: {app.version}'
    async with get_session_factory()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0010',f'Unexpected migration revision {revision}'
        cols = set((await db.execute(text("""SELECT column_name FROM information_schema.columns
            WHERE table_name='catalog_category_attributes'"""))).scalars())
        assert {'show_in_filters','sort_order','group_name'}.issubset(cols)
        categories = (await db.execute(text('SELECT count(*) FROM catalog_categories'))).scalar_one()
        assert categories >= 0
        await db.rollback()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
        for path in ('/health/live','/operator','/static/catalog_editor.js',
                     '/static/category_facets.js','/static/category_facets.css'):
            response = await client.get(path)
            assert response.status_code == 200,(path,response.status_code,response.text[:120])
        response = await client.get('/api/v1/backoffice/catalog-editor/products/'+
                                    '00000000-0000-0000-0000-000000000000/attribute-form')
        assert response.status_code == 403, 'Category templates must require operator authentication'
    print(f'R3 SMOKE PASS | alembic={revision} | categories={categories} | http_get=6')
    print('NO catalog writes, supplier API requests, purchasing, payments or orders.')


if __name__=='__main__':
    asyncio.run(main())
