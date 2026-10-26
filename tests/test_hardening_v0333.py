"""Offline regression tests for G1/A/B/C/F. Nothing calls real suppliers or bank."""
import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest

from app.modules.supplier_feeds.parsers import _positive_int, _nonnegative_int, parse_stock, read_feed
from app.modules.offer_engine.currency import require_rub, rub_amount
from app.modules.offer_engine.supplier_errors import (
    MalformedResponseError, NetworkError, UpstreamBusinessError, UnsupportedCurrencyError,
)
from app.modules.offer_engine.resilience import CircuitBreaker, bounded_read, classify_http
from app.modules.offer_engine.providers import parse_rossko_search_result
from app.modules.payments.gateway import FakeYooKassa, GatewayError, money
from app.modules.finance.pricing_engine import PricePreview, calculate, customer_price_rounding


@pytest.mark.parametrize('v,expected', [('1',1), ('1.0',1), ('3.000',3)])
def test_integer_excel_export_accepts_integral_decimal_not_fraction(v, expected):
    assert _positive_int(v) == expected


@pytest.mark.parametrize('v', ['0', '0.0', '', 'abc', '1.5', '-1', '1e3'])
def test_strict_packaging_quantity_rejected(v):
    with pytest.raises(ValueError):
        _positive_int(v)


def test_zero_stock_is_legal_but_not_a_positive_order_quantity():
    assert _nonnegative_int('0') == 0
    assert parse_stock('0') == ('0', 0, False)
    assert parse_stock(0) == ('0', 0, False)
    assert parse_stock('1.0') == ('1.0', 1, False)
    assert parse_stock('2-6') == ('2-6', 2, True)


def test_partkom_minimum_not_conflated_with_pack_multiple(tmp_path):
    file = tmp_path/'partkom.csv'
    file.write_bytes(('Артикул;Бренд;Описание;Цена;Количество;Мин.Партия;Склад\n'
                      'CF705;TRIALLI;Фильтр;125;0;3;7484\n').encode('cp1251'))
    [(_, row, error)] = list(read_feed('partkom', file))
    assert error is None and row.stock_min == 0
    assert row.minimum == 3 and row.multiple == 1


@pytest.mark.parametrize('code', ('USD','EUR','CNY','',None))
def test_missing_or_foreign_currency_fails_closed(code):
    with pytest.raises(UnsupportedCurrencyError):
        require_rub(code)


@pytest.mark.parametrize('bad', (1.1, float('nan'), '', '-1', 'NaN'))
def test_money_rejects_non_positive_non_decimal_or_non_finite(bad):
    with pytest.raises(MalformedResponseError):
        rub_amount(bad)


def test_explicit_rub_parses_decimal_without_float():
    require_rub('RUB')
    assert rub_amount('10.235') == Decimal('10.24')


def test_rossko_unknown_stock_currency_is_not_assumed_rub():
    part = {'guid':'guid','brand':'TRIALLI','partnumber':'CF 705',
            'stocks':{'stock':[{'id':'WH1','price':'100.00','count':2}]}}
    data = {'success':True,'PartsList':{'Part':[part]}}
    with pytest.raises(UnsupportedCurrencyError):
        parse_rossko_search_result(data,'TRIALLI','CF705')
    part['stocks']['stock'][0]['currency'] = 'RUB'
    found = parse_rossko_search_result(data,'TRIALLI','CF705')
    assert found[0].price == Decimal('100.00')


@pytest.mark.asyncio
async def test_retries_only_network_failure_with_fake_transport():
    state = {'calls':0}
    delays = []
    async def operation():
        state['calls'] += 1
        if state['calls'] < 3:
            raise httpx.ConnectError('broken wire')
        return 'ok'
    async def fake_sleep(seconds):
        delays.append(seconds)
    result = await bounded_read(operation,breaker=CircuitBreaker(),attempts=3,
                                sleeper=fake_sleep,jitter=lambda a,b:0)
    assert result == 'ok' and state['calls'] == 3
    assert delays == [0.15,0.30]


@pytest.mark.asyncio
async def test_business_failure_is_never_retried():
    called = []
    async def operation():
        called.append(1)
        raise UpstreamBusinessError('Denied')
    with pytest.raises(UpstreamBusinessError):
        await bounded_read(operation,breaker=CircuitBreaker(),attempts=3)
    assert len(called)==1


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_and_allows_half_open_probe():
    breaker=CircuitBreaker(threshold=1,reset_seconds=0)
    state={'calls':0}
    async def fail():
        state['calls']+=1
        raise NetworkError('down')
    with pytest.raises(NetworkError):
        await bounded_read(fail,breaker=breaker,attempts=1)
    assert breaker.opened_at is not None
    assert await bounded_read(lambda: asyncio.sleep(0,result='recovered'),breaker=breaker)== 'recovered'
    assert breaker.opened_at is None and breaker.failures == 0
    assert state['calls']==1


