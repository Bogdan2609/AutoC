"""F3 shadow ledger persistence, double-entry, idempotency, reversal, settlement tests."""
import pytest
from decimal import Decimal
from pydantic import ValidationError
from app.modules.finance.ledger_engine import canonical_lines, signed_balance
from app.modules.finance.ledger_api import LineInput, EntryInput

U='/api/v1/backoffice/finance/ledger'

async def make_book(client,h,code='SIM-BOOK-A'):
    r=await client.post(U+'/books',headers=h,json={'code':code,'name':'Simulation ledger'})
    assert r.status_code==201,r.text
    return r.json()['id']

async def make_account(client,h,book,code,kind,role='NONE'):
    r=await client.post(f'{U}/books/{book}/accounts',headers=h,json={'code':code,'title':'Test account', 'kind':kind,'settlement_role':role})
    assert r.status_code==201,r.text
    return r.json()['id']


def entry(a,b,ref='SIM:PAY:123',key='SIM-PAY-123',amount='2000.00',kind='PAYMENT'):
    return {'idempotency_key':key,'source_ref':ref,'event_kind':kind,'memo':'Test market sale',
           'lines':[{'account_id':a,'side':'DEBIT','amount':amount},{'account_id':b,'side':'CREDIT','amount':amount}]}


def test_exact_money_validation():
    with pytest.raises(ValueError):canonical_lines([LineInput(account_id='00000000-0000-0000-0000-000000000001',side='DEBIT',amount=1),LineInput(account_id='00000000-0000-0000-0000-000000000001',side='CREDIT',amount=1)])
    assert signed_balance('LIABILITY',Decimal('200'),Decimal('2000'))==Decimal('1800')
    assert signed_balance('ASSET',Decimal('2000'),Decimal('1800'))==Decimal('200')
    with pytest.raises(ValidationError):EntryInput(**entry('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002',amount='0.001'))
    with pytest.raises(ValueError):canonical_lines([LineInput(account_id='00000000-0000-0000-0000-000000000001',side='DEBIT',amount=1),LineInput(account_id='00000000-0000-0000-0000-000000000002',side='CREDIT',amount=2)])


@pytest.mark.asyncio
async def test_f3_admin_only_and_empty(client,admin_headers):
    assert (await client.get(U+'/overview')).status_code==403
    r=await client.get(U+'/overview',headers=admin_headers)
    assert r.status_code==200 and r.json()['books']==[] and r.json()['shadow_only'] is True
    assert r.json()['payouts_enabled'] is False
    assert (await client.post(U+'/books',json={'code':'SIM-NO-AUTH','name':'Oops'})).status_code==403


