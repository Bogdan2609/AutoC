"""F2 shadow calculator, readonly safety and API persistence tests."""
import pytest
from decimal import Decimal
from pydantic import ValidationError
from app.modules.finance.pricing_engine import PricePreview, calculate, fee_amount


def own(**kw):
    return {'operation_date':'2026-10-20','sale_model':'OWN','source_gross':'1000.00', **kw}

def agent(**kw):
    return {'operation_date':'2026-10-20','sale_model':'AGENCY','source_gross':'2000.00', **kw}


def test_own_cash_and_known_vat():
    r=calculate(PricePreview(**own(markup_percent='40',last_mile_delivery='80',sbp_percent='0.7')))
    assert r['customer_gross']=='1512.00'
    assert r['bank_fee']=='10.58'
    assert r['cash_contribution_before_tax']=='421.42'
    assert r['net_contribution_estimate'] is None and r['production_approved'] is False
    r=calculate(PricePreview(**own(markup_percent='22',vat_scheme='STANDARD')))
    assert r['output_vat_estimate']=='220.00'
    assert r['net_contribution_estimate']=='0.00'
    assert r['profit_after_income_tax'] is None


def test_cash_land_cost_policy_min_floor():
    r=calculate(PricePreview(**own(source_gross='1000',markup_percent='10',procurement_delivery='100',packaging='30',floor_price='1500',min_contribution='500',vat_scheme='EXEMPT')))
    assert r['landed_cost_gross']=='1130.00' and r['customer_gross']=='1500.00'
    assert r['minimum_contribution_met'] is False
    assert any('minimum contribution' in x for x in r['warnings'])


def test_agent_revenue_and_payout():
    r=calculate(PricePreview(**agent(marketplace_percent='10',sbp_percent='0.7',vat_scheme='STANDARD')))
    assert r['platform_fee_gross']=='200.00' and r['seller_payout_gross']=='1800.00'
    assert r['bank_fee']=='14.00' and r['cash_contribution_before_tax']=='186.00'
    assert r['output_vat_estimate']=='36.07' and r['net_contribution_estimate']=='149.93'
    r2=calculate(PricePreview(**agent(marketplace_percent='10',sbp_percent='0.7',bank_fee_bearer='SELLER')))
    assert r2['seller_payout_gross']=='1786.00' and r2['cash_contribution_before_tax']=='200.00'


def test_bounded_fees_and_deductions():
    assert str(fee_amount(Decimal('2000'),Decimal('0.7'),cap=Decimal('10')))=='10'
    assert str(fee_amount(Decimal('50'),Decimal('0.4'),Decimal('1')))=='1.20'
    with pytest.raises(ValueError):fee_amount(Decimal('10'),Decimal('101'))
    with pytest.raises(ValidationError):PricePreview(**own(purchase_vat_deductible=True,vat_scheme='STANDARD',purchase_vat_rate='22'))
    r=calculate(PricePreview(**own(markup_percent='22',vat_scheme='STANDARD',purchase_vat_rate='22',purchase_vat_deductible=True,purchase_document_reference='UPD 123')))
    assert r['net_contribution_estimate']=='180.33'


def test_invalid_agent_and_year():
    for value in (agent(markup_percent='20'),agent(procurement_delivery='1'),agent(purchase_vat_rate='22'),own(operation_date='2027-01-01'),own(markup_percent='1001'),own(source_gross='-1')):
        with pytest.raises(ValidationError):PricePreview(**value)
    with pytest.raises(ValueError):calculate(PricePreview(**agent(source_gross='100',marketplace_percent='100',marketplace_fixed='2')))


@pytest.mark.asyncio
async def test_finance_f2_admin_only_api(client,admin_headers):
    prefix='/api/v1/backoffice/finance/pricing'
    assert (await client.get(prefix+'/overview')).status_code==403
    r=await client.get(prefix+'/overview',headers=admin_headers)
    assert r.status_code==200 and r.json()['live_pricing_integration_enabled'] is False
    assert r.json()['pricing_rules']==[] and r.json()['fee_schedules']==[]
    r=await client.post(prefix+'/rules',headers=admin_headers,json={'name':'Base 2026','valid_from':'2026-10-20','markup_percent':'20'})
    assert r.status_code==201,r.text
    rule=r.json()['id']
    assert r.json()['state']=='draft'
    r=await client.post(prefix+'/fees',headers=admin_headers,json={'name':'Bank test','fee_kind':'SBP','valid_from':'2026-10-20','percent':'0.4','evidence_reference':'Contract ABC'})
    assert r.status_code==201,r.text
    fee=r.json()['id']
    r=await client.post(prefix+'/preview',headers=admin_headers,json={**own(), 'pricing_rule_id':rule,'sbp_fee_schedule_id':fee})
    assert r.status_code==200,r.text
    assert r.json()['customer_gross']=='1200.00' and r.json()['bank_fee']=='4.80'
    assert r.json()['production_approved'] is False
    assert (await client.post(prefix+'/rules',headers=admin_headers,json={'name':'WrongScope','valid_from':'2026-10-20','scope_kind':'SUPPLIER','scope_id':'00000000-0000-0000-0000-000000000001'})).status_code==404
    assert (await client.post(prefix+'/rules/'+rule+'/retire',headers=admin_headers)).status_code==200
    assert (await client.post(prefix+'/preview',headers=admin_headers,json={**own(), 'pricing_rule_id':rule})).status_code==409
    assert (await client.post(prefix+'/fees/'+fee+'/retire',headers=admin_headers)).status_code==200


