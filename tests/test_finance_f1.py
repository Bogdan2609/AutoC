from datetime import date
from decimal import Decimal
import pytest
from app.modules.finance.tax_engine import PreviewInput, preview, money


def base(**kw):
    return {'gross_amount':'1220.00','operation_date':'2026-10-20','sale_model':'OWN',
            'operation_kind':'GOODS','vat_scheme':'STANDARD',**kw}


def test_vat_22():
    result=preview(PreviewInput(**base()))
    assert result['vat_amount']=='220.00' and result['net_amount']=='1000.00'
    assert result['production_approved'] is False


def test_special_vat_and_exempt():
    assert preview(PreviewInput(**base(gross_amount='1050',vat_scheme='USN_5')))['vat_amount']=='50.00'
    assert preview(PreviewInput(**base(gross_amount='1070',vat_scheme='USN_7')))['vat_amount']=='70.00'
    x=preview(PreviewInput(**base(vat_scheme='EXEMPT')))
    assert x['vat_rate'] is None and x['vat_status']=='WITHOUT_VAT'
    y=preview(PreviewInput(**base(tax_class='ZERO',supporting_document='Export')))
    assert y['vat_rate']=='0' and y['vat_status']=='ZERO_RATED'


def test_half_up_and_invalid_cases():
    assert money(Decimal('1.005'))==Decimal('1.01')
    for kw in [dict(operation_date='2025-12-31'),dict(vat_scheme='USN_5',tax_class='REDUCED_10',supporting_document='docs'),dict(tax_class='ZERO'),dict(operation_kind='AGENCY_FEE')]:
        with pytest.raises(ValueError):PreviewInput(**base(**kw))


@pytest.mark.asyncio
async def test_auth_and_empty_f1(client,admin_headers):
    assert (await client.get('/api/v1/backoffice/finance/overview')).status_code==403
    r=await client.get('/api/v1/backoffice/finance/overview',headers=admin_headers)
    assert r.status_code==200 and r.json()['entities']==[] and r.json()['production_tax_integration_enabled'] is False
    r=await client.get('/api/v1/backoffice/finance/tax-rules/2026',headers=admin_headers)
    assert r.status_code==200 and r.json()['standard_vat_percent']=='22'


@pytest.mark.asyncio
async def test_profile_draft_is_not_active(client,admin_headers):
    def post(path,data):return client.post('/api/v1/backoffice/finance/'+path,json=data,headers=admin_headers)
    r=await post('legal-entities',{'name':'Тестовая организация','entity_type':'company','roles':['seller']})
    assert r.status_code==201,r.text
    eid=r.json()['id']
    profile=await post('tax-profiles',{'entity_id':eid,'valid_from':'2026-10-20','regime':'USN_INCOME','vat_scheme':'UNRESOLVED'})
    assert profile.status_code==201,profile.text
    assert profile.json()['status']=='draft'
    activate=await post('tax-profiles/'+profile.json()['id']+'/activate',{'confirmed_by':'test_admin','confirm_tax_regime_and_external_income':True,'confirm_vat_choice_and_eligibility':True,'confirm_supporting_documents':True})
    assert activate.status_code==409
    result=await post('tax/preview',dict(base(tax_profile_id=profile.json()['id'])))
    assert result.status_code==409
    result=await post('tax/preview',base())
    assert result.status_code==200 and result.json()['vat_amount']=='220.00'
    audit=await client.get('/api/v1/backoffice/finance/tax/audit',headers=admin_headers)
    assert audit.status_code==200 and len(audit.json())==2


@pytest.mark.asyncio
async def test_contract_validation(client,admin_headers):
    url='/api/v1/backoffice/finance/legal-entities'
    r=await client.post(url,json={'name':'Company','entity_type':'company','inn':'123456789012'},headers=admin_headers)
    assert r.status_code==422
    r=await client.post(url,json={'name':'Company','entity_type':'company','kpp':'123456789'},headers=admin_headers)
    assert r.status_code==201
    r=await client.post('/api/v1/backoffice/finance/tax/preview',json=base(tax_class='REDUCED_10'),headers=admin_headers)
    assert r.status_code==422

@pytest.mark.asyncio
async def test_verified_profile_activation_historical_dates_and_overlap(client, admin_headers):
    async def post(p,body):
        return await client.post('/api/v1/backoffice/finance/'+p,json=body,headers=admin_headers)
    r=await post('legal-entities',{'name':'Тестовое юрлицо для F1', 'entity_type':'company','inn':'7707083893','roles':['seller']})
    assert r.status_code==201,r.text
    eid=r.json()['id']
    r=await post('legal-entities/'+eid+'/verify',{'administrator_attestation':True,'evidence_reference':'TEST-ONLY registration verification'})
    assert r.status_code==200,r.text
    body={'entity_id':eid,'regime':'USN_INCOME','vat_scheme':'USN_5',
          'valid_from':'2026-01-01','valid_until':'2026-12-31',
          'prior_year_income':'45000000.00','current_year_income':'41000000.00',
          'external_income_confirmed':True,'evidence_reference':'TEST-ONLY accountant attestation'}
    p=await post('tax-profiles',body)
    assert p.status_code==201,p.text
    pid=p.json()['id']
    r=await post('tax-profiles/'+pid+'/activate',{'confirmed_by':'TEST_ONLY_ADMIN',
        'confirm_tax_regime_and_external_income':True,'confirm_vat_choice_and_eligibility':True,
        'confirm_supporting_documents':True})
    assert r.status_code==200,r.text
    assert r.json()['status']=='active'
    r=await post('tax/preview',base(tax_profile_id=pid,vat_scheme='USN_5',gross_amount='105.00'))
    assert r.status_code==200,r.text
    assert r.json()['vat_amount']=='5.00' and r.json()['production_approved'] is False
    r=await post('tax/preview',base(tax_profile_id=pid,vat_scheme='STANDARD'))
    assert r.status_code==409,r.text
    competing=await post('tax-profiles',{**body,'valid_from':'2026-06-01'})
    assert competing.status_code==201,competing.text
    r=await post('tax-profiles/'+competing.json()['id']+'/activate',{'confirmed_by':'TEST_ONLY_ADMIN',
        'confirm_tax_regime_and_external_income':True,'confirm_vat_choice_and_eligibility':True,
        'confirm_supporting_documents':True})
    assert r.status_code==409,r.text
    r=await post('tax/preview',base(tax_profile_id=pid,vat_scheme='USN_5',operation_date='2027-01-01'))
    assert r.status_code==422
