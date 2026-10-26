"""Offline lab contracts. Never performs HTTP requests or supplier order writes."""
from pathlib import Path
import importlib.util
from decimal import Decimal
import pytest

_MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'rossko_test_checkout_lab.py'
_spec = importlib.util.spec_from_file_location('rossko_test_checkout_lab', _MODULE)
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def test_success_requires_supplier_id():
    assert lab.normalize_checkout({'success': True, 'OrderIDS': {'id': 234}}) == (
        'accepted_unverified', [234], None)
    assert lab.normalize_checkout({'success': True})[0] == 'manual_review'
    assert lab.normalize_checkout({'success': False})[0] == 'supplier_rejected'
    assert lab.normalize_checkout({'success': False, 'OrderIDS': {'id': 234}})[0] == 'manual_review'


def test_partial_checkout_never_claimed_success():
    status, ids, reason = lab.normalize_checkout({'success': True, 'OrderIDS': {'id': [123]},
        'ItemsErrorList': {'ItemError': [{'partnumber': 'CF 705'}]}})
    assert status == 'manual_review' and ids == [123] and reason == 'partial_item_failure'


def test_reconcile_exact_single_item_and_no_more_than_price_ceiling():
    order = {'success': True, 'OrdersList': {'Order': {
        'id': 999, 'parts': {'part': {'brand': 'TRIALLI', 'partnumber': 'CF705',
                                    'count': 1, 'price': '492.27'}}}}}
    summary = lab.safe_order_summary(order, [999])
    assert summary['status'] == 'verified' and summary['item_quantity'] == 1
    order['OrdersList']['Order']['parts']['part']['price'] = '492.28'
    assert lab.safe_order_summary(order, [999])['status'] == 'manual_review'
    order['OrdersList']['Order']['parts']['part']['price'] = '492.27'
    order['OrdersList']['Order']['parts']['part']['count'] = 2
    assert lab.safe_order_summary(order, [999])['status'] == 'manual_review'


def test_reconcile_absent_never_claims_verified():
    assert lab.safe_order_summary({'success': True, 'OrdersList': {}}, [100])['status'] == 'pending'
    assert lab.safe_order_summary({'success': False}, [100])['status'] == 'manual_review'


def test_journal_persistent_and_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, 'JOURNAL_DIR', tmp_path)
    path = lab.journal_path('acc', 'dest')
    lab.write_initial(path, {'state': 'dispatch_unknown'})
    assert path.exists()
    assert lab.latest_journal('acc', 'dest') == path
    with pytest.raises(FileExistsError):
        lab.write_initial(path, {'state': 'dispatch_unknown'})
    lab.replace_journal(path, {'state': 'accepted_unverified', 'order_ids': [1]})
    assert 'accepted_unverified' in path.read_text()

@pytest.mark.asyncio
async def test_no_acknowledgement_never_checks_live_or_submits(monkeypatch):
    class Args:
        confirm = ''
        portal_test_enabled = False
    async def forbidden(*a, **k):
        raise AssertionError('No live supplier call should occur')
    monkeypatch.setattr(lab, 'live_quote', forbidden)
    assert await lab.submit(Args()) == 2


def test_unexpected_order_item_requires_manual_review():
    data = {'success': True, 'OrdersList': {'Order': {'id': 100,
        'parts': {'part': [{'brand': 'TRIALLI', 'partnumber': 'CF 705', 'count': 1, 'price': '492.27'},
                           {'brand': 'OTHER', 'partnumber': '123', 'count': 1, 'price': '1.00'}]}}}}
    assert lab.safe_order_summary(data, [100])['status'] == 'manual_review'


def test_preview_diagnostic_shows_only_allowlisted_reason():
    assert lab.format_preview_failure(RuntimeError(
        'ROSSKO courier delivery requires a configured address ID')) == (
        'READ-ONLY PREFLIGHT BLOCKED: ROSSKO courier delivery requires a configured address ID')


def test_preview_diagnostic_never_leaks_untrusted_exception():
    secret = 'https://example.invalid/?KEY1=secret&phone=PRIVATE'
    result = lab.format_preview_failure(RuntimeError(secret))
    assert 'secret' not in result and 'PRIVATE' not in result
    assert 'READ-ONLY PREFLIGHT FAILED:' in result


def test_courier_accepts_only_registered_supplier_address():
    entries = [{'id': 'valid-123', 'allowed_delivery_ids': ['000000002']}]
    assert lab.validate_test_delivery('000000002', 'valid-123', entries) == '000000002'
    with pytest.raises(ValueError, match='requires a configured address'):
        lab.validate_test_delivery('000000002', '', entries)
    with pytest.raises(RuntimeError, match='not present'):
        lab.validate_test_delivery('000000002', 'not-an-address', entries)
    with pytest.raises(RuntimeError, match='not valid'):
        lab.validate_test_delivery('000000002', 'valid-123', [{'id':'valid-123', 'allowed_delivery_ids':['000000001']}])


def test_only_two_documented_delivery_methods_allowed():
    with pytest.raises(ValueError, match='only supports courier'):
        lab.validate_test_delivery('000000001', None, [])
    with pytest.raises(ValueError, match='only supports courier'):
        lab.validate_test_delivery('000000003', 'valid-123', [{'id': 'valid-123'}])


@pytest.mark.asyncio
async def test_courier_no_submit_without_minimum_confirmation(monkeypatch):
    class Args:
        confirm = lab.TEST_CONFIRMATION
        portal_test_enabled = True
        account_id = 'abc'
        destination_id = 'def'
        stock = lab.DEFAULT_STOCK
        payment_id = '1'
        courier_terms_confirmed = False
        courier_minimum_rub = None
    class O:
        price = Decimal('492.27')
    async def stub_quote(*a):
        return None, '000000002', 'registered-addr', O(), '39619'
    async def never_send(*a, **k):
        raise AssertionError('External write must be blocked')
    class FakeSettings:
        app_env = 'development'
    monkeypatch.setattr(lab, 'live_quote', stub_quote)
    monkeypatch.setattr(lab, 'soap_request', never_send)
    monkeypatch.setattr(lab, 'get_settings', lambda: FakeSettings())
    assert await lab.submit(Args()) == 2
    Args.courier_terms_confirmed = True
    Args.courier_minimum_rub = '1000.00'
    assert await lab.submit(Args()) == 2