@pytest.mark.asyncio
async def test_f2_tax_profile_protection(client,admin_headers):
    prefix='/api/v1/backoffice/finance/pricing'
    r=await client.post(prefix+'/preview',headers=admin_headers,json={**own(vat_scheme='STANDARD'), 'tax_profile_id':'00000000-0000-0000-0000-000000000001'})
    assert r.status_code==409
    assert (await client.post(prefix+'/preview',json=own())).status_code==403


def test_rounding_smart_examples_and_margin_recalculation():
    from app.modules.finance.pricing_engine import customer_price_rounding
    for original,expected in [('48.20','49.00'),('101','101.00'),('501','505.00'),('1335','1340.00'),('4999','5000.00'),('8370','8400.00'),('32450','32500.00')]:
        r=customer_price_rounding(Decimal(original),mode='SMART_UP')
        assert r['customer_gross']==expected,(original,r)
    r=calculate(PricePreview(**own(source_gross='1000',markup_percent='33.5',rounding_mode='SMART_UP',sbp_percent='0.7',vat_scheme='STANDARD')))
    assert r['unrounded_customer_gross']=='1335.00'
    assert r['customer_gross']=='1340.00'
    assert r['rounding_uplift']=='5.00'
    assert r['bank_fee']=='9.38'
    assert r['output_vat_estimate']=='241.64'
    assert r['cash_contribution_before_tax']=='330.62'


def test_rounding_exact_and_price_ceiling():
    from app.modules.finance.pricing_engine import customer_price_rounding
    assert customer_price_rounding(Decimal('1335.47'))['customer_gross']=='1336.00'
    assert customer_price_rounding(Decimal('1335'))['customer_gross']=='1335.00'
    assert customer_price_rounding(Decimal('1335'), 'SMART_UP',price_ceiling=Decimal('1336'))['customer_gross']=='1335.00'
    r=calculate(PricePreview(**own(source_gross='1000',markup_percent='33.5',rounding_mode='SMART_UP',price_ceiling='1334')))
    assert r['price_ceiling_met'] is False and r['status']=='SIMULATION_CONSTRAINT_FAILED'


def test_rounding_ownership_and_invalid_tiers():
    with pytest.raises(ValidationError):PricePreview(**agent(rounding_mode='SMART_UP'))
    with pytest.raises(ValidationError):PricePreview(**own(rounding_tiers=[{'upper_exclusive':'100','step':'10','max_uplift_pct':'1'}]))
    with pytest.raises(ValidationError):PricePreview(**own(rounding_tiers=[{'upper_exclusive':None,'step':'0.50','max_uplift_pct':'1'}]))
    r=calculate(PricePreview(**agent()))
    assert r['rounding_mode']=='SELLER_PRICE_UNCHANGED'


@pytest.mark.asyncio
async def test_rounding_rule_saved_and_applied(client,admin_headers):
    url='/api/v1/backoffice/finance/pricing'
    r=await client.post(url+'/rules',headers=admin_headers,json={
      'name':'SmartRule','valid_from':'2026-10-20','markup_percent':'33.5',
      'rounding_mode':'SMART_UP',
      'rounding_tiers':[{'upper_exclusive':None,'step':'10','max_uplift_pct':'1'}]
    })
    assert r.status_code==201,r.text
    assert r.json()['rounding_mode']=='SMART_UP'
    rid=r.json()['id']
    r=await client.post(url+'/preview',headers=admin_headers,json={**own(),'pricing_rule_id':rid})
    assert r.status_code==200,r.text
    assert r.json()['customer_gross']=='1340.00' and r.json()['rounding_uplift']=='5.00'


def test_rounding_never_silently_violates_uplift_cap():
    r=calculate(PricePreview(**own(source_gross='1000.01',rounding_mode='SMART_UP',rounding_max_uplift_pct='0')))
    assert r['status']=='SIMULATION_CONSTRAINT_FAILED'
    assert r['rounding_uplift_cap_met'] is False
    assert r['customer_gross']=='1001.00'
