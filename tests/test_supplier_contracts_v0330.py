"""Contract regression, pure/offline: NEVER touch supplier or bank networks."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest

from app.modules.offer_engine.providers import Unavailable, require_rub, parse_rossko_search_result
from app.modules.marketplace.purchase_gateway import (
    PurchaseIntent, InvalidPurchasePlan, build_purchase_plan,
)


def intent(supplier='partkom'):
    return PurchaseIntent(supplier, 'buyer-line-1', 'buyer_line_101', 'TRIALLI', 'CF705', 3,
                          Decimal('492.27'), Decimal('500.00'), 'live-confirmed-token',
                          warehouse='WH01', minimum=2, multiple=1,
                          quote_metadata={'maker_id': '19', 'provider_id': '22'},
                          external_line_id=15001, external_order_id=15002,
                          delivery_mode='courier', courier_route_verified=True)


def test_rossko_failed_request_is_error_not_negative_cache():
    with pytest.raises(Unavailable):
        parse_rossko_search_result({'success': False, 'message': 'Temporary error'}, 'TRIALLI', 'CF705')
    assert parse_rossko_search_result({'success': True, 'PartsList': {'Part': []}}, 'TRIALLI', 'CF705') == []


@pytest.mark.parametrize('currency', ['руб', 'РУБ.', '₽', 'RUB', '643'])
def test_adeo_valid_rub(currency):
    require_rub(currency)


@pytest.mark.parametrize('currency', ['', 'USD', 'EUR', 'CNY', None])
def test_adeo_unknown_currency_fails_closed(currency):
    with pytest.raises(Unavailable):
        require_rub(currency)


@pytest.mark.parametrize('supplier', ['rossko', 'adeo', 'partkom', 'forum-auto'])
def test_supplier_plans_are_side_effect_free_and_never_authorize_dispatch(supplier):
    plan = build_purchase_plan(intent(supplier))
    assert plan.supplier == supplier
    assert plan.dispatch_authorized is False
    assert plan.requires_paid_line_validation is True
    assert 'password' not in str(plan.payload).lower()
    assert 'login' not in str(plan.payload).lower()
    if supplier == 'partkom':
        assert plan.payload['orderItems[0][reorderAgreement]'] == '0'
        assert plan.payload['orderItems[0][possibleIncreasePrice]'] == '0'
        assert plan.payload['flagTest'] == '1'
    if supplier == 'forum-auto':
        assert plan.payload['eid'] == 15001
    if supplier == 'adeo':
        assert plan.payload['basket'][0]['internal_n_orders'] == 15002


@pytest.mark.parametrize('changes', [
    {'quantity': 0}, {'quantity': 1}, {'purchase_price': Decimal('500.01')},
    {'purchase_price': Decimal('NaN')}, {'maximum_approved_price': Decimal('Infinity')},
    {'minimum': 0}, {'multiple': 0}, {'reference': ''}, {'buyer_order_line_id': ''},
])
def test_invalid_order_is_rejected(changes):
    with pytest.raises(InvalidPurchasePlan):
        build_purchase_plan(replace(intent(), **changes))


def test_missing_durable_supplier_identifiers_refused():
    for code, changes in [('adeo', {'external_order_id': None}),
                          ('forum-auto', {'external_line_id': None}),
                          ('partkom', {'quote_metadata': {}}),
                          ('rossko', {'warehouse': ''})]:
        with pytest.raises(InvalidPurchasePlan):
            build_purchase_plan(replace(intent(code), **changes))


def test_partkom_minimum_not_assumed_to_be_pack_multiple():
    # A minimum of 2 still permits an order of 3 when packaging multiple=1.
    plan = build_purchase_plan(intent('partkom'))
    assert plan.payload['orderItems[0][quantity]'] == '3'


@pytest.mark.parametrize('supplier', ['rossko', 'adeo', 'partkom', 'forum-auto'])
@pytest.mark.parametrize('delivery_mode,verified', [('pickup', True), ('courier', False), ('unverified', False)])
def test_all_supplier_purchase_plans_block_unverified_or_pickup_route(supplier, delivery_mode, verified):
    with pytest.raises(InvalidPurchasePlan, match='verified courier'):
        build_purchase_plan(replace(intent(supplier), delivery_mode=delivery_mode,
                                    courier_route_verified=verified))
