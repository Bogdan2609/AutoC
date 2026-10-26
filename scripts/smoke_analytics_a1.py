"""A1 READ ONLY production smoke: calls analytics SELECTs inside read-only PG transaction.

Do not print credentials, order-level details, customer data or banking refs.
"""
import asyncio
from sqlalchemy import text
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.db import get_session_factory
from app.modules.analytics.api import catalog_data, orders_data, shadow_data, suppliers_data
from datetime import datetime, timezone

async def main():
    assert app.version == '0.25.0', app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION READ ONLY'))
        revision = (await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0016', revision
        now = datetime.now(timezone.utc)
        catalog = await catalog_data(s)
        operations = await orders_data(s, 30, now)
        suppliers = await suppliers_data(s, 30, now)
        shadow = await shadow_data(s)
        assert operations['revenue_verified_rub'] is None
        assert operations['net_profit_verified_rub'] is None
        assert shadow['included_in_verified_financials'] is False
        assert catalog['products_total'] >= 0
        await s.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        for path in ('/health/live', '/operator', '/static/operator.js'):
            r = await client.get(path)
            assert r.status_code == 200, (path, r.status_code)
        assert 'data-view="analytics"' in (await client.get('/operator')).text
        assert 'async function analytics()' in (await client.get('/static/operator.js')).text
        for path in ('/api/v1/backoffice/analytics/overview',
                     '/api/v1/backoffice/analytics/order-trend'):
            r = await client.get(path)
            assert r.status_code == 403, (path, r.status_code)
    print(f"ANALYTICS A1 SMOKE PASS | alembic={revision} | products={catalog['products_total']} | suppliers={len(suppliers['suppliers'])} | F4_shadow_payments={shadow['f4']['simulated_payments']} | UI/auth=PASS")
    print('READ ONLY: no migrations, database writes, live prices, orders, bank, F3/F4/F5, supplier requests, purchases or payouts.')

if __name__ == '__main__':
    asyncio.run(main())
