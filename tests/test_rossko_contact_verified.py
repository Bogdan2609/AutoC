"""Offline-only contract tests; never contact ROSSKO."""
import asyncio
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree
from scripts import rossko_test_contact_verified as m

ACCOUNT = '511a4e77-55a7-48b8-874f-68420c7aa162'
DEST = 'c6afd071-6772-4963-b3d1-0770acdbd749'


def test_requires_surname_and_name_and_normalizes_phone():
    assert m.validate_recipient('Иванов Иван Иванович', '8 (999) 123-45-67') == ('Иванов Иван Иванович', '+79991234567')
    assert m.validate_recipient('Иванов Иван', '+79991234567') == ('Иванов Иван', '+79991234567')
    with pytest.raises(ValueError):
        m.validate_recipient('Богдан', '+79991234567')
    with pytest.raises(ValueError):
        m.validate_recipient('Иванов Иван', '+799')


def test_payload_uses_delivery_payment_contact_and_single_part():
    offer = SimpleNamespace(brand='Trialli',article='CF 705',warehouse='HST181')
    cfg = SimpleNamespace(rossko_key1='SECRET1', rossko_key2='SECRET2')
    p = m.build_payload(cfg,'000000002','address-123','39619',offer,'Иванов Иван','+79991234567',
                        order_uuid='a0af6379-d3be-4183-a3b9-62692c47cc2e')
    assert p['delivery']=={'delivery_id':'000000002','address_id':'address-123'}
    assert p['payment']=={'payment_id':'1','requisite_id':'39619'}
    assert p['contact']['name']=='Иванов Иван'
    assert p['contact']['phone']=='+79991234567'
    assert p['PARTS']['Part']==[{'partnumber':'CF 705','brand':'Trialli','stock':'HST181','count':1,'comment':'a0af6379-d3be-4183-a3b9-62692c47cc2e'}]


def xml_for(payload, omit=None):
    root=etree.Element('Envelope'); op=etree.SubElement(root,'GetCheckout')
    for group in ('delivery','payment','contact'):
        g=etree.SubElement(op,group)
        for key,val in payload[group].items():
            if (group,key)==omit: continue
            etree.SubElement(g,key).text=str(val)
    etree.SubElement(op,'delivery_parts').text='false'
    parts=etree.SubElement(op,'PARTS'); part=etree.SubElement(parts,'Part')
    for key,val in payload['PARTS']['Part'][0].items():
        etree.SubElement(part,key).text=str(val)
    return root


def test_xml_requires_exact_real_contact_values():
    p=m.build_payload(SimpleNamespace(rossko_key1='SECRET1',rossko_key2='SECRET2'),
                      '000000002','addr','39619',
                      SimpleNamespace(brand='Trialli',article='CF 705',warehouse='HST181'),
                      'Иванов Иван','+79991234567',order_uuid='a0af6379-d3be-4183-a3b9-62692c47cc2e')
    m.validate_exact_xml(xml_for(p),p)
    with pytest.raises(ValueError): m.validate_exact_xml(xml_for(p,('contact','phone')),p)
    bad=xml_for(p); bad.xpath("//*[local-name()='contact']/*[local-name()='name']")[0].text='Богдан'
    with pytest.raises(ValueError): m.validate_exact_xml(bad,p)


def test_requires_third_contact_rejection(monkeypatch,tmp_path):
    path=tmp_path/'third.json'
    monkeypatch.setattr(m,'final_journal_path',lambda *args:path)
    base={'account_id':ACCOUNT,'destination_id':DEST,'state':'supplier_rejected','order_ids':[],
          'redacted_supplier_messages':['Не указаны контактные данные']}
    path.write_text(json.dumps(base,ensure_ascii=False),encoding='utf-8')
    m.require_previous_contact_rejection(ACCOUNT,DEST)
    path.write_text(json.dumps({**base,'state':'dispatch_unknown'},ensure_ascii=False),encoding='utf-8')
    with pytest.raises(ValueError):m.require_previous_contact_rejection(ACCOUNT,DEST)


