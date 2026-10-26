"""Supplier checkout: never use customer contact or non-order identifiers."""
import uuid
from types import SimpleNamespace
import pytest
from sqlalchemy import inspect
from app.modules.marketplace.receiving_contact import (
    validate_receiving_contact, receiving_contact_for_node, ReceivingContactError,
)
from app.modules.marketplace.rossko_checkout_payload import build_rossko_checkout_payload
from app.modules.marketplace.models import FulfillmentNode
from app.modules.marketplace.procurement import SupplierPurchaseAttempt

ORDER = uuid.UUID('41e5d0a2-ce41-45ad-aa71-59b89b9a44c0')
LINE = uuid.UUID('f3f80c54-61db-46c9-a49f-14765e5656e3')


def payload(contact, order_id=ORDER, parts=None):
    return build_rossko_checkout_payload(
        keys=('SECRET1','SECRET2'),delivery_id='000000002',address_id='ADDRESS',
        payment_id='1',requisite_id='39619',receiving_contact=contact,
        parts=parts or [{'partnumber':'CF 705','brand':'Trialli','stock':'HST181','count':1,
                         'comment':str(LINE)}], order_uuid=order_id)


def test_contact_is_warehouse_recipient_not_buyer_even_with_extra_payload_comment():
    site=SimpleNamespace(active=True, receiving_contact_name='Складов Иван',
                         receiving_contact_phone='8 999 123 45 67',
                         receiving_contact_comment='Вход со двора')
    contact=receiving_contact_for_node(site)
    data=payload(contact)
    assert data['contact']=={'name':'Складов Иван','phone':'+79991234567',
                             'comment':'Вход со двора'}
    assert data['PARTS']['Part'][0]['comment']==str(ORDER)
    assert data['PARTS']['Part'][0]['comment']!=str(LINE)


def test_optional_contact_comment_empty_and_order_uuid_all_lines():
    contact=validate_receiving_contact('Иванов Иван','+79991234567')
    parts=[{'partnumber':f'A{i}','brand':'X','stock':'WH','count':1} for i in range(3)]
    out=payload(contact,parts=parts)
    assert out['contact']['comment']==''
    assert [p['comment'] for p in out['PARTS']['Part']]==[str(ORDER)]*3


def test_absent_or_invalid_uuid_never_generates_new_identity():
    receiver=validate_receiving_contact('Иванов Иван','+79991234567')
    for bad in ('', 'not-a-uuid',str(LINE)+' EXTRA',None):
        with pytest.raises(ValueError): payload(receiver,order_id=bad)


def test_missing_warehouse_contact_fails_closed():
    with pytest.raises(ReceivingContactError):
        receiving_contact_for_node(SimpleNamespace(active=True, receiving_contact_name=None,
                        receiving_contact_phone=None,receiving_contact_comment=''))
    with pytest.raises(ReceivingContactError):
        receiving_contact_for_node(SimpleNamespace(active=False, receiving_contact_name='Иванов Иван',
                        receiving_contact_phone='+79991234567',receiving_contact_comment=''))


def test_comment_can_be_cleared_or_updated_independently():
    assert validate_receiving_contact('Иванов Иван','+79991234567','').comment==''
    assert validate_receiving_contact('Иванов Иван','+79991234567','  Звонить охране  ').comment=='Звонить охране'
    with pytest.raises(ReceivingContactError):
        validate_receiving_contact('Иванов Иван','+79991234567','x'*501)


def test_models_pin_order_uuid_and_warehouse_comment():
    assert {'receiving_contact_name','receiving_contact_phone','receiving_contact_comment'} <= set(inspect(FulfillmentNode).columns.keys())
    assert {'buyer_order_id','contact_comment'} <= set(inspect(SupplierPurchaseAttempt).columns.keys())