def test_http_4xx_is_business_5xx_is_retryable():
    with pytest.raises(UpstreamBusinessError):
        classify_http(httpx.Response(401))
    with pytest.raises(NetworkError):
        classify_http(httpx.Response(503))


def test_f2_last_mile_is_in_markup_base_but_remains_nonbinding():
    result = calculate(PricePreview(operation_date='2026-10-20', sale_model='OWN',
                                    source_gross='100.00',last_mile_delivery='10.00',
                                    markup_percent='20'))
    assert result['customer_gross'] == '132.00'
    assert result['landed_cost_gross'] == '110.00'
    assert not result['production_approved']


def test_smart_up_respects_price_ceiling():
    v = customer_price_rounding(Decimal('1335.47'),mode='SMART_UP',
                                price_ceiling=Decimal('1336.00'))
    assert v['customer_gross']=='1336.00' and v['price_ceiling_met']


@pytest.mark.parametrize('bad', (1.01, float('inf'), Decimal('NaN'), '0.001', '-1'))
def test_gateway_money_strict_decimal_only(bad):
    with pytest.raises(GatewayError):
        money(bad)


@pytest.mark.asyncio
async def test_fake_gateway_idempotent_and_not_paid_before_explicit_settlement(monkeypatch):
    monkeypatch.setenv('APP_ENV','test')
    from app.core.config import get_settings
    get_settings.cache_clear()
    fake = FakeYooKassa()
    order_id = uuid4()
    body={'amount':{'value':'200.00','currency':'RUB'},
          'metadata':{'order_id':str(order_id)}}
    created=await fake.call('POST','/payments',key=order_id,body=body)
    again=await fake.call('POST','/payments',key=order_id,body=body)
    assert created['id']==again['id']
    assert (await fake.status(created['id']))['status']=='pending'
    with pytest.raises(GatewayError):
        await fake.call('POST','/payments',key=order_id,body={**body,'amount':{'value':'201.00','currency':'RUB'}})
    fake.settle(created['id'])
    assert (await fake.status(created['id']))['paid'] is True
    get_settings.cache_clear()

@pytest.mark.asyncio
async def test_v017_order_detail_refuses_other_workflow(client):
    from app.main import app
    from app.core.db import get_session
    from app.modules.commerce.models import Order
    from tests.test_marketplace_foundation_v07 import new_user
    from uuid import UUID
    uid, authorization = await new_user(client,'v0333-workflow@example.com')
    async for db in app.dependency_overrides[get_session]():
        order = Order(user_id=UUID(uid),idempotency_key='v0333-wrong-workflow',
                      total=Decimal('100.00'),total_is_final=True,
                      status='awaiting_sbp',request_details={'workflow':'sbp_checkout'},
                      contact='Test',phone='79999999999',shipping_address='Samara')
        db.add(order)
        await db.commit()
        oid = str(order.id)
        break
    response = await client.get(f'/api/v1/marketplace/cart/v017/orders/{oid}',headers=authorization)
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_webhook_rejects_chunked_oversize_before_json_parsing(client):
    async def streaming():
        for _ in range(18):
            yield b'a' * 1024
    response = await client.post('/api/v1/payments/yookassa/webhook',content=streaming(),
                                  headers={'Content-Type':'application/json','Transfer-Encoding':'chunked'})
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_genuine_shape_webhook_replay_does_not_duplicate_fulfillment(client,monkeypatch):
    import uuid
    from app.main import app
    from app.core.db import get_session
    from app.modules.commerce.models import Order, OrderLine, Procurement, Job
    from app.modules.accounts.models import User
    from app.modules.payments.models import Payment
    from app.modules.payments import api as payment_api
    from tests.test_marketplace_foundation_v07 import new_user
    from sqlalchemy import select, func

    uid, _ = await new_user(client, 'v0333-webhook@example.com')
    async for db in app.dependency_overrides[get_session]():
        order = Order(user_id=uuid.UUID(uid),idempotency_key='v0333-pay',
                      total=Decimal('100.00'),request_details={'workflow':'sbp_checkout'},
                      total_is_final=True,status='awaiting_sbp',
                      contact='Test',phone='79999999999',shipping_address='Samara')
        db.add(order)
        await db.flush()
        line=OrderLine(order_id=order.id,title='Test',supplier_code='rossko',
                       quantity=1,unit_retail=Decimal('100.00'),unit_purchase=Decimal('50.00'),
                       commercial_snapshot={'source_kind':'supplier'})
        pay=Payment(order_id=order.id,provider_id='fake-v0333-payment',
                    status='pending',amount=Decimal('100.00'))
        db.add_all([line,pay])
        await db.commit()
        order_id = order.id
        break
    class VerifiedProvider:
        calls = 0
        async def status(self, provider_id):
            self.calls += 1
            return {'id':provider_id,'status':'succeeded','paid':True,
                    'metadata':{'order_id':str(order_id)},
                    'amount':{'value':'100.00','currency':'RUB'},
                    'payment_method':{'type':'sbp'}}
    bank=VerifiedProvider()
    monkeypatch.setattr(payment_api,'gateway',lambda:bank)
    hook={'type':'notification','event':'payment.succeeded','object':{'id':'fake-v0333-payment'}}
    for _ in range(2):
        response=await client.post('/api/v1/payments/yookassa/webhook',json=hook)
        assert response.status_code==200,response.text
    async for db in app.dependency_overrides[get_session]():
        assert (await db.get(Order,order_id)).status == 'paid_procurement_pending'
        assert await db.scalar(select(func.count()).select_from(Procurement)) == 1
        assert await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='procurement')) == 1
        break
    assert bank.calls==2

