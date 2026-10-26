"""Read-only v0.23.2 R4 installation smoke. Never mutates catalog."""
import asyncio

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app


async def main():
    assert app.version == '0.23.2', f'Unexpected app version {app.version}'
    async with get_session_factory()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0011', f'Unexpected Alembic {revision}'
        tables = set((await db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"))).scalars())
        assert 'catalog_attribute_code_history' in tables
        attributes = (await db.execute(text('SELECT count(*) FROM catalog_attribute_definitions'))).scalar_one()
        products = (await db.execute(text('SELECT count(*) FROM products'))).scalar_one()
        await db.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        paths = ('/health/live', '/operator', '/static/catalog_editor.js')
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 200,(path,response.status_code)
        js = (await client.get('/static/catalog_editor.js')).text
        for marker in ('delete-attribute','confirm_type_change','create-product-attribute'):
            assert marker in js,marker
        private = await client.get('/api/v1/backoffice/catalog-editor/attributes/00000000-0000-0000-0000-000000000000/usage')
        assert private.status_code == 403, 'Attribute edits require admin credentials'
    print(f'R4 SMOKE PASS | alembic={revision} | attributes={attributes} | products={products} | HTTP GET=4')
    print('READ ONLY: No catalog mutation, supplier API calls, orders, purchases, or payments.')


if __name__ == '__main__':
    asyncio.run(main())