@pytest.mark.asyncio
async def test_f3_double_entry_payment_commission_and_refund(client,admin_headers):
    h=admin_headers;book=await make_book(client,h)
    clearing=await make_account(client,h,book,'CLEARING','ASSET')
    payable=await make_account(client,h,book,'SELLER_DUE','LIABILITY','SELLER_PAYABLE')
    revenue=await make_account(client,h,book,'COMM_FEE','INCOME')
    expense=await make_account(client,h,book,'SBP_FEE','EXPENSE')
    r=await client.post(f'{U}/books/{book}/entries',headers=h,json=entry(clearing,payable))
    assert r.status_code==201,r.text
    payment_id=r.json()['id']
    r2=await client.post(f'{U}/books/{book}/entries',headers=h,json=entry(clearing,payable))
    assert r2.status_code==201 and r2.json()['id']==payment_id
    changed=entry(clearing,payable);changed['lines'][0]['amount']='1999.00';changed['lines'][1]['amount']='1999.00'
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=changed)).status_code==409
    commission=entry(payable,revenue,ref='SIM:COMM:123',key='SIM-COMM-123',amount='200.00',kind='COMMISSION')
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=commission)).status_code==201
    bank=entry(expense,clearing,ref='SIM:BANK:123',key='SIM-BANK-123',amount='14.00',kind='BANK_FEE')
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=bank)).status_code==201
    rows=(await client.get(f'{U}/books/{book}/trial-balance',headers=h)).json()
    assert rows['balanced'] is True and rows['total_debit']==rows['total_credit']=='2214.00'
    bal={a['code']:a['balance'] for a in rows['accounts']}
    assert bal=={'CLEARING':'1986.00','SELLER_DUE':'1800.00','COMM_FEE':'200.00','SBP_FEE':'14.00'}
    positions=(await client.get(f'{U}/books/{book}/settlement-positions',headers=h)).json()
    assert len(positions['positions'])==1 and positions['positions'][0]['balance']=='1800.00'
    r=await client.post(f'{U}/books/{book}/entries/{payment_id}/reverse',headers=h,json={'idempotency_key':'SIM-REV-123','reason':'Customer reversed payment'})
    assert r.status_code==201,r.text
    assert r.json()['reverses_entry_id']==payment_id
    r2=await client.post(f'{U}/books/{book}/entries/{payment_id}/reverse',headers=h,json={'idempotency_key':'SIM-REV-123','reason':'Customer reversed payment'})
    assert r2.status_code==201 and r2.json()['id']==r.json()['id']
    assert (await client.post(f'{U}/books/{book}/entries/{payment_id}/reverse',headers=h,json={'idempotency_key':'SIM-REV-999','reason':'Duplicate refund'})).status_code==409
    trial=(await client.get(f'{U}/books/{book}/trial-balance',headers=h)).json()
    assert trial['balanced'] is True
    bal={a['code']:a['balance'] for a in trial['accounts']}
    assert bal['CLEARING']=='-14.00' and bal['SELLER_DUE']=='-200.00'


@pytest.mark.asyncio
async def test_f3_rejects_unbalanced_crossbook_external_replay(client,admin_headers):
    h=admin_headers;book=await make_book(client,h);other=await make_book(client,h,'SIM-BOOK-B')
    cash=await make_account(client,h,book,'CASH','ASSET')
    debt=await make_account(client,h,book,'DEBT','LIABILITY','SUPPLIER_PAYABLE')
    foreign=await make_account(client,h,other,'DEBT','LIABILITY')
    bad=entry(cash,debt);bad['lines'][1]['amount']='1999.00'
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=bad)).status_code==422
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=entry(cash,foreign))).status_code==409
    bad2=entry(cash,debt);bad2['source_ref']='ORDER:ACTUAL:123'
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=bad2)).status_code==422
    bad3=entry(cash,debt);bad3['idempotency_key']='SIM-PAY-124'
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=entry(cash,debt))).status_code==201
    assert (await client.post(f'{U}/books/{book}/entries',headers=h,json=bad3)).status_code==409
    assert (await client.post(f'{U}/books/{book}/accounts',headers=h,json={'code':'BAD1','title':'Wrong','kind':'ASSET','settlement_role':'SELLER_PAYABLE'})).status_code==422


@pytest.mark.asyncio
async def test_f3_reconcile_is_readonly(client,admin_headers):
    h=admin_headers;b=await make_book(client,h)
    a=await make_account(client,h,b,'CASH','ASSET');pay=await make_account(client,h,b,'PAY','LIABILITY')
    posted=(await client.post(f'{U}/books/{b}/entries',headers=h,json=entry(a,pay))).json()
    request={'entry_id':posted['id'],'external_reference':'SIM-bank-report-A','expected_amount':'2000.00'}
    r=await client.post(f'{U}/books/{b}/reconcile-preview',headers=h,json=request)
    assert r.status_code==200 and r.json()['status']=='MATCH'
    request['expected_amount']='1999.00'
    assert (await client.post(f'{U}/books/{b}/reconcile-preview',headers=h,json=request)).json()['status']=='MISMATCH'
    assert len((await client.get(f'{U}/books/{b}/entries',headers=h)).json())==1