@pytest.mark.asyncio
async def test_read_endpoint_has_local_rate_fallback_write_fails_closed(monkeypatch):
    import app.core.rate_limit as rl
    from fastapi import HTTPException
    from app.core.config import get_settings
    monkeypatch.setenv('APP_ENV','development')
    get_settings.cache_clear()
    async def down():
        raise ConnectionError('no Redis')
    import app.modules.offer_engine.cache as cache
    monkeypatch.setattr(cache,'redis_client',down)
    rl._LOCAL_BUCKETS.clear()
    await rl.enforce_budget('public_costly','fake-peer-v0333',limit=1,period=60)
    with pytest.raises(HTTPException) as limit:
        await rl.enforce_budget('public_costly','fake-peer-v0333',limit=1,period=60)
    assert limit.value.status_code == 429
    with pytest.raises(HTTPException) as closed:
        await rl.enforce_budget('staff_login_ip','fake-peer-v0333',limit=1,period=60)
    assert closed.value.status_code == 503
    get_settings.cache_clear()


def test_f2_preview_rejects_extreme_commission_and_markup():
    from pydantic import ValidationError
    from app.modules.finance.pricing_engine import PricePreview
    for extra in ({'sbp_percent':'99.9'},{'markup_percent':'999'}):
        with pytest.raises(ValidationError):
            PricePreview(operation_date='2026-10-20',sale_model='OWN',
                         source_gross='100',**extra)


@pytest.mark.asyncio
async def test_partkom_parser_uses_decimal_json_and_currency_guard(monkeypatch):
    import app.modules.offer_engine.providers as providers
    from decimal import Decimal
    from types import SimpleNamespace
    import json
    data=[{'detailGroup':'Original','maker':'TRIALLI','number':'CF 705',
           'makerId':1,'providerId':2,'placementId':'WH01','price':492.27,
           'currency':'RUB','quantity':8}]
    class FakeClient:
        async def get(self,*args,**kwargs):
            return httpx.Response(200,content=json.dumps(data).encode())
    monkeypatch.setattr(providers,'get_client',FakeClient)
    settings=SimpleNamespace(supplier_api_enabled=True,partkom_login='mock',partkom_password='mock')
    offers=await providers.PROVIDERS['partkom'].search('TRIALLI','CF705',settings)
    assert offers[0].price==Decimal('492.27')
    data[0]['currency']='USD'
    with pytest.raises(UnsupportedCurrencyError):
        await providers.PROVIDERS['partkom'].search('TRIALLI','CF705',settings)


def test_supplier_request_url_and_xml_are_redacted_in_logs(monkeypatch):
    import logging
    import io
    from app.modules.offer_engine.secret_redaction import SecretRedactionFilter
    from app.core.config import get_settings
    monkeypatch.setenv('FORUM_PASSWORD','secret-v0333-pass')
    get_settings.cache_clear()
    buffer=io.StringIO()
    handler=logging.StreamHandler(buffer)
    handler.addFilter(SecretRedactionFilter())
    logger=logging.getLogger('supplier-v0333-redaction-test')
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        logger.warning('https://api.example.test/v2?login=user&pass=secret-v0333-pass <password>SECRET</password>')
    finally:
        logger.removeHandler(handler)
        get_settings.cache_clear()
    rendered=buffer.getvalue()
    assert 'secret-v0333-pass' not in rendered
    assert '>SECRET<' not in rendered
    assert 'pass=***' in rendered
    assert '<password>***</password>' in rendered
