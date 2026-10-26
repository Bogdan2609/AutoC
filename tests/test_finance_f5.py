"""F5 preview tax records; never official filing, no bank requests or tax-profile mutations."""
from datetime import date
from decimal import Decimal
import pytest
from app.modules.finance.register_engine import calculate_vat, export_lines

R='/api/v1/backoffice/finance/tax-registers-sim'

def book(code,role):return {'book_ref':'SIM:F5:BOOK:'+code,'actor_role':role,'period_start':'2026-10-01','period_end':'2026-12-31'}
def doc(code,kind):return {'document_ref':'SIM:F5:DOC:'+code,'document_date':'2026-10-20','kind':kind,'memo':'SHADOW SIMULATION'}
def row(code,d,k,gross='2000.00',vat='UNRESOLVED',correction=None):
    x={'request_key':'SIM:F5:ROW:'+code,'document_id':d,'event_kind':k,'operation_date':'2026-10-20','gross_amount':gross,'vat_scheme':vat}
    if correction:x['correction_of_id']=correction
    return x

@pytest.mark.asyncio
async def test_access_and_default_unknown(client,admin_headers):
    assert (await client.get(R+'/overview')).status_code==403
    assert (await client.post(R+'/books',json=book('X','PLATFORM'))).status_code==403
    res=await client.get(R+'/overview',headers=admin_headers)
    assert res.status_code==200
    j=res.json();assert j['status']=='SHADOW_ONLY' and j['official_exports_enabled'] is False
    assert j['tax_obligations_posted'] is False and j['filing_enabled'] is False
    assert j['counts']['rows']==0

@pytest.mark.asyncio
async def test_smoke_probes_correct_http_methods_without_writes(client):
    # The authenticated book-creation route accepts POST only. A GET is 405,
    # not 403, and should never be used as the authentication smoke probe.
    assert (await client.get(R+'/books')).status_code==405
    assert (await client.get(R+'/overview')).status_code==403
    assert (await client.post(R+'/books',json={})).status_code==403
    response=await client.get(R+'/overview',headers={'X-Admin-Token':'test-token'})
    assert response.status_code==200
    assert response.json()['counts']['books']==0

@pytest.mark.asyncio
async def test_roles_and_agent_income(client,admin_headers):
    h=admin_headers
    seller=(await client.post(R+'/books',json=book('SELLER','SELLER'),headers=h)).json()
    platform=(await client.post(R+'/books',json=book('PLATFORM','PLATFORM'),headers=h)).json()
    assert seller['tax_profile_status']=='UNRESOLVED' and not seller['filing_ready']
    old=await client.post(R+'/books',json=book('SELLER','SELLER'),headers=h)
    assert old.status_code==201 and old.json()['id']==seller['id']
    bad=book('SELLER','PLATFORM');assert (await client.post(R+'/books',json=bad,headers=h)).status_code==409
    agent=(await client.post(R+'/documents',json=doc('REPORT','AGENT_REPORT'),headers=h)).json()['id']
    fee=await client.post(f"{R}/books/{platform['id']}/rows",json=row('FEE',agent,'AGENCY_PLATFORM_FEE','200.00','STANDARD'),headers=h)
    assert fee.status_code==201,fee.text
    assert fee.json()['vat_preview']=='36.07'
    sellerrow=await client.post(f"{R}/books/{seller['id']}/rows",json=row('GROSS',agent,'AGENCY_SELLER_SALE'),headers=h)
    assert sellerrow.status_code==201,sellerrow.text
    assert sellerrow.json()['gross_amount']=='2000.00' and sellerrow.json()['vat_preview'] is None
    cross=await client.post(f"{R}/books/{platform['id']}/rows",json=row('INCORRECT',agent,'AGENCY_SELLER_SALE'),headers=h)
    assert cross.status_code==422
    report=await client.get(f"{R}/books/{seller['id']}/export/AGENCY_REPORT_PREVIEW",headers=h)
    assert report.status_code==200
    assert report.headers['x-filing-ready']=='false' and report.content.startswith(b'\xef\xbb\xbf')
    assert b'2000.00' in report.content and b'SHADOW_NOT_FOR_FILING' in report.content
    assert report.headers['x-row-count']=='1'
    copy=await client.post(f"{R}/books/{seller['id']}/rows",json=row('GROSS',agent,'AGENCY_SELLER_SALE'),headers=h)
    assert copy.json()['id']==sellerrow.json()['id']
    fake=row('GROSS',agent,'AGENCY_SELLER_SALE','1900.00')
    assert (await client.post(f"{R}/books/{seller['id']}/rows",json=fake,headers=h)).status_code==409

