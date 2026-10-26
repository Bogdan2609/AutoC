"""A1 analytics is read-only: operational activity is not certified revenue."""
import pytest
from app.modules.analytics.api import money

U='/api/v1/backoffice/analytics'

@pytest.mark.asyncio
async def test_admin_only_and_empty_scopes(client,admin_headers):
    assert (await client.get(U+'/overview')).status_code==403
    assert (await client.get(U+'/order-trend')).status_code==403
    r=await client.get(U+'/overview',headers=admin_headers)
    assert r.status_code==200,r.text
    j=r.json()
    assert j['version']=='A1_READ_ONLY' and j['financial_reconciliation']=='NOT_CONNECTED'
    assert j['verified_revenue_available'] is False and j['verified_profit_available'] is False
    assert j['catalog']['products_total']==0 and j['catalog']['categories_total']==0
    assert j['operations']['revenue_verified_rub'] is None
    assert j['operations']['net_profit_verified_rub'] is None
    assert j['operations']['final_quote_order_value_rub']=='0.00'
    assert j['suppliers']['suppliers']==[]
    assert j['simulations']['included_in_verified_financials'] is False
    assert j['simulations']['f4']['simulated_payments']==0
    assert (await client.get(U+'/order-trend',headers=admin_headers)).json()['series']==[]

@pytest.mark.asyncio
async def test_window_limits_and_no_mutation(client,admin_headers):
    for bad in ('0','91','-2','text'):
        assert (await client.get(U+'/overview?days='+bad,headers=admin_headers)).status_code==422
    assert (await client.post(U+'/overview',headers=admin_headers)).status_code==405
    assert (await client.get(U+'/overview?days=1',headers=admin_headers)).status_code==200
    assert (await client.get(U+'/order-trend?days=90',headers=admin_headers)).status_code==200

@pytest.mark.asyncio
async def test_shadow_events_not_real_revenue(client,admin_headers):
    h=admin_headers;f4='/api/v1/backoffice/finance/payments-sim'
    p=await client.post(f4+'/payments',headers=h,json={
        'sim_order_ref':'SIM:ORDER:ANALYTICS-A1','idempotency_key':'SIM:PAY:ANALYTICS-A1',
        'amount':'250000.00','sale_model':'AGENCY'})
    assert p.status_code==201,p.text
    r=await client.get(U+'/overview',headers=h)
    j=r.json()
    assert j['simulations']['f4']['simulated_payments']==1
    assert j['operations']['orders_total']==0
    assert j['operations']['provider_reported_succeeded_rub']=='0.00'
    assert j['operations']['revenue_verified_rub'] is None
    assert j['operations']['net_profit_verified_rub'] is None

@pytest.mark.asyncio
async def test_auth_refusal_before_access(client):
    wrong={'X-Admin-Token':'bad-token'}
    assert (await client.get(U+'/overview',headers=wrong)).status_code==403
    assert (await client.get(U+'/order-trend',headers=wrong)).status_code==403


def test_money_precision_and_no_default_revenue():
    from decimal import Decimal
    assert money(Decimal('0'))=='0.00'
    assert money(Decimal('1335.47'))=='1335.47'
    assert money(None)=='0.00'
