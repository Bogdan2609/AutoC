"""Offline unit tests: no external SOAP orders, no credentials."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from scripts import rossko_test_retry_diagnostic as retry


ACCOUNT = '511a4e77-55a7-48b8-874f-68420c7aa162'
DEST = 'c6afd071-6772-4963-b3d1-0770acdbd749'


def test_supplier_message_redacts_contacts_and_secrets():
    msg = retry.redact_supplier_message('Мин. сумма 3000 ₽; телефон +79879026120; KEY1=abcSECRET; help@domain.ru')
    assert '3000' in msg
    assert '79879026120' not in msg
    assert 'abcSECRET' not in msg
    assert 'help@domain.ru' not in msg
    assert '[телефон скрыт]' in msg


def test_extracts_documented_message_and_item_rejection():
    result = retry.checkout_messages({'CheckoutResult': {
        'success': False, 'message': 'Минимальная сумма заказа 3000',
        'ItemsErrorList': {'ItemError': {'message': 'Нет в наличии'}},
    }})
    assert result == ['Минимальная сумма заказа 3000', 'Позиция: Нет в наличии']


def args(**kw):
    data = dict(account_id=ACCOUNT, destination_id=DEST, stock='HST282827366', payment_id='1',
                confirm=retry.CONFIRM, portal_test_enabled=True, accept_duplicate_risk=True,
                courier_minimum_unknown=True, courier_minimum_rub=None)
    data.update(kw)
    return SimpleNamespace(**data)


def environment(monkeypatch, tmp_path, oldstate='supplier_rejected'):
    old = tmp_path / 'original.json'
    old.write_text(json.dumps({'account_id': ACCOUNT, 'destination_id': DEST,
                               'state': oldstate, 'order_ids': [], 'created_utc': 'test'}))
    monkeypatch.setattr(retry, 'JOURNAL_DIR', tmp_path)
    monkeypatch.setattr(retry, 'latest_journal', lambda a, d: old)
    monkeypatch.setattr(retry, 'get_settings', lambda: SimpleNamespace(app_env='development', supplier_api_enabled=True))
    cfg = SimpleNamespace(rossko_key1='LOCAL_FAKE_KEY1', rossko_key2='LOCAL_FAKE_KEY2')
    offer = SimpleNamespace(warehouse='HST282827366', article='CF 705', brand='Trialli', price=retry.MAX_TEST_PRICE)
    async def fake_quote(*a):
        return cfg, retry.COURIER_DELIVERY_ID, 'FAKE_ADDRESS', offer, '39619'
    monkeypatch.setattr(retry, 'live_quote', fake_quote)
    from app.modules.marketplace.receiving_contact import ReceivingContact
    async def configured_receiver(*args): return ReceivingContact('Складов Андрей', '+79870000000')
    monkeypatch.setattr(retry, 'receiving_contact_for_destination_ids', configured_receiver)
    return cfg


def test_one_retry_reports_rossko_message_then_blocks_second(monkeypatch, tmp_path, capsys):
    environment(monkeypatch, tmp_path)
    calls = []
    async def fake_soap(endpoint, action, payload):
        calls.append(action)
        if action == 'GetOrders':
            return {'OrdersResult': {'success': True, 'OrdersList': {'Order': []}}}
        assert payload['PARTS']['Part'][0]['count'] == 1
        return {'CheckoutResult': {'success': False, 'message': 'Минимальная сумма 3000 ₽, телефон +79870000000'}}
    monkeypatch.setattr(retry, 'soap_request', fake_soap)
    assert asyncio.run(retry.attempt(args())) == 3
    shown = capsys.readouterr().out
    assert 'Минимальная сумма 3000' in shown
    assert '79870000000' not in shown
    assert calls == ['GetOrders', 'GetCheckout']
    saved = json.loads(retry.retry_journal_path(ACCOUNT, DEST).read_text())
    assert saved['state'] == 'supplier_rejected'
    assert '3000' in saved['redacted_supplier_messages'][0]
    assert '79870000000' not in json.dumps(saved)
    assert asyncio.run(retry.attempt(args())) == 2
    assert calls == ['GetOrders', 'GetCheckout']


def test_skips_retry_when_sku_found(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    calls = []
    async def fake_soap(endpoint, action, payload):
        calls.append(action)
        return {'OrdersResult': {'success': True, 'OrdersList': {'Order': [{'id': 123, 'parts': {
            'part': {'brand': 'Trialli', 'partnumber': 'CF 705', 'count': 1, 'price': '492.27'}}}]}}}
    monkeypatch.setattr(retry, 'soap_request', fake_soap)
    assert asyncio.run(retry.attempt(args())) == 2
    assert calls == ['GetOrders']
    assert not retry.retry_journal_path(ACCOUNT, DEST).exists()


def test_only_prior_supplier_rejected_eligible(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path, oldstate='dispatch_unknown')
    with pytest.raises(ValueError):
        retry.load_previous(ACCOUNT, DEST)


def test_block_without_acknowledgements(monkeypatch, tmp_path):
    environment(monkeypatch, tmp_path)
    assert asyncio.run(retry.attempt(args(portal_test_enabled=False))) == 2
    assert not retry.retry_journal_path(ACCOUNT, DEST).exists()
