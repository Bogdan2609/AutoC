"""Offline verification of account-scoped ROSSKO receiving destination -> company resolution."""
from types import SimpleNamespace
import pytest
from app.modules.marketplace.rossko_requisites import (
    RosskoRequisiteError, resolve_live_requisite, make_payment,
)
from app.modules.marketplace.account_credentials import for_account


def raw(*ids):
    companies = [{'id': value, 'name': 'NO_PII'} for value in ids]
    return {'CheckoutDetailsResult': {'success': True, 'CompanyList': {'company': companies}}}


def test_single_live_company_selected_without_operator_input():
    assert resolve_live_requisite(raw(39619)) == '39619'
    assert make_payment('1', resolve_live_requisite(raw(39619))) == {
        'payment_id': '1', 'requisite_id': '39619'}


def test_multicompany_requires_destination_mapping():
    with pytest.raises(RosskoRequisiteError, match='Multiple ROSSKO companies'):
        resolve_live_requisite(raw(11, 22))
    assert resolve_live_requisite(raw(11, 22), stored_requisite_id='22') == '22'


def test_destination_requisite_is_authoritative():
    with pytest.raises(RosskoRequisiteError, match='differs from requested'):
        resolve_live_requisite(raw(11,22), stored_requisite_id='22', supplied_requisite_id='11')
    with pytest.raises(RosskoRequisiteError, match='not present'):
        resolve_live_requisite(raw(11), stored_requisite_id='22')


def test_missing_and_unsuccessful_supplier_response_fails_closed():
    with pytest.raises(RosskoRequisiteError):
        resolve_live_requisite(raw())
    with pytest.raises(RosskoRequisiteError):
        resolve_live_requisite({'success':False, 'message':'PRIVATE'}, stored_requisite_id='39619')


def test_for_account_uses_destination_mapping_not_global_address(monkeypatch):
    from app.modules.marketplace import account_credentials
    settings = SimpleNamespace(supplier_api_enabled=True, rossko_key1='ONE',rossko_key2='TWO',
                               rossko_delivery_id='WRONG',rossko_address_id='WRONG')
    monkeypatch.setattr(account_credentials,'get_settings',lambda: settings)
    account=SimpleNamespace(credential_profile='default')
    dest=SimpleNamespace(external_address_id='ADDRESS_FROM_DB',
                         external_delivery_id='000000002',external_requisite_id='39619')
    cfg=for_account('rossko',account,dest)
    assert (cfg.rossko_delivery_id,cfg.rossko_address_id,cfg.rossko_requisite_id)==(
        '000000002','ADDRESS_FROM_DB','39619')


def test_nullable_mapping_preserves_existing_destinations():
    # The migration adds the nullable column without changing preexisting links.
    from sqlalchemy import inspect
    from app.modules.marketplace.models import SupplierDestination
    cols = {c.key for c in inspect(SupplierDestination).columns}
    assert {'account_id','node_id','external_address_id','external_delivery_id','external_requisite_id'}<=cols
    assert inspect(SupplierDestination).columns.external_requisite_id.nullable
