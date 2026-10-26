import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import rossko_test_requisite_confirm as mod
from scripts.rossko_test_retry_diagnostic import retry_journal_path
from scripts.rossko_test_checkout_lab import journal_path

ACCOUNT = '511a4e77-55a7-48b8-874f-68420c7aa162'
DEST = 'c6afd071-6772-4963-b3d1-0770acdbd749'


def args(**kw):
    data = dict(account_id=ACCOUNT, destination_id=DEST, stock=mod.DEFAULT_STOCK, payment_id='1',
                confirm=mod.CONFIRM, portal_test_enabled=True, accept_duplicate_risk=True,
                courier_minimum_unknown=True, courier_minimum_rub=None)
    return SimpleNamespace(**{**data, **kw})


def test_journal_path_is_distinct_from_old_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, 'JOURNAL_DIR', tmp_path)
    fresh = mod.final_journal_path(ACCOUNT, DEST)
    assert fresh != retry_journal_path(ACCOUNT, DEST)
    assert fresh != journal_path(ACCOUNT, DEST)
    assert 'third-attempt' in fresh.name


def test_requires_two_prior_rejected_records(monkeypatch, tmp_path):
    old = tmp_path / 'original.json'
    retry = tmp_path / 'second.json'
    base = dict(account_id=ACCOUNT, destination_id=DEST, state='supplier_rejected', order_ids=[])
    old.write_text(json.dumps(base), encoding='utf-8')
    retry.write_text(json.dumps({**base, 'error_code':'supplier_rejected_payment'}), encoding='utf-8')
    monkeypatch.setattr(mod, 'latest_journal', lambda *args: old)
    monkeypatch.setattr(mod, 'retry_journal_path', lambda *args: retry)
    assert mod.load_previous(ACCOUNT, DEST)['error_code'] == 'supplier_rejected_payment'
    retry.write_text(json.dumps({**base, 'state':'dispatch_unknown'}))
    with pytest.raises(ValueError):
        mod.load_previous(ACCOUNT, DEST)
    retry.write_text(json.dumps({**base, 'error_code':'supplier_rejected_other'}))
    with pytest.raises(ValueError):
        mod.load_previous(ACCOUNT, DEST)


def test_submit_never_uses_non_test_credentials_or_wrong_stock(monkeypatch):
    monkeypatch.setattr(mod, 'get_settings', lambda: SimpleNamespace(app_env='production'))
    assert asyncio.run(mod.attempt(args())) == 2
    monkeypatch.setattr(mod, 'get_settings', lambda: SimpleNamespace(app_env='development'))
    assert asyncio.run(mod.attempt(args(stock='other'))) == 2
    assert asyncio.run(mod.attempt(args(confirm='wrong'))) == 2


def test_preview_never_calls_checkout(monkeypatch, tmp_path):
    from decimal import Decimal
    monkeypatch.setattr(mod, 'get_settings', lambda: SimpleNamespace(app_env='development'))
    monkeypatch.setattr(mod, 'final_journal_path', lambda *args: tmp_path/'missing.json')
    monkeypatch.setattr(mod, 'load_previous', lambda *args: {'state':'supplier_rejected'})
    cfg = SimpleNamespace(rossko_key1='PRIVATE1', rossko_key2='PRIVATE2')
    offer = SimpleNamespace(warehouse=mod.DEFAULT_STOCK, price=Decimal('492.27'))
    async def quote(*args): return cfg,'000000002','address-private',offer,'39619'
    async def soap(endpoint, *args):
        assert endpoint=='GetOrders'
        return {'OrdersResult':{'success':True,'OrdersList':{}}}
    monkeypatch.setattr(mod, 'live_quote', quote)
    monkeypatch.setattr(mod, 'soap_request', soap)
    assert asyncio.run(mod.preview(args())) == 0
    assert not (tmp_path/'missing.json').exists()


def test_one_submit_includes_requisite_id_and_no_retries(monkeypatch, tmp_path):
    from decimal import Decimal
    monkeypatch.setattr(mod, 'get_settings', lambda: SimpleNamespace(app_env='development', supplier_api_enabled=True))
    path = tmp_path/'third.json'
    monkeypatch.setattr(mod, 'final_journal_path', lambda *args: path)
    monkeypatch.setattr(mod, 'load_previous', lambda *args: {'state':'supplier_rejected','created_utc':'old'})
    cfg = SimpleNamespace(rossko_key1='PRIVATE1', rossko_key2='PRIVATE2')
    offer = SimpleNamespace(brand='Trialli', article='CF 705', warehouse=mod.DEFAULT_STOCK, price=Decimal('492.27'))
    async def quote(*args): return cfg,'000000002','private-address',offer,'39619'
    seen=[]
    async def soap(endpoint, service, payload):
        seen.append((endpoint,payload))
        if endpoint == 'GetOrders': return {'OrdersResult':{'success':True,'OrdersList':{}}}
        assert endpoint == 'GetCheckout'
        assert payload['payment']=={'payment_id':'1','requisite_id':'39619'}
        assert payload['delivery']=={'delivery_id':'000000002','address_id':'private-address'}
        assert payload['PARTS']['Part'][0]['count']==1
        return {'CheckoutResult':{'success': False,'message':'test declined','OrderIDS':{}}}
    monkeypatch.setattr(mod, 'live_quote', quote)
    monkeypatch.setattr(mod, 'soap_request', soap)
    from app.modules.marketplace.receiving_contact import ReceivingContact
    async def configured_receiver(*args): return ReceivingContact('Складов Андрей', '+79999999999')
    monkeypatch.setattr(mod, 'receiving_contact_for_destination_ids', configured_receiver)
    assert asyncio.run(mod.attempt(args())) == 3
    assert path.exists()
    assert json.loads(path.read_text())['state']=='supplier_rejected'
    assert len([x for x in seen if x[0]=='GetCheckout'])==1
    assert asyncio.run(mod.attempt(args())) == 2
    assert len([x for x in seen if x[0]=='GetCheckout'])==1


def test_reconcile_no_ids_does_not_call_api(monkeypatch,tmp_path):
    path=tmp_path/'third.json'
    path.write_text(json.dumps({'account_id':ACCOUNT,'destination_id':DEST,'order_ids':[], 'state':'supplier_rejected'}))
    monkeypatch.setattr(mod, 'final_journal_path',lambda *args:path)
    assert asyncio.run(mod.reconcile(args()))==3
