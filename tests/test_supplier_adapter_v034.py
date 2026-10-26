"""v0.34 contract tests. All fixtures synthetic; no supplier or bank network.

None of these tests authorizes procurement or assumes absent currency is RUB.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.modules.offer_engine import providers
from app.modules.offer_engine.contracts import Offer
from app.modules.offer_engine.currency import rub_amount
from app.modules.offer_engine.parsers import (
    MAX_RESPONSE_BYTES, parse_adeo_xml, parse_forum_rows, parse_json_body,
    parse_partkom_rows, parse_rossko_search_result,
)
from app.modules.offer_engine.resilience import BREAKERS, CircuitBreaker, bounded_read
from app.modules.offer_engine.supplier_errors import (
    MalformedResponseError, NetworkError, UnsupportedCurrencyError,
    UpstreamBusinessError,
)
from app.modules.suppliers.adapters.base import ReadAdapter, SupplierAdapter

FIXTURES = Path(__file__).parent / 'fixtures'
SAMPLES = json.loads((FIXTURES / 'v034_supplier_response_samples.json').read_text('utf8'))


def test_single_registry_and_read_contract_compatibility():
    from app.modules.suppliers.adapters import (
        AdeoAdapter, RosskoAdapter, PartkomAdapter, ForumAdapter, get_adapter,
    )
    assert get_adapter('rossko') is providers.PROVIDERS['rossko']
    for code, cls in [
        ('adeo', AdeoAdapter), ('rossko', RosskoAdapter),
        ('partkom', PartkomAdapter), ('forum-auto', ForumAdapter),
    ]:
        adapter = providers.get_adapter(code)
        assert type(adapter) is cls
        assert isinstance(adapter, SupplierAdapter)
        assert isinstance(adapter, ReadAdapter)
        assert not hasattr(adapter, 'create_order')
        assert not hasattr(adapter, 'cancel_order')
    with pytest.raises(UpstreamBusinessError):
        providers.get_adapter('not-a-real-supplier')


@pytest.mark.parametrize('source,parser,expected_price', [
    ('partkom', parse_partkom_rows, '492.28'),
    ('forum-auto', parse_forum_rows, '510.25'),
])
def test_supplier_json_fixtures_decimal_rub_and_exact_sku(source, parser, expected_price):
    rows = parse_json_body(json.dumps(SAMPLES[source]).encode())
    offers = parser(rows, 'TRIALLI', 'CF 705')
    assert len(offers) == 1
    assert offers[0].currency == 'RUB'
    assert offers[0].price == Decimal(expected_price)
    assert offers[0].price.as_tuple().exponent == -2
    assert offers[0].article.replace(' ', '') == 'CF705'


def test_partkom_minimum_not_confused_with_pack_multiple():
    [item] = parse_partkom_rows(SAMPLES['partkom'], 'TRIALLI', 'CF705')
    assert item.minimum == 2 and item.multiple == 1
    assert item.public_meta['pack_multiple_unverified'] is True


def test_forum_multiplicity_and_stock():
    [item] = parse_forum_rows(SAMPLES['forum-auto'], 'TRIALLI', 'CF705')
    assert (item.qty, item.minimum, item.multiple) == (7, 2, 2)
    assert item.public_meta['returnable'] is True


def test_adeo_xml_is_pure_and_retains_lower_bound_semantics():
    xml = (FIXTURES / 'v034_adeo_response_sample.xml').read_bytes()
    [offer] = parse_adeo_xml(xml, 'TRIALLI', 'CF 705')
    assert offer.price == Decimal('550.20')
    assert offer.qty == 5
    assert offer.public_meta['stock_raw'] == '>4'
    assert offer.public_meta['stock_is_lower_bound'] is True


def test_rossko_soap_shape_is_pure():
    [offer] = parse_rossko_search_result(SAMPLES['rossko'], 'TRIALLI', 'CF705')
    assert (offer.price, offer.qty, offer.days, offer.key) == (
        Decimal('505.00'), 5, 2, 'RG1:W-A')


@pytest.mark.parametrize('source,parser', [
    ('partkom', parse_partkom_rows), ('forum-auto', parse_forum_rows),
])
@pytest.mark.parametrize('currency', [None, '', 'EUR', 'USD'])
def test_json_supplier_quote_without_confirmed_rub_is_rejected(source, parser, currency):
    rows = [dict(SAMPLES[source][0], currency=currency)]
    with pytest.raises(UnsupportedCurrencyError):
        parser(rows, 'TRIALLI', 'CF705')


@pytest.mark.parametrize('currency', [None, '', 'EUR', 'USD'])
def test_rossko_currency_cannot_be_assumed_from_territory(currency):
    row = SAMPLES['rossko']
    import copy
    copy_ = copy.deepcopy(row)
    copy_['PartsList']['Part'][0]['stocks']['stock'][0]['currency'] = currency
    with pytest.raises(UnsupportedCurrencyError):
        parse_rossko_search_result(copy_, 'TRIALLI', 'CF705')


@pytest.mark.parametrize('currency', ['', 'EUR', 'USD'])
def test_adeo_currency_invalid(currency):
    text = (FIXTURES / 'v034_adeo_response_sample.xml').read_text('utf8')
    with pytest.raises(UnsupportedCurrencyError):
        parse_adeo_xml(text.replace('<currency>руб</currency>', f'<currency>{currency}</currency>').encode(),
                       'TRIALLI', 'CF705')


@pytest.mark.parametrize('body', [b'{bad json', b'{}', b'NaN', b'[NaN]', b'[1]', b''])
def test_malformed_json_fails_not_empty_cache(body):
    with pytest.raises(MalformedResponseError):
        parse_json_body(body)


def test_supplier_response_size_limit():
    with pytest.raises(MalformedResponseError):
        parse_json_body(b' ' * (MAX_RESPONSE_BYTES + 1))
    with pytest.raises(MalformedResponseError):
        parse_adeo_xml(b' ' * (MAX_RESPONSE_BYTES + 1), 'A', 'B')


def test_xml_entity_expansion_rejected():
    payload = b'<!DOCTYPE foo [ <!ENTITY a "hello"> ]><root><detail><producer>&a;</producer></detail></root>'
    with pytest.raises(MalformedResponseError):
        parse_adeo_xml(payload, 'A', 'B')


def test_offer_model_refuses_float_money_and_invalid_packaging():
    template = Offer('partkom', 'key', 'TRIALLI', 'CF705', Decimal('10.20'), 2, 1, 'source')
    for changes in ({'price': 10.2}, {'price': Decimal('NaN')},
                    {'price': Decimal('10.201')}, {'qty': -1}, {'minimum': 0},
                    {'multiple': 0}):
        with pytest.raises(MalformedResponseError):
            replace(template, **changes)
    with pytest.raises(UnsupportedCurrencyError):
        replace(template, currency='EUR')


@pytest.mark.asyncio
async def test_check_stock_performs_fresh_read_exact_sku_and_packaging():
    class InMemorySupplier(ReadAdapter):
        code = 'partkom'
        calls = 0
        async def search(self, brand, article, config=None):
            self.calls += 1
            return [Offer(self.code, 'A', brand, article, Decimal('500.00'), 7, 1,
                          'source', minimum=2, multiple=2)]
    adapter = InMemorySupplier()
    assert (await adapter.check_stock('A', 'TRIALLI', 'CF705', 4)).qty == 7
    for key, qty in [('A', 1), ('A', 3), ('A', 8), ('BAD', 4)]:
        with pytest.raises(UpstreamBusinessError):
            await adapter.check_stock(key, 'TRIALLI', 'CF705', qty)
    assert adapter.calls == 5


@pytest.mark.asyncio
async def test_global_registry_calls_have_backward_compatible_verify(monkeypatch):
    class ReadOnlyFake(ReadAdapter):
        code = 'partkom'
        async def search(self, brand, article, config=None):
            return [Offer('partkom', 'a', brand, article, Decimal('500.00'), 5, 1, 'token')]
    monkeypatch.setitem(providers.PROVIDERS, 'partkom', ReadOnlyFake())
    assert (await providers.verify('partkom', 'a', 'TRIALLI', 'CF705', 1)).qty == 5


@pytest.mark.asyncio
async def test_per_account_breaker_does_not_disable_another_account(monkeypatch):
    call_count = {'bad': 0, 'good': 0}
    class FakeHTTP:
        async def get(self, url, **kwargs):
            login, _ = kwargs['auth']
            call_count[login] += 1
            if login == 'bad':
                # Error message is intentionally a sensitive full URL.
                raise httpx.ConnectError('https://example.invalid/?pass=secret-dont-log')
            return httpx.Response(200, content=json.dumps(SAMPLES['partkom']).encode())
    monkeypatch.setattr(providers, 'get_client', lambda: FakeHTTP())
    bad = SimpleNamespace(supplier_api_enabled=True, partkom_login='bad',
                          partkom_password='secret', supplier_account_scope='account-A')
    good = SimpleNamespace(supplier_api_enabled=True, partkom_login='good',
                           partkom_password='secret', supplier_account_scope='account-B')
    key = ('partkom', 'account-A')
    monkeypatch.setitem(BREAKERS, key, CircuitBreaker(threshold=1, reset_seconds=3600))
    try:
        with pytest.raises(NetworkError) as error:
            await providers.PROVIDERS['partkom'].search('TRIALLI', 'CF705', bad)
        assert 'secret-dont-log' not in str(error.value)
        assert call_count['bad'] == 3
        with pytest.raises(NetworkError, match='circuit is open'):
            await providers.PROVIDERS['partkom'].search('TRIALLI', 'CF705', bad)
        assert call_count['bad'] == 3
        [offer] = await providers.PROVIDERS['partkom'].search('TRIALLI', 'CF705', good)
        assert offer.price == Decimal('492.28')
        assert call_count['good'] == 1
    finally:
        BREAKERS.pop(key, None)
        BREAKERS.pop(('partkom', 'account-B'), None)


@pytest.mark.asyncio
async def test_half_open_probe_cancellation_releases_inflight_flag():
    breaker = CircuitBreaker(threshold=1, reset_seconds=0)
    breaker.opened_at = 0
    async def cancelled():
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await bounded_read(cancelled, breaker=breaker, attempts=3)
    assert breaker.probe_inflight is False


@pytest.mark.asyncio
async def test_provider_nonretryable_malformed_response_only_calls_network_once(monkeypatch):
    calls = 0
    class FakeHTTP:
        async def get(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b'not-json')
    monkeypatch.setattr(providers, 'get_client', lambda: FakeHTTP())
    settings = SimpleNamespace(supplier_api_enabled=True, partkom_login='x',
                               partkom_password='y', supplier_account_scope='v034-nonretryable')
    try:
        with pytest.raises(MalformedResponseError):
            await providers.PROVIDERS['partkom'].search('TRIALLI', 'CF705', settings)
        assert calls == 1
    finally:
        BREAKERS.pop(('partkom', 'v034-nonretryable'), None)


def test_currency_and_price_do_not_assume_float():
    assert rub_amount('105.325') == Decimal('105.33')
    with pytest.raises(MalformedResponseError):
        rub_amount(105.325)


@pytest.mark.asyncio
async def test_rossko_wsdl_cached_per_loop_version_without_credentials(monkeypatch):
    """No live WSDL download; successful reuse and clean shutdown are asserted."""
    import sys
    from types import ModuleType
    from app.modules.offer_engine import soap_client
    zeep = ModuleType('zeep')
    zeep_transports = ModuleType('zeep.transports')
    monkeypatch.setitem(sys.modules, 'zeep', zeep)
    monkeypatch.setitem(sys.modules, 'zeep.transports', zeep_transports)
    created, closed = [], []

    class FakeHTTP:
        def close(self):
            closed.append(self)

    def new_http(**kwargs):
        client = FakeHTTP()
        created.append(client)
        return client

    def new_zeep_client(wsdl, transport):
        return {'wsdl': wsdl, 'transport': transport}

    monkeypatch.setattr(soap_client.httpx, 'Client', new_http)
    zeep.AsyncClient = new_zeep_client
    zeep_transports.AsyncTransport = lambda **kw: kw
    from app.modules.offer_engine import http_pool
    monkeypatch.setattr(http_pool, 'get_client', lambda: object())
    await soap_client.close_soap_clients()
    wsdl = 'https://api.rossko.ru/service/v2.1/GetSearch?wsdl'
    first = soap_client.rossko_client(wsdl)
    second = soap_client.rossko_client(wsdl)
    assert first is second and len(created) == 1
    assert 'KEY1' not in repr(first) and 'KEY2' not in repr(first)
    changed = soap_client.rossko_client(wsdl + '&v=next')
    assert changed is not first and len(created) == 2
    await soap_client.close_soap_clients()
    assert len(closed) == 2


def test_account_credentials_assign_safe_unique_breaker_scopes(monkeypatch):
    from app.modules.marketplace import account_credentials as ac
    account_a = SimpleNamespace(credential_profile='TESTA', id='account-a')
    account_b = SimpleNamespace(credential_profile='TESTB', id='account-b')
    dest = SimpleNamespace(id='dest-one', external_delivery_id='', external_address_id='')
    monkeypatch.setattr(ac, 'get_settings', lambda: SimpleNamespace(supplier_api_enabled=True))
    monkeypatch.setenv('SUPPLIER_PROFILE_TESTA_PARTKOM_LOGIN', 'a-login')
    monkeypatch.setenv('SUPPLIER_PROFILE_TESTA_PARTKOM_PASSWORD', 'topsecret-A')
    monkeypatch.setenv('SUPPLIER_PROFILE_TESTB_PARTKOM_LOGIN', 'b-login')
    monkeypatch.setenv('SUPPLIER_PROFILE_TESTB_PARTKOM_PASSWORD', 'topsecret-B')
    first = ac.for_account('partkom', account_a, dest)
    second = ac.for_account('partkom', account_b, dest)
    assert first.supplier_account_scope != second.supplier_account_scope
    assert 'topsecret' not in first.supplier_account_scope
    assert first.partkom_login == 'a-login'
    assert second.partkom_login == 'b-login'