def test_writes_once_and_never_resubmits(monkeypatch,tmp_path):
    destination=tmp_path/'fourth.json'
    monkeypatch.setattr(m,'attempt_path',lambda *args:destination)
    monkeypatch.setattr(m,'require_previous_contact_rejection',lambda *args:None)
    monkeypatch.setattr(m,'get_settings',lambda:SimpleNamespace(app_env='development',supplier_api_enabled=True))
    offer=SimpleNamespace(brand='Trialli',article='CF 705',warehouse='HST181',price=492.27)
    cfg=SimpleNamespace(rossko_key1='SECRET1',rossko_key2='SECRET2')
    async def fake_live_quote(*args):return cfg,'000000002','addr','', '39619'
    async def actual_live_quote(*args):return cfg,'000000002','addr',offer,'39619'
    monkeypatch.setattr(m,'live_quote',actual_live_quote)
    async def fake_orders(endpoint,*args):
        assert endpoint=='GetOrders'
        return {'OrdersResult':{'success':True}}
    monkeypatch.setattr(m,'soap_request',fake_orders)
    monkeypatch.setattr(m,'parse_recent_orders',lambda *args:('ok',[]))
    class FakeTransport:
        def __init__(self,**kwargs):pass
    class FakeContext:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
    import httpx
    monkeypatch.setattr(httpx,'AsyncClient',FakeContext)
    monkeypatch.setattr(httpx,'Client',FakeContext)
    sent=[]
    class FakeService:
        async def GetCheckout(self, **kw):
            assert destination.is_file() # durable fence exists BEFORE request
            sent.append(kw)
            return {'CheckoutResult':{'success':False,'message':'Тестовый отказ','OrderIDS':{}}}
    class FakeZeep:
        def __init__(self,*args,**kwargs):self.service=FakeService()
        def create_message(self,service,method,**payload):
            assert method=='GetCheckout'
            return xml_for(payload)
    monkeypatch.setitem(sys.modules,'zeep',types.SimpleNamespace(AsyncClient=FakeZeep))
    monkeypatch.setitem(sys.modules,'zeep.transports',types.SimpleNamespace(AsyncTransport=FakeTransport))
    monkeypatch.setitem(sys.modules,'zeep.helpers',types.SimpleNamespace(serialize_object=lambda x,**kw:x))
    monkeypatch.setattr(m,'normalize_checkout',lambda response:('supplier_rejected',[],'supplier_no_confirmed_order_ids'))
    monkeypatch.setattr(m,'checkout_messages',lambda response:['Тестовый отказ'])
    from app.modules.marketplace.receiving_contact import ReceivingContact
    async def configured_receiver(*args): return ReceivingContact('Иванов Иван Иванович', '+79991234567')
    monkeypatch.setattr(m, 'receiving_contact_for_destination_ids', configured_receiver)
    args=SimpleNamespace(account_id=ACCOUNT,destination_id=DEST,stock='HST181',payment_id='1',
        confirm=m.CONFIRM,portal_test_enabled=True,accept_duplicate_risk=True,acknowledge_unknown_courier_terms=True)
    assert asyncio.run(m.submit(args))==3
    assert destination.is_file()
    assert json.loads(destination.read_text())['state']=='supplier_rejected'
    assert len(sent)==1
    assert sent[0]['contact']['name']=='Иванов Иван Иванович'
    assert sent[0]['contact']['comment']==''
    assert sent[0]['PARTS']['Part'][0]['comment'] == json.loads(destination.read_text())['internal_test_order_uuid']
    assert asyncio.run(m.submit(args))==2
    assert len(sent)==1


def test_stock_gate_prevents_old_warehouse_without_creating_journal(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import asyncio
    marker = tmp_path / 'fourth.json'
    monkeypatch.setattr(m, 'attempt_path', lambda *args: marker)
    monkeypatch.setattr(m, 'get_settings', lambda: SimpleNamespace(app_env='development',supplier_api_enabled=True))
    args = SimpleNamespace(account_id=ACCOUNT,destination_id=DEST,stock='HST282827366',payment_id='1',
      confirm=m.CONFIRM,portal_test_enabled=True,accept_duplicate_risk=True,acknowledge_unknown_courier_terms=True)
    assert asyncio.run(m.submit(args)) == 2
    assert not marker.exists()
    assert m.AUTHORIZED_STOCK == 'HST181'
