"""A2: authorization, filters, numerical integrity and safe downloads."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User
from app.modules.analytics.a2 import calendar_buckets, csv_response, period_label, safe_excel_text
from app.modules.catalog.models import Brand, Product
from app.modules.commerce.models import Order, OrderLine
from app.modules.marketplace.models import Merchant
from app.modules.finance.payment_sim_models import ShadowPayment

U = '/api/v1/backoffice/analytics/a2'


@pytest.mark.asyncio
async def test_a2_auth_empty_and_csv(client, admin_headers):
    for path in ('/trend', '/products', '/suppliers', '/filters', '/export/trend.csv', '/export/products.csv', '/export/trend.xlsx', '/export/products.xlsx'):
        assert (await client.get(U+path)).status_code == 403, path
        assert (await client.get(U+path, headers={'X-Admin-Token': 'invalid'})).status_code == 403
        r = await client.get(U+path, headers=admin_headers)
        assert r.status_code == 200, (path, r.text)
    t = (await client.get(U+'/trend?days=7&bucket=week', headers=admin_headers)).json()
    assert t['verified_revenue_rub'] is None and t['verified_net_profit_rub'] is None
    assert all(x['orders_created'] == 0 for x in t['series'])
    assert (await client.get(U+'/products', headers=admin_headers)).json()['rows'] == []
    assert (await client.get(U+'/suppliers', headers=admin_headers)).json()['rows'] == []
    csv_file = await client.get(U+'/export/products.csv', headers=admin_headers)
    assert csv_file.content.startswith(b'\xef\xbb\xbf') and b'NOT_FOR_FILING' not in csv_file.content
    assert 'attachment;' in csv_file.headers['content-disposition']
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree
    xl = await client.get(U+'/export/trend.xlsx?days=7', headers=admin_headers)
    assert xl.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    with ZipFile(BytesIO(xl.content)) as archive:
        assert archive.testzip() is None
        assert ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml')).tag.endswith('worksheet')


@pytest.mark.asyncio
async def test_a2_invalid_filters_do_not_run_queries(client, admin_headers):
    for path in ('/trend?days=0', '/trend?days=366', '/trend?bucket=quarter',
                 '/products?brand_id=bad', '/products?limit=51',
                 '/products?supplier_code='+'x'*41, '/suppliers?days=-1'):
        assert (await client.get(U+path, headers=admin_headers)).status_code == 422, path
    assert (await client.post(U+'/products', headers=admin_headers)).status_code == 405


@pytest.mark.asyncio
async def test_a2_order_line_safety_owner_only_and_final_orders(client, admin_headers):
    from uuid import uuid4
    async for s in app.dependency_overrides[get_session]():
        uid, bid, pid, own, third = [uuid4() for _ in range(5)]
        user = User(id=uid, email='a2@example.test', password_hash='unused')
        brand = Brand(id=bid, name='GATES', name_norm='gates', slug='gates')
        product = Product(id=pid, brand_id=bid, article='A2', article_norm='a2',
                          name='Belt', slug='a2-belt', status='published')
        primary = Merchant(id=own, name='Our shop', slug='our-shop', is_platform_owner=True, status='active')
        partner = Merchant(id=third, name='Partner', slug='a2-partner', is_platform_owner=False, status='active')
        s.add(user);s.add(brand);s.add(product);s.add(primary);s.add(partner)
        await s.flush()
        paid_ref = Order(id=uuid4(), user_id=uid, idempotency_key='a2-one',total=Decimal('1500'),
                         status='awaiting_sbp',contact='hidden',phone='hidden',shipping_address='hidden',
                         total_is_final=True)
        draft_ref = Order(id=uuid4(), user_id=uid, idempotency_key='a2-draft',total=Decimal('99999'),
                          status='quote_requested',contact='hidden',phone='hidden',shipping_address='hidden',
                          total_is_final=False)
        s.add(paid_ref);s.add(draft_ref);await s.flush()
        for o, m, unit in ((paid_ref,own,'1000'),(paid_ref,third,'500'),(draft_ref,own,'99999')):
            s.add(OrderLine(order_id=o.id, product_id=pid, merchant_id=m, quantity=1,
                            title='Belt', supplier_code='ROSSKO', unit_retail=Decimal(unit),
                            unit_purchase=Decimal('600'), status='awaiting_sbp'))
        await s.commit()
        break
    tr = (await client.get(U+'/trend?days=7', headers=admin_headers)).json()
    assert sum(Decimal(x['final_order_value_rub']) for x in tr['series']) == Decimal('1500')
    assert sum(x['orders_created'] for x in tr['series']) == 2
    p = (await client.get(U+'/products?days=7', headers=admin_headers)).json()
    row = p['rows'][0]
    assert row['order_line_value_rub'] == '1500.00'
    assert row['owner_spread_estimate_rub'] == '400.00'  # no partner contribution
    assert row['owner_spread_coverage_lines'] == 1
    assert p['verified_revenue_rub'] is None and p['verified_profit_rub'] is None
    filtered = (await client.get(U+f'/products?merchant_id={third}', headers=admin_headers)).json()
    assert filtered['rows'][0]['order_line_value_rub'] == '500.00'
    assert filtered['rows'][0]['owner_spread_estimate_rub'] is None
    brandless = (await client.get(U+f'/products?brand_id={uuid4()}', headers=admin_headers)).json()
    assert brandless['rows'] == []


@pytest.mark.asyncio
async def test_shadow_payment_does_not_change_a2_order_values(client, admin_headers):
    f4='/api/v1/backoffice/finance/payments-sim'
    p=await client.post(f4+'/payments',headers=admin_headers,json={
        'sim_order_ref':'SIM:ORDER:ANALYTICS-A2','idempotency_key':'SIM:A2:SHADOW',
        'amount':'999999.00','sale_model':'AGENCY'})
    assert p.status_code==201,p.text
    t=(await client.get(U+'/trend', headers=admin_headers)).json()
    assert all(Decimal(row['final_order_value_rub']) == 0 for row in t['series'])
    assert t['verified_revenue_rub'] is None


def test_period_grouping_and_excel_safe_csv():
    days=calendar_buckets(date(2026,10,1),date(2026,10,20),'week')
    assert days == ['2026-09-28','2026-10-05','2026-10-12','2026-10-19']
    assert period_label(date(2026,10,20),'month')=='2026-10'
    assert safe_excel_text('=HYPERLINK("evil")').startswith("'")
    assert safe_excel_text(' +2+2').startswith("'")
    assert safe_excel_text('\t=bad').startswith("'")
    assert safe_excel_text('TRIALLI CF705')=='TRIALLI CF705'
    assert safe_excel_text('-1335.12') == '-1335.12'
    result=csv_response(['Название'],[['=SUM(1,2)']], 'safe.csv')
    assert "'=SUM(1,2)" in result.body.decode('utf-8')
