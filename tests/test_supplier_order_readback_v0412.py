"""v0.41.2 contract tests: offline only, never import actual credentials."""
from decimal import Decimal
from types import SimpleNamespace
import pytest
from app.integrations.suppliers.order_readback import (
    ReadbackError, normalize_order, validate_reference,
)
from app.integrations.suppliers.order_transport import read_external_order


def test_reference_fails_closed():
    for code, ref in [('rossko','0'),('adeo','1 OR 1=1'),('forum-auto','abc'),
                      ('partkom','x/../admin'),('partkom',''),('rossko','-4')]:
        with pytest.raises(ReadbackError, match='invalid_external_reference'):
            validate_reference(code,ref)
    assert validate_reference('partkom','order_ABC-123')=='order_ABC-123'
    with pytest.raises(ReadbackError):validate_reference('other','1')


def test_rossko_exact_id_and_no_pii():
    sample={'OrdersResult':{'success':True,'OrdersList':{'Order':[{
        'id':123,'detail':{'delivery_address':'BUYER HOME 55', 'company_requisites':'PRIVATE INN'},
        'parts':{'part':{'brand':'TRIALLI','partnumber':'CF705','count':1,'price':'492.27',
                          'comment':'CUSTOMER PRIVATE PHONE','status':1}}}]}}}
    output=normalize_order('rossko','123',sample).public()
    assert output['observation']=='found'
    assert output['lines'][0]['unit_price_rub']=='492.27'
    assert output['supplier_confirmed'] is False
    for private in ('PRIVATE','BUYER HOME','CUSTOMER','INN'):
        assert private not in str(output)
    assert normalize_order('rossko','124',sample).public()['not_found_is_inconclusive']


def test_rossko_failure_and_partial():
    with pytest.raises(ReadbackError):normalize_order('rossko','123',{'OrdersResult':{'success':False}})
    sample={'success':True,'OrdersList':{'Order':{'id':123,'parts':{'part':{'status':7,'count':2,'brand':'T','partnumber':'A'}}}}}
    assert normalize_order('rossko','123',sample).observation=='partial'


def test_adeo_xml_status_no_implicit_confirmation():
    raw=b'<result><detail><id>246899</id><article>CF705</article><producer>TRIALLI</producer><count>2</count><count_orkaz>1</count_orkaz></detail></result>'
    output=normalize_order('adeo','246899',raw).public()
    assert output['observation']=='partial'
    assert output['lines'][0]['state']=='refused'
    assert not output['supplier_confirmed']
    malformed=b'<!DOCTYPE foo [<!ENTITY XXE SYSTEM "file:///etc/passwd">]><result>&XXE;</result>'
    with pytest.raises(Exception):normalize_order('adeo','246899',malformed)
    # Ambiguous flattened rows are not equivalent to a missing order.
    flat=b'<result><detail><id>42</id><id>43</id><article>X</article></detail></result>'
    assert normalize_order('adeo','42',flat).observation=='manual_review'


def test_partkom_documented_reference_path():
    raw=[{'orderNumber':'567','detailNum':'CF705','maker':'TRIALLI','quantity':2,
          'refusalCount':1,'releaseCount':0,'price':Decimal('493.50'),
          'comment':'DO NOT EXPOSE THIS'}]
    report=normalize_order('partkom','customer_ref_99',raw).public()
    assert report['observation']=='partial'
    assert report['lines'][0]['observed']==1
    assert 'DO NOT EXPOSE' not in str(report)
    assert normalize_order('partkom','customer_ref_99',[]).observation=='not_found_in_response'
    with pytest.raises(ReadbackError):normalize_order('partkom','customer_ref_99',{'errorCode':5})


def test_forum_status_90_not_delivered_and_no_early_false_partial():
    raw=[{'did':11,'statusId':90,'arTovs':[{'nr':'CF705','brand':'TRIALLI',
          'k1':1,'k2':1,'k3':0,'price':Decimal('550.00')}]}]
    result=normalize_order('forum-auto','11',raw).public()
    assert result['observation']=='found'
    assert result['lines'][0]['state']=='pickup_ready_not_delivered'
    assert result['lines'][0]['observed'] is None
    assert result['buyer_delivery_confirmed'] is False
    canceled=[{'did':11,'statusId':0,'arTovs':[{'k1':1,'k3':0}]}]
    assert normalize_order('forum-auto','11',canceled).observation=='canceled'
    assert normalize_order('forum-auto','11',[{'did':11,'statusId':0}]).observation=='canceled'
    assert normalize_order('forum-auto','11',[{'did':11,'statusId':30}]).observation=='manual_review'
    assert normalize_order('forum-auto','12',raw).observation=='not_found_in_response'


def test_forum_unknown_status_requires_manual_review():
    raw=[{'did':11,'statusId':999,'arTovs':[{'k1':1}]}]
    assert normalize_order('forum-auto','11',raw).observation=='manual_review'
    with pytest.raises(ReadbackError):normalize_order('forum-auto','11',[{'did':11},{'did':11}])
    with pytest.raises(ReadbackError):normalize_order('forum-auto','11',{'errorCode':5})


@pytest.mark.asyncio
async def test_read_transports_never_use_supplier_write_methods(monkeypatch):
    from app.integrations.suppliers import order_transport as t
    class FakeResponse:
        status_code=200
        headers={}
        async def aiter_bytes(self):
            yield b'[]'
    class Stream:
        async def __aenter__(self):return FakeResponse()
        async def __aexit__(self,*args):return None
    class Fake:
        def __init__(self,*args,**kwargs):
            assert kwargs.get('follow_redirects') is False
            assert kwargs.get('trust_env') is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return None
        def stream(self,method,url,**kwargs):
            assert method=='GET'
            assert 'basket/order' not in url and 'addGoodsToOrder' not in url
            assert url.startswith('https://')
            return Stream()
    monkeypatch.setattr(t.httpx,'AsyncClient',Fake)
    config=SimpleNamespace(partkom_login='S',partkom_password='S',forum_login='S',forum_password='S')
    assert await read_external_order('partkom',config,'ref_01')==[]
    assert await read_external_order('forum-auto',config,'42')==[]


@pytest.mark.asyncio
async def test_bounded_response_does_not_consume_oversized_stream():
    from app.integrations.suppliers.order_transport import _read_bounded
    class FakeResponse:
        status_code=200
        headers={}
        async def aiter_bytes(self):
            yield b'x'*700000
            yield b'x'*700000
            raise AssertionError('Should stop before reading a third chunk')
    class Stream:
        async def __aenter__(self):return FakeResponse()
        async def __aexit__(self,*args):return None
    class Fake:
        def stream(self,*args,**kwargs):return Stream()
    with pytest.raises(ReadbackError, match='supplier_response_too_large'):
        await _read_bounded(Fake(),'GET','https://example.invalid')


def test_matrix_declares_readback_without_purchasing():
    from app.integrations.suppliers.diagnostics import matrix
    report=matrix()
    assert report['version']=='0.41.2'
    for item in report['suppliers']:
        assert item['manual_readback_endpoint_available']
        assert item['documented_order_readback']
        assert item['gateway_dispatch'] is False
    assert report['outbound_supplier_writes_allowed'] is False
