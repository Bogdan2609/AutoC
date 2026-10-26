"""Regression coverage for editable API accounts and delivery mappings."""
import uuid
from pathlib import Path
import pytest
from tests.test_operator_workspace_v080 import merchant, node

M='/api/v1/admin/marketplace'
B='/api/v1/backoffice'
W='/api/v1/backoffice/workspace'

@pytest.mark.asyncio
async def test_account_full_edit_and_profile_safety(client,admin_headers):
    h=admin_headers
    mid=await merchant(client,h)
    s=(await client.post(B+'/suppliers',headers=h,json={'name':'ROSSKO','code':'rossko'})).json()['id']
    r=await client.post(M+'/supplier-accounts',headers=h,json={'supplier_id':s,'merchant_id':mid,'label':'ROS', 'credential_profile':'default'})
    assert r.status_code==201,r.text
    account=r.json()['id']
    r=await client.patch(M+'/supplier-accounts/'+account,headers=h,json={'label':'ROS Kazan','credential_profile':'default','requests_per_hour':21,'timeout_seconds':15,'enabled':True,'read_enabled':True})
    assert r.status_code==200,r.text
    assert r.json()['label']=='ROS Kazan' and r.json()['requests_per_hour']==21 and r.json()['enabled']
    rows=(await client.get(W+'/accounts',headers=h)).json()
    assert rows[0]['label']=='ROS Kazan' and rows[0]['timeout_seconds']==15
    r=await client.patch(M+'/supplier-accounts/'+account,headers=h,json={'credential_profile':'TEST','enabled':True})
    assert r.status_code==422  # Both enable/read flags are mandatory for backwards compatibility
    r=await client.patch(M+'/supplier-accounts/'+account,headers=h,json={'credential_profile':'TEST','enabled':True,'read_enabled':True,'requests_per_hour':25})
    assert r.status_code==200,r.text
    assert r.json()['requires_reenable'] is True
    assert r.json()['enabled'] is False and r.json()['read_enabled'] is False
    assert (await client.patch(M+'/supplier-accounts/'+account,headers=h,json={'enabled':True,'read_enabled':True,'requests_per_hour':0})).status_code==422
    assert (await client.patch(M+'/supplier-accounts/'+account,json={'enabled':True,'read_enabled':True})).status_code==403

@pytest.mark.asyncio
async def test_destination_edit_resets_approval_and_checks_owner(client,admin_headers):
    h=admin_headers
    mid=await merchant(client,h)
    n1=await node(client,h,mid,'Central','warehouse','central')
    n2=await node(client,h,mid,'PVZ','pickup','pickup')
    outsider=await merchant(client,h,'Outside','outside')
    n3=await node(client,h,outsider,'Other','pickup','other')
    sid=(await client.post(B+'/suppliers',headers=h,json={'name':'ROSSKO','code':'rossko'})).json()['id']
    a=(await client.post(M+'/supplier-accounts',headers=h,json={'supplier_id':sid,'merchant_id':mid,'label':'ROSSKO Central','credential_profile':'default'})).json()['id']
    response=await client.post(M+'/supplier-destinations',headers=h,json={'account_id':a,'node_id':n1,'external_address_id':'ADDR-1','external_delivery_id':'DEL-1'})
    assert response.status_code==201,response.text
    dest=response.json()['id']
    r=await client.patch(M+'/supplier-destinations/'+dest,headers=h,json={'verified':True,'enabled':True})
    assert r.status_code==200,r.text
    r=await client.patch(M+'/supplier-destinations/'+dest,headers=h,json={'verified':True,'enabled':True,'external_address_id':'ADDR-2','external_delivery_id':'DEL-2'})
    assert r.status_code==200,r.text
    assert r.json()['reverification_required'] is True
    assert r.json()['verified'] is False and r.json()['enabled'] is False
    r=await client.patch(M+'/supplier-destinations/'+dest,headers=h,json={'verified':True,'enabled':True,'node_id':n3})
    assert r.status_code==409
    r=await client.patch(M+'/supplier-destinations/'+dest,headers=h,json={'verified':True,'enabled':True,'node_id':n2})
    assert r.status_code==200 and r.json()['enabled'] is False
    r=await client.patch(M+'/supplier-destinations/'+dest,headers=h,json={'verified':True,'enabled':True})
    assert r.status_code==200 and r.json()['enabled'] is True
    rows=(await client.get(W+'/destinations',headers=h)).json()
    assert rows[0]['external_address_id']=='ADDR-2' and rows[0]['node_id']==n2
    assert (await client.patch(M+'/supplier-destinations/'+dest,json={'verified':True,'enabled':True})).status_code==403


def test_ui_has_probe_map_and_edit_controls():
    js=Path('static/operator.js').read_text()
    assert "act('Привязать к складу','probe-map'" in js
    assert "if(name==='probe-map')" in js
    assert "act('Редактировать','edit-destination'" in js
