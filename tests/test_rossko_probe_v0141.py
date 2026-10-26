"""Read-only address discovery must work before delivery ID is known."""
import pytest
from app.core.config import get_settings
from app.modules.marketplace import account_probe


@pytest.mark.asyncio
async def test_details_requires_keys_but_not_delivery_id(client, admin_headers, monkeypatch):
    supplier=(await client.post('/api/v1/backoffice/suppliers', headers=admin_headers,
        json={'code':'rossko','name':'ROSSKO'})).json()['id']
    merchant=(await client.post('/api/v1/admin/marketplace/merchants', headers=admin_headers,
        json={'name':'Local merchant','slug':'local-merchant-v141'})).json()['id']
    account=(await client.post('/api/v1/admin/marketplace/supplier-accounts', headers=admin_headers,
        json={'supplier_id':supplier,'merchant_id':merchant,'label':'ROS Address Test',
              'credential_profile':'default'})).json()['id']
    r=await client.patch('/api/v1/admin/marketplace/supplier-accounts/'+account,
        headers=admin_headers,json={'enabled':True,'read_enabled':True})
    assert r.status_code==200,r.text
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    monkeypatch.setenv('ROSSKO_KEY1','test-key-1')
    monkeypatch.setenv('ROSSKO_KEY2','test-key-2')
    monkeypatch.setenv('ROSSKO_DELIVERY_ID','')
    get_settings.cache_clear()
    async def allowed(*args,**kwargs):return True
    async def details(cfg):
        assert cfg.rossko_key1 == 'test-key-1'
        assert cfg.rossko_key2 == 'test-key-2'
        assert cfg.rossko_delivery_id == ''
        return {'CheckoutDetailsResult':{'success':True,
                'DeliveryType':{'delivery':[{'id':'000000001','name':'Pickup'}]},
                'DeliveryAddress':{'address':[{'id':'123','city':'Samara',
                                                'street':'Lenina','house':'1'}]}}}
    monkeypatch.setattr(account_probe,'claim_supplier_budget',allowed)
    monkeypatch.setattr(account_probe,'live_rossko_details',details)
    r=await client.post(f'/api/v1/admin/marketplace/supplier-accounts/{account}/checkout-details',
                        headers=admin_headers)
    assert r.status_code==200,r.text
    assert r.json()['deliveries'][0]['id']=='000000001'
    assert r.json()['addresses'][0]['id']=='123'
    get_settings.cache_clear()