@pytest.mark.asyncio
async def test_refunds_and_limits(client,admin_headers):
    h=admin_headers
    b=(await client.post(R+'/books',json=book('OWN','PLATFORM'),headers=h)).json()['id']
    sale=(await client.post(R+'/documents',json=doc('S1','SALE'),headers=h)).json()['id']
    refund=(await client.post(R+'/documents',json=doc('R1','REFUND'),headers=h)).json()['id']
    refund2=(await client.post(R+'/documents',json=doc('R2','REFUND'),headers=h)).json()['id']
    src=(await client.post(f'{R}/books/{b}/rows',json=row('SALE1',sale,'OWN_SALE','1220.00','STANDARD'),headers=h))
    assert src.status_code==201,src.text
    sid=src.json()['id'];assert src.json()['vat_preview']=='220.00'
    r1=await client.post(f'{R}/books/{b}/rows',json=row('REF1',refund,'REFUND','700.00','STANDARD',sid),headers=h)
    assert r1.status_code==201,r1.text
    too_much=await client.post(f'{R}/books/{b}/rows',json=row('REF2',refund2,'REFUND','600.00','STANDARD',sid),headers=h)
    assert too_much.status_code==409
    data=await client.get(f'{R}/books/{b}/export/EVENT_JOURNAL',headers=h)
    assert data.status_code==200 and b'-700.00' in data.content
    assert data.headers['x-row-count']=='2'
    assert (await client.get(f'{R}/books/{b}/export/SECRET',headers=h)).status_code==404

@pytest.mark.asyncio
async def test_no_real_documents_or_unknown_tax_rate(client,admin_headers):
    h=admin_headers
    assert (await client.post(R+'/documents',json={'document_ref':'REAL:UPD:1','document_date':'2026-10-20','kind':'SALE'},headers=h)).status_code==422
    assert (await client.post(R+'/books',json={'book_ref':'SIM:F5:BOOK:BADYEAR','actor_role':'PLATFORM','period_start':'2027-01-01','period_end':'2027-02-01'},headers=h)).status_code==422
    b=(await client.post(R+'/books',json=book('TEST','PLATFORM'),headers=h)).json()['id']
    d=(await client.post(R+'/documents',json=doc('TEST','SALE'),headers=h)).json()['id']
    wrong=row('R1',d,'OWN_SALE','1000','UNRESOLVED');wrong['tax_class']='ZERO'
    assert (await client.post(f'{R}/books/{b}/rows',json=wrong,headers=h)).status_code==422
    item=await client.post(f'{R}/books/{b}/rows',json=row('R2',d,'OWN_SALE','1000','UNRESOLVED'),headers=h)
    assert item.status_code==201 and item.json()['vat_preview'] is None


def test_vat_preview_math():
    base,vat=calculate_vat('OWN_SALE',Decimal('1220.00'),'STANDARD','STANDARD_GOODS',date(2026,10,20),'SIM:F5:DOC:1')
    assert base==Decimal('1000.00') and vat==Decimal('220.00')
    assert calculate_vat('OWN_SALE',Decimal('100.00'),'UNRESOLVED','STANDARD_GOODS',date(2026,10,20),'SIM:F5:DOC:1')==(None,None)
