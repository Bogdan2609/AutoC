"""ADEO quantity ranges: expose safe minimum and preserve >N notation."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from app.modules.offer_engine.providers import Adeo, parse_adeo_stock
from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import utc
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from app.modules.offer_engine.models import CacheState, OfferDetail
from app.modules.marketplace.account_search import account_key
from app.modules.pricing.models import PricePolicy
from app.modules.suppliers.models import SupplierItem, OfferSnapshot
from tests.test_catalog_integration import create_product
from tests.test_marketplace_foundation_v07 import new_merchant
from tests.test_v014_integration import new_node


@pytest.mark.parametrize('raw,quantity,is_lower', [
    ('>4', 5, True), ('>100', 101, True), (' > 4 ', 5, True),
    ('4', 4, False), ('0', 0, False), (' 9 ', 9, False),
    ('', None, False), ('many', None, False), ('>abc', None, False),
    ('>-4', None, False), ('>2147483647', None, False),
])
def test_adeo_stock_parsing(raw, quantity, is_lower):
    actual, meta = parse_adeo_stock(raw)
    assert actual == quantity
    assert meta['stock_is_lower_bound'] is is_lower
    assert meta['stock_raw'] == raw.strip()


@pytest.mark.asyncio
async def test_adeo_xml_parse_stocks_and_preserve_raw(monkeypatch):
    from app.modules.offer_engine import providers
    xml = '''<root><detail><producer>TRIALLI</producer><code>CF705</code><price>573.49</price><currency>руб</currency><rest>&gt;4</rest><b_id>A1</b_id><id>1</id><cella_id>2</cella_id><n_file>3</n_file><stock>Cella1465/10</stock><delivery>2</delivery><PercentRefuse>16</PercentRefuse></detail><detail><producer>TRIALLI</producer><code>CF705</code><price>573.49</price><currency>руб</currency><rest>&gt;100</rest><b_id>A2</b_id><id>5</id><cella_id>6</cella_id><n_file>7</n_file><stock>Cella952/1</stock><delivery>1</delivery></detail><detail><producer>TRIALLI</producer><code>CF705</code><price>573.49</price><currency>руб</currency><rest>8</rest><b_id>A3</b_id><id>8</id><cella_id>9</cella_id><n_file>10</n_file><stock>Cella001/1</stock><delivery>1</delivery></detail></root>'''
    class FakeHTTP:
        async def post(self, *args, **kwargs): return httpx.Response(200, content=xml.encode('utf8'), request=httpx.Request('POST','https://xml.adeo.pro/pricedetals2.php'))
    monkeypatch.setattr(providers, 'get_client', lambda: FakeHTTP())
    settings = SimpleNamespace(supplier_api_enabled=True, adeo_login='test', adeo_password='test')
    offers = await Adeo().search('TRIALLI', 'CF705', settings)
    assert [o.qty for o in offers] == [5, 101, 8]
    assert [o.public_meta['stock_raw'] for o in offers] == ['>4', '>100', '8']
    assert [o.public_meta['stock_is_lower_bound'] for o in offers] == [True, True, False]
    assert offers[0].public_meta['refusal_percent'] == '16'
    assert offers[0].minimum == 1
    assert offers[0].price == Decimal('573.49')


@pytest.mark.asyncio
async def test_public_api_marks_stock_as_lower_bound(client, admin_headers):
    brand, product = await create_product(client, admin_headers)
    published = await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    assert published.status_code == 200, published.text
    merchant = await new_merchant(client, admin_headers, 'Adeo Store', 'adeo-v0145-store')
    node = await new_node(client, admin_headers, merchant, 'adeo-v0145-node', 'warehouse', 'Самара')
    endpoint = '/api/v1/admin/marketplace'
    supplier_result = await client.post('/api/v1/backoffice/suppliers', headers=admin_headers,
                                        json={'code': 'adeo', 'name': 'ADEO'})
    assert supplier_result.status_code == 201, supplier_result.text
    supplier = supplier_result.json()['id']
    a = await client.post(endpoint+'/supplier-accounts', headers=admin_headers, json={
        'supplier_id': supplier, 'merchant_id': merchant, 'label': 'ADEO main',
        'credential_profile': 'default'})
    assert a.status_code == 201, a.text
    account = a.json()['id']
    d = await client.post(endpoint+'/supplier-destinations', headers=admin_headers, json={
        'account_id': account, 'node_id': node, 'external_address_id': 'A', 'external_delivery_id': 'B'})
    assert d.status_code == 201, d.text
    dest = d.json()['id']
    for url, body in [(endpoint+'/supplier-accounts/'+account, {'enabled':True,'read_enabled':True}),
                      (endpoint+'/supplier-destinations/'+dest, {'enabled':True,'verified':True})]:
        result = await client.patch(url, headers=admin_headers, json=body)
        assert result.status_code == 200, result.text
    async for db in app.dependency_overrides[get_session]():
        item=SupplierItem(supplier_id=UUID(supplier),product_id=UUID(product['id']),
                          external_key='a:b:c',raw_brand='BOSCH',raw_article='0986479088',
                          match_status='confirmed')
        db.add(item)
        await db.flush()
        snap=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
                           supplier_destination_id=UUID(dest),purchase_price=Decimal('573.49'),
                           quantity=5,lead_time_days=2)
        db.add(snap)
        await db.flush()
        db.add(OfferDetail(snapshot_id=snap.id,external_token='A1',warehouse='Cella1465/10',
                           verified_live=True,metadata_public={'stock_raw':'>4','stock_is_lower_bound':True,'_search_job_id':'test-v0145'}))
        db.add(CacheState(supplier_code='adeo',lookup_key=account_key('BOSCH','0986479088',
                            UUID(account),UUID(dest)),product_id=UUID(product['id']),status='ready',
                            last_completed_job_id='test-v0145',result_count=1,expires_at=utc()))
        db.add(PricePolicy(name='default',markup_percent=Decimal('20'),active=True))
        await db.commit()
    response=await client.get('/api/v1/marketplace/products/'+product['id']+'/offers')
    assert response.status_code == 200, response.text
    offers = response.json()['offers']
    assert len(offers) == 1, response.text
    assert offers[0]['fulfillment'][0]['stock'] == 5
    assert offers[0]['fulfillment'][0]['stock_display'] == '>4'
    assert offers[0]['fulfillment'][0]['stock_is_lower_bound'] is True
    html = await client.get('/product/'+product['slug'])
    assert html.status_code == 200, html.text
    assert '&gt;4' in html.text
