"""Pure regression tests: courier-only supplier purchasing, no supplier writes."""
import pytest
from app.modules.marketplace.supplier_delivery_policy import (
    require_rossko_courier, require_live_rossko_courier,
    CourierPolicyViolation, ROSSKO_COURIER_ID,
)


def test_only_ros_courier_with_address_is_permitted():
    require_rossko_courier(ROSSKO_COURIER_ID, 'vendor-address-1')
    for delivery_id in ('000000001', '', None, 'pickup'):
        with pytest.raises(CourierPolicyViolation):
            require_rossko_courier(delivery_id, 'vendor-address-1')
    with pytest.raises(CourierPolicyViolation, match='requires a configured address'):
        require_rossko_courier(ROSSKO_COURIER_ID, None)


def details(allowed=None):
    return {
        'deliveries': [{'id': ROSSKO_COURIER_ID}],
        'addresses': [{'id': 'dest-100', 'allowed_delivery_ids':
                       [ROSSKO_COURIER_ID] if allowed is None else allowed}],
        'payments': [{'id': '1'}],
    }


def test_ros_live_courier_validates_payment_and_address():
    require_live_rossko_courier(ROSSKO_COURIER_ID, 'dest-100', details(), payment_id='1')
    with pytest.raises(CourierPolicyViolation, match='address was not returned'):
        require_live_rossko_courier(ROSSKO_COURIER_ID, 'other', details())
    with pytest.raises(CourierPolicyViolation, match='not eligible'):
        require_live_rossko_courier(ROSSKO_COURIER_ID, 'dest-100', details(['000000001']))
    with pytest.raises(CourierPolicyViolation, match='payment method'):
        require_live_rossko_courier(ROSSKO_COURIER_ID, 'dest-100', details(), payment_id='2')
    with pytest.raises(CourierPolicyViolation, match='not currently offered'):
        d=details(); d['deliveries']=[]
        require_live_rossko_courier(ROSSKO_COURIER_ID, 'dest-100', d)


@pytest.mark.asyncio
async def test_real_procurement_permission_requires_approved_courier_destination(client, admin_headers):
    h=admin_headers
    from tests.test_operator_workspace_v080 import merchant, node
    mid=await merchant(client,h)
    nid=await node(client,h,mid,'Courier Warehouse','warehouse','courier-receiving')
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=h,
        json={'name':'ROSSKO','code':'rossko'})).json()['id']
    account=(await client.post('/api/v1/admin/marketplace/supplier-accounts',headers=h,
        json={'supplier_id':supplier,'merchant_id':mid,'label':'ROS Courier Policy',
              'credential_profile':'default'})).json()['id']
    base='/api/v1/admin/marketplace'
    dest=(await client.post(base+'/supplier-destinations',headers=h,json={
        'account_id':account,'node_id':nid,'external_delivery_id':'000000001',
        'external_address_id':'ADDR-1'})).json()['id']
    assert (await client.patch(base+'/supplier-destinations/'+dest,headers=h,
        json={'verified':True,'enabled':True})).status_code==200
    permission={'enabled':True,'confirmation':'I_ACCEPT_REAL_SUPPLIER_PURCHASES'}
    wrong=await client.patch(base+'/supplier-accounts/'+account+'/procurement-permission',
        headers=h,json=permission)
    assert wrong.status_code==409 and 'courier address' in wrong.text
    update=await client.patch(base+'/supplier-destinations/'+dest,headers=h,
        json={'verified':True,'enabled':True,'external_delivery_id':'000000002'})
    assert update.status_code==200 and update.json()['reverification_required'] is True
    approved=await client.patch(base+'/supplier-destinations/'+dest,headers=h,
        json={'verified':True,'enabled':True})
    assert approved.status_code==200
    ok=await client.patch(base+'/supplier-accounts/'+account+'/procurement-permission',
        headers=h,json=permission)
    assert ok.status_code==200 and ok.json()['procurement_enabled'] is True