@pytest.mark.asyncio
async def test_admin_node_contact_comment_can_be_cleared_without_editing_phone(client, admin_headers):
    from tests.test_marketplace_foundation_v07 import new_merchant
    merchant = await new_merchant(client, admin_headers, 'Receiving Merchant', 'receiving-merchant-test')
    api='/api/v1/admin/marketplace'
    resp=await client.post(api+'/nodes',headers=admin_headers,json={
        'merchant_id':merchant,'code':'recv-test1','name':'Receiving Warehouse',
        'kind':'warehouse','city':'Самара','address':'Улица Примерная 1',
        'receiving_contact_name':'Иванов Иван','receiving_contact_phone':'89991234567',
        'receiving_contact_comment':'Звонить охране'})
    assert resp.status_code==201,resp.text
    ident=resp.json()['id']
    details=await client.get(api+'/nodes/'+ident+'/receiving-contact',headers=admin_headers)
    assert details.status_code==200,details.text
    assert details.json()['receiving_contact_comment']=='Звонить охране'
    assert (await client.get(api+'/nodes/'+ident+'/receiving-contact')).status_code in (401,403)
    edited=await client.patch(api+'/nodes/'+ident,headers=admin_headers,json={
        'receiving_contact_comment':''})
    assert edited.status_code==200,edited.text
    result=(await client.get(api+'/nodes/'+ident+'/receiving-contact',headers=admin_headers)).json()
    assert result['receiving_contact_comment']==''
    assert result['receiving_contact_name']=='Иванов Иван'
    assert result['receiving_contact_phone']=='+79991234567'
    invalid=await client.patch(api+'/nodes/'+ident,headers=admin_headers,json={
        'receiving_contact_name':'Богдан','receiving_contact_phone':'+79991234567'})
    assert invalid.status_code==422


@pytest.mark.asyncio
async def test_real_rossko_checkout_payload_uses_pinned_order_and_warehouse_comment(monkeypatch):
    """Mock transport, no network. Test actual working checkout_rossko not only helper."""
    import asyncio, sys, types
    from app.modules.marketplace import procurement
    class Env:
        app_env = 'development'
    monkeypatch.setattr(procurement,'get_settings',lambda:Env())
    class FakeClient:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class FakeTransport:
        def __init__(self,**kwargs):pass
    captured=[]
    class FakeZeep:
        def __init__(self,*args,**kwargs):self.service=self
        async def GetCheckout(self, **kwargs):
            captured.append(kwargs)
            return {'CheckoutResult':{'success':True,'OrderIDS':{'id':123}}}
    import httpx
    monkeypatch.setattr(httpx,'AsyncClient',FakeClient)
    monkeypatch.setattr(httpx,'Client',FakeClient)
    monkeypatch.setitem(sys.modules,'zeep',types.SimpleNamespace(AsyncClient=FakeZeep))
    monkeypatch.setitem(sys.modules,'zeep.transports',types.SimpleNamespace(AsyncTransport=FakeTransport))
    monkeypatch.setitem(sys.modules,'zeep.helpers',types.SimpleNamespace(serialize_object=lambda result,**kwargs:result))
    result=await procurement.checkout_rossko(
        SimpleNamespace(rossko_key1='SECRET1',rossko_key2='SECRET2'),
        item=SimpleNamespace(raw_brand='Trialli',raw_article='CF 705'),
        detail=SimpleNamespace(external_token='SKU1',warehouse='HST181',min_qty=1,pack_multiple=1),
        dest=SimpleNamespace(external_delivery_id='000000002',external_address_id='ADDRESS'),
        request=SimpleNamespace(order_line_id=LINE,buyer_order_id=ORDER,client_ref='test-order-reference-345',
                                quantity=1,approved_unit_price=100,
                                contact_name='Складов Иван',contact_phone='+79991234567',
                                contact_comment='Позвонить за 10 минут', payment_id='1',requisite_id='39619'))
    assert result['CheckoutResult']['success'] is True
    assert len(captured)==1
    assert captured[0]['contact']=={'name':'Складов Иван','phone':'+79991234567',
                                     'comment':'Позвонить за 10 минут'}
    assert captured[0]['PARTS']['Part'][0]['comment']==str(ORDER)
    assert captured[0]['PARTS']['Part'][0]['comment']!=str(LINE)
