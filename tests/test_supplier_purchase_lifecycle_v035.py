"""v0.35 regressions never contact real supplier or payment services."""
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID
from types import SimpleNamespace

import pytest

from app.modules.marketplace.purchase_lifecycle import (
    capabilities, capability_list, verify_rossko_orders, read_rossko_orders,
)
from app.modules.marketplace.procurement import SupplierPurchaseAttempt, normalize_checkout_result

ORDER = UUID('8aab99ae-2dda-4b95-91d7-82326621d59f')


def response(*orders):
    return {'OrdersResult': {'success': True, 'OrdersList': {'Order': list(orders)}}}


def remote(id='123', qty=2, price='99.00', comment=None, brand='Trialli', article='CF 705'):
    line = {'brand': brand, 'partnumber': article, 'count': qty, 'price': price, 'currency': 'RUB'}
    if comment is not None: line['comment'] = comment
    return {'id': id, 'parts': {'part': [line]}}


def check(raw, ids=None, **kw):
    return verify_rossko_orders(raw, external_ids=ids or ['123'], brand='Trialli',
                                article='CF 705', quantity=2,
                                approved_unit_price=Decimal('100.00'),
                                buyer_order_id=ORDER, **kw)


def test_capabilities_are_explicit_and_fail_closed():
    all_codes = {x.supplier for x in capability_list()}
    assert all_codes == {'rossko', 'adeo', 'partkom', 'forum-auto'}
    assert capabilities('rossko').live_create and capabilities('rossko').live_order_lookup
    for name in ['adeo','partkom','forum-auto']:
        entry = capabilities(name)
        assert entry.preview and not entry.live_create and not entry.live_order_lookup
        assert not entry.live_cancel
    assert not capabilities('rossko').live_cancel
    with pytest.raises(ValueError): capabilities('unknown')


def test_rossko_ack_not_remote_verification():
    assert normalize_checkout_result({'CheckoutResult':{'success':True,
        'OrderIDS':{'id':123}}}) == ('accepted', ['123'], None)
    assert SupplierPurchaseAttempt.__table__.c.verified_at is not None



def test_readback_exact_qty_sku_price_uuid():
    result = check(response(remote(comment=str(ORDER))))
    assert result.result == 'verified' and result.found_ids == ('123',)
    assert check(response(remote(comment='ANOTHER'))).reason == 'order_uuid_mismatch'
    assert check(response(remote(price='100.01'))).result == 'manual_review'
    assert check(response(remote(qty=3))).reason == 'order_quantity_or_price_mismatch'
    assert check(response(remote(brand='Other'))).reason == 'unexpected_order_item'
    without_currency=remote(); without_currency['parts']['part'][0].pop('currency')
    assert check(response(without_currency)).reason == 'unsupported_order_currency'
    assert check(response(remote(price='99.00'))).result == 'verified'
    assert check(response(remote(article='Wrong'))).reason == 'unexpected_order_item'
    cancelled=remote(); cancelled['status']='cancelled'
    assert check(response(cancelled)).reason == 'remote_order_not_active'
    other_currency=remote(); other_currency['currency']='EUR'
    assert check(response(other_currency)).reason == 'unsupported_order_currency'


def test_getorders_page_not_proof_of_missing_order():
    assert check(response()).result == 'pending'
    assert check(response(remote(id='999'))).reason == 'supplier_order_not_visible'
    assert check(response(remote(id='123'),remote(id='123'))).reason == 'duplicate_remote_order_id'
    assert check(response(remote(id='123')),ids=['123','124']).result == 'pending'


@pytest.mark.parametrize('bad', [None, [], 1, {}, {'OrdersResult':{'success':False}},
    {'OrdersResult':{'success':True,'OrdersList':{'Order':['not dict']}}},
    {'OrdersResult':{'success':True,'OrdersList':None}}])
def test_response_malformed_never_verifies(bad):
    assert check(bad).result != 'verified'


@pytest.mark.parametrize('bad', ['NaN','Infinity','-1','0','100.001', 90.0, '', None,True])
def test_invalid_money_fails_closed(bad):
    assert check(response(remote(price=bad))).result != 'verified'


@pytest.mark.parametrize('bad', ['2.4','NaN','Infinity','0',0,-1,None,True])
def test_invalid_quantities_fail_closed(bad):
    assert check(response(remote(qty=bad))).result != 'verified'


def test_invalid_ids_duplicate_and_reference_fail_closed():
    assert check(response(remote()),ids=['0']).result == 'manual_review'
    assert check(response(remote()),ids=['abc']).result == 'manual_review'
    assert check(response(remote()),ids=['123','123']).result == 'manual_review'
    assert verify_rossko_orders(response(remote()),external_ids=['123'],brand='Trialli',
       article='CF 705',quantity=2,approved_unit_price=Decimal('100.00'),
       buyer_order_id='not-a-uuid').result == 'manual_review'


@pytest.mark.asyncio
async def test_no_real_rossko_getorders_in_test():
    with pytest.raises(RuntimeError,match='test environment'):
        await read_rossko_orders(SimpleNamespace(rossko_key1='x',rossko_key2='y'))


@pytest.mark.asyncio
async def test_public_route_never_exposes_contacts(client, admin_headers):
    response = await client.get('/api/v1/admin/supplier-purchases/capabilities', headers=admin_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()['suppliers'])==4
    assert response.json()['supplier_writes_triggered'] is False
    no_auth = await client.get('/api/v1/admin/supplier-purchases/capabilities')
    assert no_auth.status_code in (401,403)
    sample = response.json()
    assert 'secret' not in str(sample).lower()


@pytest.mark.asyncio
async def test_reconcile_never_uses_real_network_when_globally_disabled(client, admin_headers):
    response = await client.post('/api/v1/admin/supplier-purchases/'+str(ORDER)+'/reconcile', headers=admin_headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_cancellation_does_not_send_supplier_request_for_unknown_attempt(client, admin_headers):
    response = await client.post('/api/v1/admin/supplier-purchases/'+str(ORDER)+'/request-cancellation',headers=admin_headers)
    assert response.status_code==404
