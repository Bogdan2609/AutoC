"""R/O finance F2 smoke: database metadata + auth + deterministic local calculation."""
import asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from app.main import app
from app.core.db import get_session_factory
from app.modules.finance.pricing_engine import PricePreview,calculate

async def run():
    assert app.version=='0.24.1',app.version
    async with get_session_factory()() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        rev=(await session.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert rev=='20261020_0013',rev
        n=(await session.execute(text('SELECT count(*) FROM products'))).scalar_one()
        rules=(await session.execute(text('SELECT count(*) FROM finance_pricing_rules'))).scalar_one()
        fees=(await session.execute(text('SELECT count(*) FROM finance_fee_schedules'))).scalar_one()
        await session.rollback()
    r=calculate(PricePreview(operation_date='2026-10-20',sale_model='OWN',source_gross='1000',markup_percent='22',vat_scheme='STANDARD'))
    assert r['customer_gross']=='1220.00' and r['output_vat_estimate']=='220.00' and not r['production_approved']
    rounded=calculate(PricePreview(operation_date='2026-10-20',sale_model='OWN',source_gross='1000',markup_percent='33.5',rounding_mode='SMART_UP'))
    assert rounded['customer_gross']=='1340.00' and rounded['rounding_uplift']=='5.00'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        for path in ('/health/live','/operator','/static/operator.js'):
            x=await c.get(path);assert x.status_code==200,(path,x.status_code)
        assert 'v0.24 F2' in (await c.get('/operator')).text
        assert 'finance-pricing' in (await c.get('/static/operator.js')).text
        assert (await c.get('/api/v1/backoffice/finance/pricing/overview')).status_code==403
        assert (await c.post('/api/v1/backoffice/finance/pricing/preview',json={'operation_date':'2026-10-20','sale_model':'OWN','source_gross':'1000'})).status_code==403
    print(f'FINANCE F2 SMOKE PASS | alembic={rev} | products={n} | pricing_rules={rules} | fee_schedules={fees} | UI/auth=PASS')
    print('READ ONLY: no real prices/offers/orders modified, no supplier API, bank requests, payment, purchases or settlements.')

if __name__=='__main__':asyncio.run(run())
