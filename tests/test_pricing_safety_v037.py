"""v0.37 regression: documented landed costs, floors, fees and shadow VAT."""
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
import pytest
from pydantic import ValidationError

from app.modules.finance.pricing_engine import PricePreview, calculate, cost_coverage_quote, fee_amount, simple_markup_quote
from app.modules.finance.pricing_api import FeeInput, RuleInput
from app.modules.supplier_feeds.control_api import PricePolicyInput
from app.modules.supplier_feeds.pricing import quote_unit


def req(**changes):
    raw = dict(operation_date='2026-10-26', sale_model='OWN', source_gross='1000', markup_percent='20')
    raw.update(changes)
    return PricePreview(**raw)


def rule(**changes):
    raw=dict(id='test',version=2,markup_percent=Decimal('20'),supplier_delivery=Decimal('30'),
        last_mile_delivery=Decimal('80'),packaging=Decimal('5'),other_costs=Decimal('0'),
        tax_reserve=Decimal('10'),sbp_percent=Decimal('0.7'),sbp_fixed=Decimal('1'),
        min_contribution=Decimal('100'))
    raw.update(changes)
    return SimpleNamespace(**raw)


def test_feed_and_cost_coverage_include_last_mile_in_gross_and_target_margin():
    r=rule()
    q=quote_unit(Decimal('1000.00'),r)
    shared=cost_coverage_quote(purchase_gross=Decimal('1000'),markup_percent=r.markup_percent,
        supplier_delivery=r.supplier_delivery,last_mile_delivery=r.last_mile_delivery,
        packaging=r.packaging,other_costs=r.other_costs,tax_reserve=r.tax_reserve,
        sbp_percent=r.sbp_percent,sbp_fixed=r.sbp_fixed,min_contribution=r.min_contribution)
    assert q['price']==shared['price']
    assert q['last_mile_delivery']=='80'
    assert Decimal(q['price']) - Decimal(q['sbp_fee']) - Decimal('1125') >= Decimal('200')
    assert Decimal(q['price']) > Decimal(quote_unit(Decimal('1000'),rule(last_mile_delivery=Decimal('0')))['price'])


def test_old_fixture_missing_last_mile_stays_compatible_but_zero_is_explicit():
    data=vars(rule()).copy();data.pop('last_mile_delivery')
    assert quote_unit(Decimal('1000'),SimpleNamespace(**data))['last_mile_delivery']=='0'


def test_markup_preview_adds_tax_reserve_to_cost_base_and_last_mile():
    r=calculate(req(procurement_delivery='20',last_mile_delivery='60',packaging='10',tax_reserve='10'))
    assert r['customer_gross']=='1320.00'  # (1000+20+60+10+10)*1.2
    assert r['landed_cost_gross']=='1100.00'


def test_no_silent_negative_cash_contribution_even_when_vat_unknown():
    r=calculate(req(markup_percent='0',sbp_percent='10',sbp_fixed='5'))
    assert r['status']=='SIMULATION_CONSTRAINT_FAILED'
    assert Decimal(r['cash_contribution_before_tax']) < 0
    assert r['cash_minimum_contribution_met'] is False
    assert r['minimum_contribution_met'] is None
    assert r['production_approved'] is False


def test_tax_aware_margin_floor_blocks_below_required_net_contribution():
    r=calculate(req(markup_percent='25',vat_scheme='STANDARD',min_contribution='210'))
    assert r['cash_minimum_contribution_met'] is True
    assert r['minimum_contribution_met'] is False
    assert r['status']=='SIMULATION_CONSTRAINT_FAILED'


def test_optional_max_delta_flags_overshoot_instead_of_silently_clamping():
    r=calculate(req(markup_percent='50', max_price_delta_pct='20'))
    assert r['max_price_delta_met'] is False
    assert r['status']=='SIMULATION_CONSTRAINT_FAILED'
    assert r['customer_gross']=='1500.00'
    allowed=calculate(req(markup_percent='20',max_price_delta_pct='20'))
    assert allowed['max_price_delta_met'] is True


def test_sbp_price_policy_and_fee_inputs_are_capped_without_guessing_contract():
    from uuid import uuid4
    scope=dict(supplier_code='rossko',account_id=uuid4(),destination_id=uuid4(),warehouse_code='X1',
        markup_percent='10',supplier_delivery='10',packaging='0',other_costs='0',tax_reserve='0',
        sbp_fixed='0',min_contribution='0',evidence_reference='signed supplier contract',sbp_percent='0.7')
    assert PricePolicyInput(**scope).last_mile_delivery == Decimal('0')
    with pytest.raises(ValidationError):PricePolicyInput(**{**scope,'sbp_percent':'11'})
    with pytest.raises(ValidationError):PricePolicyInput(**{**scope,'markup_percent':'600'})
    with pytest.raises(ValidationError):FeeInput(name='High bank',fee_kind='SBP',percent='11',valid_from='2026-10-26',evidence_reference='signed approved bank tariff')
    assert FeeInput(name='Marketplace',fee_kind='MARKETPLACE',percent='11',valid_from='2026-10-26',evidence_reference='documented schedule').percent==Decimal('11')
    with pytest.raises(ValueError):quote_unit(Decimal('1000'),rule(sbp_percent=Decimal('11')))
    with pytest.raises(ValueError):quote_unit(Decimal('1000'),rule(markup_percent=Decimal('501')))


@pytest.mark.parametrize('input_value', [0.7, float('nan'), '10', None])
def test_fee_rejects_non_decimal_values(input_value):
    with pytest.raises(ValueError):fee_amount(Decimal('100'),input_value)


def test_fee_cap_must_be_kopeck_amount_and_percent_is_decimal():
    with pytest.raises(ValueError):fee_amount(Decimal('100'),Decimal('1'),cap=Decimal('0.005'))
    assert fee_amount(Decimal('100'),Decimal('1'),cap=Decimal('0.50'))==Decimal('0.50')
    with pytest.raises(ValueError):simple_markup_quote(1000,Decimal('10'))


def test_migration_revokes_old_approvals():
    p=Path(__file__).resolve().parents[1]/'migrations/versions/20261026_0037_last_mile_feed_pricing.py'
    data=p.read_text()
    assert 'UPDATE feed_price_rules SET approved = false' in data
    assert 'last_mile_delivery' in data
    assert '20261026_0036' in data


def test_vat_exclusive_feed_is_not_mislabeled_as_gross_cost():
    from app.modules.supplier_feeds.pricing import usable_rule
    verified = SimpleNamespace(approved=True,tax_status_verified='excluded')
    source = SimpleNamespace(commercial_verified=True,price_kind='purchase',currency='RUB',tax_status='excluded')
    assert usable_rule(verified,source) is False
    verified.tax_status_verified='included'
    source.tax_status='included'
    assert usable_rule(verified,source) is True


def test_alembic_uses_metadata_constraint_name_for_postgres():
    """The original migration used a naming convention; do not drop the raw label."""
    from io import StringIO
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from app.core.db import Base
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={
        'as_sql': True, 'target_metadata': Base.metadata, 'output_buffer': output,
    })
    operations = Operations(context)
    operations.drop_constraint('feed_price_nonnegative_costs','feed_price_rules',type_='check')
    operations.create_check_constraint('feed_price_nonnegative_costs','feed_price_rules',
                                       'last_mile_delivery >= 0')
    sql = output.getvalue()
    assert 'DROP CONSTRAINT ck_feed_price_rules_feed_price_nonnegative_costs' in sql
    assert 'ADD CONSTRAINT ck_feed_price_rules_feed_price_nonnegative_costs' in sql
