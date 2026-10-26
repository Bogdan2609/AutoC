"""v0.31.6: one F2 arithmetic core; compatibility without charging or publication."""
from decimal import Decimal
from types import SimpleNamespace
import uuid
import random

import pytest
from pydantic import ValidationError

from app.modules.finance.pricing_engine import (
    PricePreview, calculate, cost_coverage_quote, fee_amount, simple_markup_quote,
)
from app.modules.pricing.service import calculate_retail
from app.modules.supplier_feeds.pricing import quote_unit


def approved_rule(**overrides):
    data = {
        'id': uuid.uuid4(), 'version': 1, 'markup_percent': Decimal('25'),
        'supplier_delivery': Decimal('30'), 'packaging': Decimal('10'),
        'other_costs': Decimal('5'), 'tax_reserve': Decimal('12'),
        'sbp_percent': Decimal('0.700'), 'sbp_fixed': Decimal('1'),
        'min_contribution': Decimal('100'),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_supplier_feed_and_f2_share_exact_cost_price_without_tax_guess():
    rule = approved_rule()
    cost = quote_unit(Decimal('1000.00'), rule)
    shadow = calculate(PricePreview(
        operation_date='2026-10-22', sale_model='OWN', source_gross='1000.00',
        strategy='COST_COVERAGE',markup_percent=rule.markup_percent,
        procurement_delivery=rule.supplier_delivery,packaging=rule.packaging,
        other_costs=rule.other_costs,tax_reserve=rule.tax_reserve,
        sbp_percent=rule.sbp_percent,sbp_fixed=rule.sbp_fixed,
        min_contribution=rule.min_contribution,
    ))
    assert cost['price'] == shadow['customer_gross']
    assert cost['sbp_fee'] == shadow['bank_fee']
    assert cost['contribution'] == shadow['cash_contribution_before_tax']
    assert cost['pricing_core'] == 'finance_f2_cost_coverage_v1'
    assert cost['payment_enabled'] is False
    assert shadow['production_approved'] is False
    assert shadow['net_contribution_estimate'] is None
    assert shadow['output_vat_estimate'] is None
    assert shadow['rounding_mode'] == 'COST_COVERAGE_KOPECK_UP'


def test_caps_cents_and_minimal_price_no_one_cent_cheaper():
    r = cost_coverage_quote(purchase_gross=Decimal('80.00'),markup_percent=Decimal('0'),
        sbp_percent=Decimal('0.7'), sbp_fixed=Decimal('1'), sbp_cap=Decimal('1.50'),
        min_contribution=Decimal('12.00'))
    price=Decimal(r['price']); fee=Decimal(r['bank_fee'])
    assert price-fee >= Decimal('92')
    cheaper=price-Decimal('0.01')
    assert cheaper-fee_amount(cheaper,Decimal('0.7'),Decimal('1'),Decimal('1.50'),rounding='CEILING') < Decimal('92')
    assert fee <= Decimal('1.50')


def test_near_100_percent_fee_does_not_spin_in_unbounded_kopeck_loop():
    q=cost_coverage_quote(purchase_gross=Decimal('1.00'),markup_percent=Decimal('0'),
                          sbp_percent=Decimal('99.999'),min_contribution=Decimal('1'))
    assert Decimal(q['cash_contribution']) >= Decimal('1.00')


def test_cost_coverage_rejects_unsafe_inputs_and_incompatible_shadow_modes():
    with pytest.raises(ValueError):
        cost_coverage_quote(purchase_gross=Decimal('100'),markup_percent=Decimal('10'),
                            sbp_percent=Decimal('100'))
    with pytest.raises(ValueError):
        cost_coverage_quote(purchase_gross=Decimal('NaN'),markup_percent=Decimal('10'))
    with pytest.raises(ValidationError):
        PricePreview(operation_date='2026-10-22',sale_model='OWN',source_gross='100',
                     strategy='COST_COVERAGE',floor_price='120')
    with pytest.raises(ValidationError):
        PricePreview(operation_date='2026-10-22',sale_model='AGENCY',source_gross='100',
                     strategy='COST_COVERAGE')


def test_old_simulator_is_unchanged_and_percent_only_math_is_one_source():
    old=calculate(PricePreview(operation_date='2026-10-22',sale_model='OWN',
                               source_gross='1000',markup_percent='25',sbp_percent='0.7'))
    assert old['customer_gross']=='1250.00'
    assert old['production_approved'] is False
    assert calculate_retail(Decimal('1000'),Decimal('25')) == simple_markup_quote(Decimal('1000'),Decimal('25'))


def test_cost_covered_quote_meets_cash_margin_across_randomized_prices():
    randomizer=random.Random(31516)
    for _ in range(120):
        purchase=Decimal(randomizer.randrange(1,1000000))/100
        markup=Decimal(randomizer.randrange(0,1001))/10
        pct=Decimal(randomizer.randrange(0,699))/1000
        fee=Decimal(randomizer.randrange(0,400))/100
        reserve=Decimal(randomizer.randrange(0,500))/100
        q=cost_coverage_quote(purchase_gross=purchase,markup_percent=markup,
            supplier_delivery=Decimal('35'),tax_reserve=reserve,
            sbp_percent=pct,sbp_fixed=fee,min_contribution=Decimal('50'))
        price=Decimal(q['price'])
        target=max(Decimal('50'),purchase*markup/100)
        base=purchase+Decimal('35')+reserve
        actual_fee=fee_amount(price,pct,fee,rounding='CEILING')
        assert price-actual_fee-base >= target
        previous=price-Decimal('.01')
        assert previous-fee_amount(previous,pct,fee,rounding='CEILING')-base < target

@pytest.mark.asyncio
async def test_f2_admin_cost_coverage_preview_is_nonproduction(client, admin_headers):
    r=await client.get('/api/v1/backoffice/finance/pricing/overview',headers=admin_headers)
    assert r.status_code == 200
    assert r.json()['shared_pricing_arithmetic_enabled'] is True
    assert r.json()['live_pricing_integration_enabled'] is False
    r=await client.post('/api/v1/backoffice/finance/pricing/preview',headers=admin_headers,json={
        'operation_date':'2026-10-22','sale_model':'OWN','strategy':'COST_COVERAGE',
        'source_gross':'1000.00','markup_percent':'25','procurement_delivery':'30',
        'packaging':'10','other_costs':'5','tax_reserve':'12',
        'sbp_percent':'0.7','sbp_fixed':'1','min_contribution':'100',
    })
    assert r.status_code == 200, r.text
    assert r.json()['customer_gross']==quote_unit(Decimal('1000'),approved_rule())['price']
    assert r.json()['production_approved'] is False
    assert r.json()['net_contribution_estimate'] is None
