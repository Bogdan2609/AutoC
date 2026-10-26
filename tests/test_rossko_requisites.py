"""All tests are offline and never send supplier orders."""
import pytest
from app.modules.marketplace.rossko_requisites import (
    RosskoRequisiteError, live_requisite_ids, require_live_requisite, make_payment,
)


def response(companies):
    return {'CheckoutDetailsResult': {'success': True, 'CompanyList': {'company': companies}}}


def test_real_shape_single_company_id():
    raw = response({'id': 39619, 'name': 'PRIVATE', 'requisite': 'PRIVATE TAX INFO'})
    assert live_requisite_ids(raw) == ['39619']
    assert require_live_requisite(raw, '39619') == '39619'
    assert make_payment(1, '39619') == {'payment_id': '1', 'requisite_id': '39619'}


def test_multiple_companies_need_exact_selection():
    raw = response([{'id': '32'}, {'id': '12'}, {'id': '32'}, {'id': 'bad'}])
    assert live_requisite_ids(raw) == ['12', '32']
    with pytest.raises(RosskoRequisiteError, match='not present'):
        require_live_requisite(raw, '39619')


def test_missing_requisite_fails_closed():
    raw = {'CheckoutDetailsResult': {'success': True}}
    assert live_requisite_ids(raw) == []
    with pytest.raises(RosskoRequisiteError):
        require_live_requisite(raw, None)
    with pytest.raises(RosskoRequisiteError):
        require_live_requisite(raw, '39619')


def test_supplier_error_rejected():
    with pytest.raises(RosskoRequisiteError):
        live_requisite_ids({'success': False, 'message': 'PRIVATE TOKEN'})


def test_malformed_id_and_payment_are_rejected():
    raw = response([{'id': -1}, {'id': True}, {'id': '1.5'}, {'id': '0'}, {'id': '39619'}])
    assert live_requisite_ids(raw) == ['39619']
    with pytest.raises(RosskoRequisiteError):
        require_live_requisite(raw, '000')
    with pytest.raises(RosskoRequisiteError):
        make_payment('x', '39619')
    with pytest.raises(RosskoRequisiteError):
        make_payment('1', '')


def test_does_not_include_names_or_tax_numbers():
    raw = response({'id': '39619', 'name': 'DO_NOT_DISCLOSE', 'requisite': 'DO_NOT_DISCLOSE'})
    assert live_requisite_ids(raw) == ['39619']
