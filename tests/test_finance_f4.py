"""F4 isolated payments/refunds/receipts/bank matching, all local mock-only."""
import pytest
from decimal import Decimal
from app.modules.finance.payment_sim_engine import money,next_payment_status,reconcile

U='/api/v1/backoffice/finance/payments-sim'

def payment(code='001',amount='2000.00'):
    return {'sim_order_ref':'SIM:ORDER:'+code,'idempotency_key':'SIM:PAY:'+code,'amount':amount,'sale_model':'AGENCY'}

def event(key='PENDING',state='PENDING',amount='2000.00'):
    return {'event_key':'SIM:EVENT:'+key,'new_status':state,'amount':amount,'currency':'RUB','method':'MOCK_SBP'}

@pytest.mark.asyncio
async def test_auth_and_shadow_status(client,admin_headers):
    assert (await client.get(U+'/overview')).status_code==403
    assert (await client.post(U+'/payments',json=payment())).status_code==403
    r=await client.get(U+'/overview',headers=admin_headers)
    assert r.status_code==200 and r.json()['real_bank_enabled'] is False
    assert r.json()['real_refunds_enabled'] is False and r.json()['auto_ledger_postings'] is False

@pytest.mark.asyncio
async def test_shadow_payment_replay_and_status_gate(client,admin_headers):
    h=admin_headers
    r=await client.post(U+'/payments',json=payment(),headers=h);assert r.status_code==201,r.text
    p=r.json()['id'];assert r.json()['money_moved'] is False
    assert (await client.post(U+'/payments',json=payment(),headers=h)).json()['id']==p
    bad=payment();bad['amount']='1999.00'
    assert (await client.post(U+'/payments',json=bad,headers=h)).status_code==409
    bad=payment('001');bad['idempotency_key']='SIM:PAY:OTHER'
    assert (await client.post(U+'/payments',json=bad,headers=h)).status_code==409
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=event(amount='1999.00'),headers=h)).status_code==409
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=event(),headers=h)).status_code==201
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=event(),headers=h)).json()['duplicate'] is True
    changed=event();changed['new_status']='CANCELED'
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=changed,headers=h)).status_code==409
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=event('SUCCESS','SUCCEEDED'),headers=h)).status_code==201
    assert (await client.post(f'{U}/payments/{p}/mock-events',json=event('REGRESS','PENDING'),headers=h)).status_code==409
    assert (await client.get(f'{U}/payments/{p}',headers=h)).json()['status']=='SUCCEEDED'

