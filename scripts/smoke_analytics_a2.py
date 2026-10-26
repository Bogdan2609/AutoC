"""A2 read-only production smoke: fixed PostgreSQL snapshot, no purchases or banking."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.modules.analytics.a2 import customer_activity, product_ranking, supplier_quality, trends
from app.modules.analytics.api import catalog_data, shadow_data


async def main():
    assert app.version == '0.25.1', app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision = (await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0016', revision
        now = datetime.now(timezone.utc)
        catalog = await catalog_data(s)
        trend = await trends(s, 30, 'week', now)
        prod = await product_ranking(s, 30, now=now)
        supplier = await supplier_quality(s, 30, now=now)
        customers = await customer_activity(s, 30, now=now)
        shadow = await shadow_data(s)
        assert trend['verified_revenue_rub'] is None and trend['verified_net_profit_rub'] is None
        assert prod['verified_profit_rub'] is None
        assert shadow['included_in_verified_financials'] is False
        assert customers['distinct_users_with_orders'] >= 0
        assert catalog['products_total'] >= 0
        await s.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        for path in ('/health/live', '/operator', '/static/operator.js', '/static/operator.css'):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.status_code)
        assert 'Аналитика бизнеса (A2)' in (await client.get('/operator')).text
        for path in ('/api/v1/backoffice/analytics/a2/trend',
                     '/api/v1/backoffice/analytics/a2/products',
                     '/api/v1/backoffice/analytics/a2/suppliers',
                     '/api/v1/backoffice/analytics/a2/customers',
                     '/api/v1/backoffice/analytics/a2/filters',
                     '/api/v1/backoffice/analytics/a2/export/products.csv',
                     '/api/v1/backoffice/analytics/a2/export/trend.xlsx'):
            response = await client.get(path)
            assert response.status_code == 403, (path, response.status_code)
    print(f"ANALYTICS A2 SMOKE PASS | alembic={revision} | products={catalog['products_total']} | suppliers={len(supplier['rows'])} | shadow_excluded=YES | API/auth=PASS")
    print('READ ONLY: no migrations, database writes, bank calls, live payments, supplier requests, procurement, payouts, F3/F4/F5 updates or verified revenue claims.')


if __name__ == '__main__':
    asyncio.run(main())
