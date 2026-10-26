"""A3 read-only queries, seller separation, archive quote history and exported workbook."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User
from app.modules.catalog.models import Brand, Product
from app.modules.commerce.models import Order, OrderLine
from app.modules.marketplace.models import Merchant
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot
from app.modules.finance.payment_sim_models import ShadowPayment
from app.modules.analytics.a3 import rounding_comparison

ROOT = '/api/v1/backoffice/analytics/a3'


@pytest.mark.asyncio
async def test_a3_auth_empty_validations_exports(client, admin_headers):
    for path in ('/supplier-quotes', '/merchants', '/rounding-preview',
                 '/export/supplier-quotes.csv', '/export/merchants.xlsx'):
        assert (await client.get(ROOT+path)).status_code == 403
        r = await client.get(ROOT+path, headers=admin_headers)
        assert r.status_code == 200, (path, r.text)
    assert (await client.get(ROOT+'/supplier-quotes', headers=admin_headers)).json()['rows'] == []
    assert (await client.get(ROOT+'/merchants', headers=admin_headers)).json()['rows'] == []
    for path in ('/supplier-quotes?days=0', '/supplier-quotes?days=366',
                 '/supplier-quotes?article=x', '/rounding-preview?price=-2',
                 '/rounding-preview?price=1000001', '/merchants?merchant_id=broken'):
        assert (await client.get(ROOT+path, headers=admin_headers)).status_code == 422, path
    p = (await client.get(ROOT+'/rounding-preview?price=1335', headers=admin_headers)).json()
    assert p['exact_rub']['customer_gross'] == '1335.00'
    assert p['smart_up']['customer_gross'] == '1340.00'
    assert p['real_offer_changed'] is False and p['conversion_uplift'] is None
    xl = await client.get(ROOT+'/export/merchants.xlsx', headers=admin_headers)
    with ZipFile(BytesIO(xl.content)) as archive:
        assert archive.testzip() is None


def test_a3_rounding_is_simulation_without_agency_modification():
    r = rounding_comparison(Decimal('101'))
    assert r['status'] == 'SIMULATION' and r['difference_rub'] == '0.00'
    r = rounding_comparison(Decimal('501'))
    assert r['smart_up']['customer_gross'] == '505.00'


@pytest.mark.asyncio
async def test_a3_history_multi_supplier_and_merchant_ownership(client, admin_headers):
    async for s in app.dependency_overrides[get_session]():
        brand = Brand(id=uuid4(), name='TRIALLI', name_norm='trialli', slug='trialli')
        p = Product(id=uuid4(), brand_id=brand.id, article='CF705', article_norm='cf705',
                    name='Filter CF705', slug='trialli-cf705', status='published')
        u = User(id=uuid4(), email='a3@example.test', password_hash='unused')
        owner = Merchant(id=uuid4(), name='Owner', slug='a3-owner', is_platform_owner=True,status='active')
        partner = Merchant(id=uuid4(), name='Partner', slug='a3-partner', is_platform_owner=False,status='active')
        suppliers = [Supplier(id=uuid4(),code=code,name=code) for code in ('rossko','partkom')]
        [s.add(v) for v in [brand,p,u,owner,partner]+suppliers]
        await s.flush()
        for supplier, cost in zip(suppliers,('800.00','700.00')):
            item=SupplierItem(id=uuid4(),supplier_id=supplier.id,product_id=p.id,
                              external_key='CF705-'+supplier.code,raw_brand='TRIALLI',
                              raw_article='CF705',match_status='confirmed')
            s.add(item)
            await s.flush()
            s.add(OfferSnapshot(supplier_item_id=item.id,purchase_price=Decimal(cost),
                                currency='RUB',lead_time_days=2,quantity=7,
                                observed_at=datetime.now(timezone.utc)-timedelta(hours=1)))
        order = Order(id=uuid4(),user_id=u.id,idempotency_key='a3-quote',total=Decimal('2000'),
                      status='awaiting_sbp',contact='none',phone='none',shipping_address='none',
                      total_is_final=True)
        s.add(order);await s.flush()
        for merchant, retail, purchase in ((owner,'1200','800'),(partner,'800','500')):
            s.add(OrderLine(order_id=order.id,product_id=p.id,merchant_id=merchant.id,
                            quantity=1,title='Filter',supplier_code='rossko',
                            unit_retail=Decimal(retail),unit_purchase=Decimal(purchase)))
        await s.commit()
        break
    h = (await client.get(ROOT+'/supplier-quotes?article=CF705',headers=admin_headers)).json()
    assert h['rows'][0]['supplier_count']==2
    assert h['rows'][0]['historical_low_rub']=='700.00'
    assert h['rows'][0]['historical_high_rub']=='800.00'
    assert h['verified_current_stock'] is None
    eco=(await client.get(ROOT+'/merchants',headers=admin_headers)).json()
    a = {r['role']:r for r in eco['rows']}
    assert a['OWNER']['owner_buy_sell_spread_rub'] == '400.00'
    assert a['AGENCY_MERCHANT']['owner_buy_sell_spread_rub'] is None
    assert a['AGENCY_MERCHANT']['platform_agency_fee_rub'] is None
    assert a['AGENCY_MERCHANT']['verified_revenue_rub'] is None
    c=(await client.get(ROOT+'/export/supplier-quotes.csv?article=CF705',headers=admin_headers))
    assert c.status_code==200 and c.content.startswith(b'\xef\xbb\xbf')
    assert b'HISTORICAL_ONLY' in c.content
    assert (await client.get(ROOT+'/supplier-quotes?article=notfound',headers=admin_headers)).json()['rows']==[]