@pytest.mark.asyncio
async def test_partial_refunds_fiscal_preview_and_matching(client,admin_headers):
    h=admin_headers
    p=(await client.post(U+'/payments',json=payment('002'),headers=h)).json()['id']
    assert (await client.post(f'{U}/payments/{p}/refunds',headers=h,json={'request_key':'SIM:REF:R0','amount':'10.00'})).status_code==409
    assert (await client.post(f'{U}/payments/{p}/mock-events',headers=h,json=event('DIRECT','SUCCEEDED'))).status_code==201
    receipt=lambda k,kind,amount:{'request_key':'SIM:RCPT:'+k,'kind':kind,'amount':amount,'vat_note':'UNCONFIRMED'}
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('0','OFFSET','2000.00'))).status_code==409
    pre=await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('1','PREPAYMENT','2000.00'))
    assert pre.status_code==201 and pre.json()['fiscalized'] is False
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('1','PREPAYMENT','2000.00'))).json()['id']==pre.json()['id']
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('2','OFFSET','1500.00'))).status_code==201
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('3','OFFSET','501.00'))).status_code==409
    r=await client.post(f'{U}/payments/{p}/refunds',headers=h,json={'request_key':'SIM:REF:A','amount':'700.00'})
    assert r.status_code==201,r.text;rid=r.json()['id']
    assert (await client.post(f'{U}/payments/{p}/refunds',headers=h,json={'request_key':'SIM:REF:B','amount':'1400.00'})).status_code==409
    assert (await client.post(f'{U}/refunds/{rid}/mock-result',headers=h,json={'status':'SUCCEEDED'})).status_code==200
    assert (await client.post(f'{U}/refunds/{rid}/mock-result',headers=h,json={'status':'REJECTED'})).status_code==409
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('4','REFUND','700.00'))).status_code==201
    assert (await client.post(f'{U}/payments/{p}/receipt-plans',headers=h,json=receipt('5','REFUND','1.00'))).status_code==409
    bank=await client.post(U+'/bank-lines',headers=h,json={'external_ref':'SIM:BANK:IN002','direction':'IN','amount':'2000.00'})
    assert bank.status_code==201;bid=bank.json()['id']
    assert (await client.post(f'{U}/bank-lines/{bid}/match',headers=h,json={'payment_id':p})).json()['status']=='MATCH'
    assert (await client.post(f'{U}/bank-lines/{bid}/match',headers=h,json={'payment_id':p})).json()['duplicate'] is True
    wrong=(await client.post(U+'/bank-lines',headers=h,json={'external_ref':'SIM:BANK:OUT002','direction':'IN','amount':'700.00'})).json()['id']
    assert (await client.post(f'{U}/bank-lines/{wrong}/match',headers=h,json={'refund_id':rid})).status_code==409
    actual=(await client.post(U+'/bank-lines',headers=h,json={'external_ref':'SIM:BANK:REF002','direction':'OUT','amount':'700.00'})).json()['id']
    assert (await client.post(f'{U}/bank-lines/{actual}/match',headers=h,json={'refund_id':rid})).json()['status']=='MATCH'
    assert len((await client.get(U+'/reconciliation',headers=h)).json()['lines'])==3

@pytest.mark.asyncio
async def test_rejected_refund_releases_budget_and_prohibits_fake_source(client,admin_headers):
    h=admin_headers
    bad=payment('INVALID');bad['sim_order_ref']='REAL:ORDER:12'
    assert (await client.post(U+'/payments',headers=h,json=bad)).status_code==422
    p=(await client.post(U+'/payments',headers=h,json=payment('003','100.00'))).json()['id']
    assert (await client.post(f'{U}/payments/{p}/mock-events',headers=h,json=event('C','CANCELED','100.00'))).status_code==201
    assert (await client.post(f'{U}/payments/{p}/mock-events',headers=h,json=event('P','SUCCEEDED','100.00'))).status_code==409
    q=(await client.post(U+'/payments',headers=h,json=payment('004','100.00'))).json()['id']
    assert (await client.post(f'{U}/payments/{q}/mock-events',headers=h,json=event('S4','SUCCEEDED','100.00'))).status_code==201
    refund=(await client.post(f'{U}/payments/{q}/refunds',headers=h,json={'request_key':'SIM:REF:004','amount':'100.00'})).json()['id']
    assert (await client.post(f'{U}/refunds/{refund}/mock-result',headers=h,json={'status':'REJECTED'})).status_code==200
    assert (await client.post(f'{U}/payments/{q}/refunds',headers=h,json={'request_key':'SIM:REF:004B','amount':'100.00'})).status_code==201
    assert (await client.post(U+'/bank-lines',headers=h,json={'external_ref':'BANK:REAL:001','direction':'IN','amount':'100.00'})).status_code==422


def test_money_invariants():
    assert money('0.01')==Decimal('0.01')
    for value in ('0','-2','NaN','Infinity','1.001'):
        with pytest.raises(ValueError):money(value)
    assert next_payment_status('CREATED','SUCCEEDED')
    assert not next_payment_status('SUCCEEDED','CANCELED')
    assert reconcile('OUT',Decimal('700'),Decimal('700'),'REFUND')=='MATCH'
    assert reconcile('IN',Decimal('700'),Decimal('700'),'REFUND')=='DIRECTION_MISMATCH'
