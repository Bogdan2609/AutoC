"""Read-only smoke for Finance F1 installation, no supplier/payment/procurement calls."""
import asyncio
from sqlalchemy import text
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.db import get_session_factory

async def run():
    assert app.version == '0.24.0', app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION READ ONLY'))
        rev=(await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert rev=='20261020_0012', rev
        product_count=(await s.execute(text('SELECT count(*) FROM products'))).scalar_one()
        count=(await s.execute(text('SELECT count(*) FROM finance_legal_entities'))).scalar_one()
        assert count>=0
        await s.rollback()
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        for path in ['/health/live','/operator','/static/operator.js']:
            r=await c.get(path); assert r.status_code==200,(path,r.status_code)
        assert 'v0.24 F1' in (await c.get('/operator')).text
        r=await c.get('/api/v1/backoffice/finance/overview')
        assert r.status_code==403,('admin missing',r.status_code)
        r=await c.post('/api/v1/backoffice/finance/tax/preview',json={
          'gross_amount':'1220.00','operation_date':'2026-10-20','sale_model':'OWN',
          'operation_kind':'GOODS','vat_scheme':'STANDARD'})
        assert r.status_code==403,('admin missing',r.status_code)
    print(f'FINANCE F1 SMOKE PASS | alembic={rev} | products={product_count} | legal_entities={count} | operator/auth=PASS')
    print('READ ONLY: no catalog updates, supplier requests, purchases, money movement, payments or financial profiles activated.')

if __name__=='__main__':asyncio.run(run())
