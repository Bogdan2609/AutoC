"""Read-only deployment smoke for enum UX R5; no writes or supplier HTTP."""
import asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.core.db import get_session_factory
from app.main import app

async def main():
    assert app.version == '0.23.2', app.version
    async with get_session_factory()() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        rev = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert rev == '20261020_0011', rev
        count = (await db.execute(text('SELECT count(*) FROM catalog_attribute_definitions'))).scalar_one()
        await db.rollback()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
        for p in ('/health/live','/operator','/static/catalog_editor.js'):
            r=await client.get(p)
            assert r.status_code==200,(p,r.status_code)
        html=(await client.get('/operator')).text
        assert '/static/catalog_editor.js?v=0235' in html
        js=(await client.get('/static/catalog_editor.js')).text
        for token in ('observed_values_truncated','suggestedOptions','Список значений'):
            assert token in js,token
        unauth=await client.get('/api/v1/backoffice/catalog-editor/attributes/00000000-0000-0000-0000-000000000000/usage')
        assert unauth.status_code == 403,unauth.status_code
    print(f'R5 ENUM SMOKE PASS | alembic={rev} | attributes={count} | UI/GET=PASS')
    print('READ ONLY: no migrations, catalog writes, supplier calls, purchases or payments.')
if __name__=='__main__':asyncio.run(main())
