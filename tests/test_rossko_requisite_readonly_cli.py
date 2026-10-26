"""No network access: verify the read-only CLI never invokes checkout."""
import asyncio
from types import SimpleNamespace
from scripts import rossko_requisite_readonly as cli


def test_readonly_shows_selected_requisite_without_pii(monkeypatch, capsys):
    cfg = SimpleNamespace(rossko_key1='SECRET', rossko_key2='SECRET')
    async def fake_context(*a):
        return cfg, '000000002', 'ADDRESS_PRIVATE'
    async def fake_details(c):
        assert c is cfg
        return {'CheckoutDetailsResult': {'success': True, 'CompanyList': {'company': {
            'id': 39619, 'name': 'PRIVATE COMPANY', 'requisite': 'PRIVATE TAX INFO',
        }}}}
    def fake_normalize(resp):
        return {'deliveries': [{'id': '000000002'}], 'payments': [{'id': '1'}],
                'addresses': [{'id': 'ADDRESS_PRIVATE', 'allowed_delivery_ids': ['000000002']}]}
    monkeypatch.setattr(cli, 'context', fake_context)
    monkeypatch.setattr(cli, 'live_rossko_details', fake_details)
    monkeypatch.setattr(cli, 'normalize_checkout_details', fake_normalize)
    assert asyncio.run(cli.check('account', 'destination', '39619', '1')) == 0
    out = capsys.readouterr().out
    assert 'GETCHECKOUT_CALLED=False' in out and '39619' in out
    assert 'PRIVATE' not in out and 'SECRET' not in out


def test_readonly_rejects_stale_requisite(monkeypatch):
    async def fake_context(*a):
        return None, '000000002', 'ADDRESS_PRIVATE'
    async def fake_details(*a):
        return {'success': True, 'CompanyList': {'company': [{'id': 11}]}}
    def fake_normalize(resp):
        return {'deliveries': [{'id': '000000002'}], 'payments': [{'id': '1'}],
                'addresses': [{'id': 'ADDRESS_PRIVATE', 'allowed_delivery_ids': ['000000002']}]}
    monkeypatch.setattr(cli, 'context', fake_context)
    monkeypatch.setattr(cli, 'live_rossko_details', fake_details)
    monkeypatch.setattr(cli, 'normalize_checkout_details', fake_normalize)
    try:
        asyncio.run(cli.check('account', 'destination', '39619', '1'))
    except ValueError as exc:
        assert 'not present' in str(exc)
    else:
        assert False, 'stale requisite must fail closed'
