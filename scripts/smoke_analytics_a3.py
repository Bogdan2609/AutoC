"""Read-only A3 PostgreSQL smoke. Uses one repeatable-read snapshot, never alters data."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import get_session_factory
from app.main import app
from app.modules.analytics.a3 import merchant_economics, quote_comparison, rounding_comparison
from app.modules.analytics.a2 import supplier_quality
from app.modules.analytics.api import catalog_data, shadow_data


async def main():
    assert app.version == '0.25.2', app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision = (await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision == '20261020_0016', revision
        now = datetime.now(timezone.utc)
        catalog = await catalog_data(s)
        logs = await supplier_quality(s, 30, now=now)
        quotes = await quote_comparison(s, 30, now=now)
        sellers = await merchant_economics(s, 30, now=now)
        shadow = await shadow_data(s)
        rounding = rounding_comparison(Decimal('1335'))
        assert rounding['exact_rub']['customer_gross'] == '1335.00'
        assert rounding['smart_up']['customer_gross'] == '1340.00'
        assert quotes['verified_current_stock'] is None and quotes['verified_procurement_price'] is None
        assert sellers['verified_revenue_rub'] is None and sellers['verified_profit_rub'] is None
        assert shadow['included_in_verified_financials'] is False
        await s.rollback()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as c:
        for route in ('/health/live','/operator','/static/operator.js','/static/operator.css'):
            response = await c.get(route)
            assert response.status_code == 200, (route, response.status_code)
        assert 'Аналитика бизнеса (A3)' in (await c.get('/operator')).text
        for route in ('/api/v1/backoffice/analytics/a3/supplier-quotes',
                      '/api/v1/backoffice/analytics/a3/merchants',
                      '/api/v1/backoffice/analytics/a3/rounding-preview',
                      '/api/v1/backoffice/analytics/a3/export/supplier-quotes.csv',
                      '/api/v1/backoffice/analytics/a3/export/merchants.xlsx'):
            response = await c.get(route)
            assert response.status_code == 403, (route, response.status_code)
    print(f"ANALYTICS A3 SMOKE PASS | alembic={revision} | products={catalog['products_total']} | suppliers={len(logs['rows'])} | historic_products={len(quotes['rows'])} | seller_rows={len(sellers['rows'])} | auth=PASS")
    print('READ ONLY: no migrations or DB writes, no real price updates, suppliers API, bank, payouts, procurement, F3/F4/F5 posts or verified revenue claims.')


if __name__ == '__main__':
    asyncio.run(main())
