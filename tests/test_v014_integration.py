"""New v0.14 functionality: bounded routes, purchase idempotency, zero accidental spend."""
from decimal import Decimal
from uuid import UUID

import pytest
from app.core.config import get_settings
from app.core.db import get_session
from app.main import app
from app.modules.marketplace import procurement as purchase
from app.modules.offer_engine.providers import Offer, PROVIDERS
from app.modules.suppliers.models import SupplierItem, OfferSnapshot
from app.modules.offer_engine.models import OfferDetail, CacheState
from app.modules.marketplace.account_search import account_key
from app.modules.accounts.models import utc
from app.modules.commerce.models import Order, OrderLine
from app.modules.commerce.sale_lifecycle import record_paid_fulfillment
from app.modules.payments.models import Payment
from tests.test_catalog_integration import create_product
from tests.test_marketplace_foundation_v07 import new_merchant, new_store, new_user

M='/api/v1/admin/marketplace'
P='/api/v1/admin/supplier-purchases'


async def new_node(client,h,merchant,code,kind,city):
    r=await client.post(M+'/nodes',headers=h,json={
        'merchant_id':merchant,'code':code,'name':code.upper(),
        'kind':kind,'city':city,'address':city+', Test address 1',
        'receiving_contact_name':'Складов Иван', 'receiving_contact_phone':'+79990000000'})
    assert r.status_code==201,r.text
    return r.json()['id']


@pytest.mark.asyncio
async def test_two_hop_routes_and_tenant_restrictions(client,admin_headers):
    _,product=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)
    merchant=await new_merchant(client,admin_headers,'Our Store','our-v14')
    store=await new_store(client,admin_headers,merchant,'central-v14')
    src=await new_node(client,admin_headers,merchant,'samara-v14','warehouse','Самара')
    hub=await new_node(client,admin_headers,merchant,'kazan-v14','warehouse','Казань')
    dst=await new_node(client,admin_headers,merchant,'kzn-pickup-v14','pickup','Казань')
    outsider=await new_merchant(client,admin_headers,'Outsider','outside-v14')
    alien=await new_node(client,admin_headers,outsider,'alien-v14','pickup','Москва')
    for a,b,days,fee in [(src,hub,2,'90.00'),(hub,dst,1,'45.00')]:
        r=await client.post(M+'/routes',headers=admin_headers,json={
            'source_node_id':a,'target_node_id':b,'transit_days':days,'fee':fee})
        assert r.status_code==201,r.text
    forbidden=await client.post(M+'/routes',headers=admin_headers,json={
        'source_node_id':src,'target_node_id':alien,'transit_days':1})
    assert forbidden.status_code==409
    uid,auth=await new_user(client,'v14seller@example.org')
    await client.post(M+'/memberships',headers=admin_headers,json={
        'store_id':store,'user_id':uid,'role':'owner'})
    created=await client.post('/api/v1/seller/offers',headers=auth,json={
        'store_id':store,'product_id':product['id'],'sku':'v14-stock-1',
        'source_kind':'own_stock','price':'3000.00'})
    assert created.status_code==201,created.text
    offer=created.json()['id']
    await client.patch('/api/v1/seller/offers/'+offer,headers=auth,json={'status':'active'})
    await client.put('/api/v1/seller/offers/'+offer+'/stock',headers=auth,
                     json={'node_id':src,'on_hand':2})
    fetched=await client.get('/api/v1/marketplace/products/'+product['id']+'/offers?pickup_node_id='+dst)
    assert fetched.status_code==200,fetched.text
    options=fetched.json()['offers'][0]['fulfillment']
    assert options[0]['days']==3
    assert options[0]['fee']=='135.00'
    assert options[0]['route_node_ids']==[src,hub,dst]


@pytest.mark.asyncio
async def test_purchase_endpoints_fail_closed_and_permissions(client,admin_headers):
    assert (await client.get(P)).status_code==403
    assert (await client.get(P,headers=admin_headers)).json()==[]
    random='00000000-0000-4000-8000-000000000001'
    response=await client.post(P+'/prepare',headers=admin_headers,json={
        'snapshot_id':random,'destination_id':random,'quantity':1,
        'approved_unit_price':'12.00','client_ref':'abc_unique_ref_001',
        'contact_name':'Buyer Test','contact_phone':'1234567890','payment_id':'1'})
    assert response.status_code==403
    # No job or supplier order can be sent as a consequence of mere deployment.
    assert (await client.get(P,headers=admin_headers)).json()==[]


@pytest.mark.asyncio
async def test_rossko_explicit_checkout_is_idempotent(client,admin_headers,monkeypatch):
    brand,product=await create_product(client,admin_headers)
    merchant=await new_merchant(client,admin_headers,'Primary Merchant','primary-v14')
    node=await new_node(client,admin_headers,merchant,'purchase-v14','warehouse','Самара')
    supp=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
        json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account=(await client.post(M+'/supplier-accounts',headers=admin_headers,json={
        'supplier_id':supp,'merchant_id':merchant,'label':'ROS Samara','credential_profile':'V14'})).json()['id']
    dest=(await client.post(M+'/supplier-destinations',headers=admin_headers,json={
        'account_id':account,'node_id':node,'external_address_id':'12345',
        'external_delivery_id':'000000002'})).json()['id']
    await client.patch(M+'/supplier-accounts/'+account,headers=admin_headers,json={
        'enabled':True,'read_enabled':True})
    await client.patch(M+'/supplier-destinations/'+dest,headers=admin_headers,json={
        'enabled':True,'verified':True})
    monkeypatch.setenv('SUPPLIER_PROFILE_V14_ROSSKO_KEY1','mock-key-1')
    monkeypatch.setenv('SUPPLIER_PROFILE_V14_ROSSKO_KEY2','mock-key-2')
    monkeypatch.setenv('SUPPLIER_PROFILE_V14_ROSSKO_DELIVERY_ID','000000002')
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    monkeypatch.setenv('SUPPLIER_PROCUREMENT_ENABLED','true')
    monkeypatch.setenv('REAL_SUPPLIER_CHECKOUT_ENABLED','true')
    get_settings.cache_clear()
    response=await client.patch(M+'/supplier-accounts/'+account+'/procurement-permission',
        headers=admin_headers,json={'enabled':True,'confirmation':'not-approved'})
    assert response.status_code==409
    response=await client.patch(M+'/supplier-accounts/'+account+'/procurement-permission',
        headers=admin_headers,json={'enabled':True,'confirmation':'I_ACCEPT_REAL_SUPPLIER_PURCHASES'})
    assert response.status_code==200,response.text
    async for db in app.dependency_overrides[get_session]():
        item=SupplierItem(supplier_id=UUID(supp),product_id=UUID(product['id']),
            external_key='GUID:WH',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed')
        db.add(item);await db.flush()
        snap=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
            supplier_destination_id=UUID(dest),purchase_price=Decimal('1800.00'),
            quantity=3,lead_time_days=2)
        db.add(snap);await db.flush()
        db.add(OfferDetail(snapshot_id=snap.id,external_token='GUID:WH',warehouse='WH',
                           min_qty=1,pack_multiple=1,verified_live=True,metadata_public={'_search_job_id':'purchase-current'}))
        db.add(CacheState(supplier_code='rossko',lookup_key=account_key('BOSCH','0986479088',UUID(account),UUID(dest)),product_id=UUID(product['id']),status='ready',last_completed_job_id='purchase-current',result_count=1,expires_at=utc()))
        await db.commit()
        snap_id=str(snap.id)
        old=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
            supplier_destination_id=UUID(dest),purchase_price=Decimal('1800.00'),
            quantity=3,lead_time_days=2)
        db.add(old);await db.flush()
        db.add(OfferDetail(snapshot_id=old.id,external_token='GUID:WH',warehouse='WH',
            min_qty=1,pack_multiple=1,verified_live=True,
            metadata_public={'_search_job_id':'outdated-search'}))
        await db.commit()
        old_snap_id=str(old.id)
        # v0.32: supplier-side checkout may only represent a verified paid
        # customer order, never a standalone purchasing operation.
        buyer_id,_=await new_user(client,'v14-purchase-customer@example.org')
        order=Order(user_id=UUID(buyer_id),idempotency_key='v14-funded-order',
                    total=Decimal('2200.00'),status='paid_procurement_pending',
                    total_is_final=True,request_details={'workflow':'sbp_checkout'},
                    contact='Test Customer',phone='79001234567',shipping_address='Pickup')
        db.add(order);await db.flush()
        line=OrderLine(order_id=order.id,title='Test part',supplier_code='rossko',
                       quantity=1,snapshot_id=UUID(snap_id),supplier_item_id=item.id,
                       merchant_id=UUID(merchant),fulfillment_node_id=UUID(node),
                       unit_retail=Decimal('2200.00'),unit_purchase=Decimal('1800.00'))
        db.add(line);await db.flush()
        db.add(Payment(order_id=order.id,provider_id='v14-verified-provider-123',
                       amount=Decimal('2200.00'),status='succeeded'))
        await record_paid_fulfillment(db,order,[line])
        await db.commit()
        line_id=str(line.id)
        paid_order_id=str(order.id)
    payload={'order_line_id':line_id,'snapshot_id':snap_id,'destination_id':dest,'quantity':1,
        'approved_unit_price':'1800.00','client_ref':'order_v14_unique_0001',
        'contact_name':'Test Customer','contact_phone':'79001234567','payment_id':'1','requisite_id':'39619'}
    # A recent historical snapshot must fail even while the underlying SKU is in stock.
    stale_payload={**payload,'snapshot_id':old_snap_id,'client_ref':'order_v14_stale_ref_0002'}
    stale=await client.post(P+'/prepare',headers=admin_headers,json=stale_payload)
    assert stale.status_code==409,stale.text
    prepared=await client.post(P+'/prepare',headers=admin_headers,json=payload)
    assert prepared.status_code==201,prepared.text
    ident=prepared.json()['id']
    async for db in app.dependency_overrides[get_session]():
        stored=await db.get(purchase.SupplierPurchaseAttempt,UUID(ident))
        assert stored.contact_name=='Складов Иван'
        assert stored.contact_phone=='+79990000000'
        assert stored.contact_name!=payload['contact_name']
        break
    repeated=await client.post(P+'/prepare',headers=admin_headers,json=payload)
    assert repeated.json()['reused'] is True
    count={'orders':0}
    async def search(brand,article,config):
        return [Offer('rossko','GUID:WH','BOSCH','0986479088',Decimal('1800.00'),
            4,2,'GUID:WH','WH',1,1)]
    async def order(config,*,item,detail,dest,request):
        count['orders']+=1
        return {'success':True,'OrderIDS':{'id':'9876543'}}
    async def rate(*args,**kwargs):return True
    monkeypatch.setattr(PROVIDERS['rossko'],'search',search)
    monkeypatch.setattr(purchase,'checkout_rossko',order)
    from app.modules.marketplace import account_probe
    async def details(config):
        return {'CheckoutDetailsResult': {'success': True,
            'DeliveryType': {'delivery': [{'id': '000000002', 'name': 'Courier'}]},
            'PaymentType': {'payment': [{'id': '1', 'name': 'Bank transfer'}]},
            'DeliveryAddress': {'address': [{'id': '12345',
                'Delivery': {'ids': {'id': ['000000002']}}}]},
            'CompanyList': {'company': [{'id': 39619}]}}}
    monkeypatch.setattr(account_probe, 'live_rossko_details', details)
    import app.modules.offer_engine.discovery as discovery
    monkeypatch.setattr(discovery,'claim_supplier_budget',rate)
    ack={'client_ref':payload['client_ref'],'confirmation':'PLACE_REAL_SUPPLIER_ORDER'}
    confirmation=await client.post(P+'/'+ident+'/confirm',headers=admin_headers,json=ack)
    assert confirmation.status_code==200,confirmation.text
    assert confirmation.json()['status']=='accepted_unverified'
    assert confirmation.json()['remote_order_ids']==['9876543']
    confirmation=await client.post(P+'/'+ident+'/confirm',headers=admin_headers,json=ack)
    assert confirmation.json()['sent_again'] is False
    assert count['orders']==1
    # A SOAP ACK is not verified fulfillment. Read-only GetOrders must prove
    # the exact paid SKU, quantity and price before supplier_confirmed.
    from app.modules.marketplace import purchase_operations
    async def readback(*args, **kwargs):
        return {'OrdersResult': {'success': True, 'OrdersList': {'Order': [
            {'id': '9876543', 'parts': {'part': [{
                'brand': 'BOSCH', 'partnumber': '0986479088', 'count': 1,
                'price': '1800.00', 'currency': 'RUB', 'comment': paid_order_id,
            }]}}
        ]}}}
    monkeypatch.setattr(purchase_operations, 'read_rossko_orders', readback)
    result=await client.post(P+'/'+ident+'/reconcile',headers=admin_headers)
    assert result.status_code==200,result.text
    assert result.json()['status']=='verified',result.json()
    reused=await client.post(P+'/'+ident+'/reconcile',headers=admin_headers)
    assert reused.json()['reused'] is True
    first=await client.post(P+'/'+ident+'/request-cancellation',headers=admin_headers)
    assert first.status_code==200,first.text
    assert first.json()['supplier_cancelled'] is False
    second=await client.post(P+'/'+ident+'/request-cancellation',headers=admin_headers)
    assert second.status_code==200 and second.json()['reused'] is True
    assert count['orders']==1
    get_settings.cache_clear()


def test_rossko_partial_or_unknown_response_requires_reconciliation():
    assert purchase.normalize_checkout_result({'success':True,'OrderIDS':{'id':55}})==('accepted',['55'],None)
    assert purchase.normalize_checkout_result({'success':True,'OrderIDS':{'id':55},
        'ItemsErrorList':{'ItemError':[{'partnumber':'x'}]}})[0]=='manual_review'
    assert purchase.normalize_checkout_result({})[0]=='manual_review'


@pytest.mark.asyncio
async def test_supplier_seller_offer_live_cart_no_purchase(client,admin_headers,monkeypatch):
    """Cart makes only read-only supplier search and cannot place a supplier order."""
    _,product=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)
    owner=await new_merchant(client,admin_headers,'Stock Sourcing','supplier-store-v14')
    store=await new_store(client,admin_headers,owner,'store-v14')
    node=await new_node(client,admin_headers,owner,'supply-node-v14','warehouse_pickup','Самара')
    uid,token=await new_user(client,'suppliercartv14@example.org')
    await client.post(M+'/memberships',headers=admin_headers,json={'store_id':store,'user_id':uid,'role':'owner'})
    supp=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
        json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account=(await client.post(M+'/supplier-accounts',headers=admin_headers,json={
        'supplier_id':supp,'merchant_id':owner,'label':'ROSSKO from Samara','credential_profile':'SUPPCART'})).json()['id']
    dest=(await client.post(M+'/supplier-destinations',headers=admin_headers,json={
        'account_id':account,'node_id':node,'external_delivery_id':'000000002'})).json()['id']
    await client.patch(M+'/supplier-accounts/'+account,headers=admin_headers,json={'enabled':True,'read_enabled':True})
    await client.patch(M+'/supplier-destinations/'+dest,headers=admin_headers,json={'verified':True,'enabled':True})
    monkeypatch.setenv('SUPPLIER_PROFILE_SUPPCART_ROSSKO_KEY1','mock-key-1')
    monkeypatch.setenv('SUPPLIER_PROFILE_SUPPCART_ROSSKO_KEY2','mock-key-2')
    monkeypatch.setenv('SUPPLIER_PROFILE_SUPPCART_ROSSKO_DELIVERY_ID','000000002')
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    get_settings.cache_clear()
    async for db in app.dependency_overrides[get_session]():
        item=SupplierItem(supplier_id=UUID(supp),product_id=UUID(product['id']),
            external_key='GUID:WH',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed')
        db.add(item);await db.flush()
        snap=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
            supplier_destination_id=UUID(dest),purchase_price=Decimal('1800.00'),quantity=5,lead_time_days=1)
        db.add(snap);await db.flush()
        db.add(OfferDetail(snapshot_id=snap.id,external_token='GUID:WH',warehouse='WH',
                           min_qty=1,pack_multiple=1,verified_live=True,metadata_public={'_search_job_id':'v14-cart-current'}))
        db.add(CacheState(supplier_code='rossko',lookup_key=account_key('BOSCH','0986479088',UUID(account),UUID(dest)),product_id=UUID(product['id']),status='ready',last_completed_job_id='v14-cart-current',result_count=1,expires_at=utc()))
        await db.commit()
        item_id=str(item.id)
    seller_response=await client.post('/api/v1/seller/offers',headers=token,json={
        'store_id':store,'sku':'BOSCH-API-1','product_id':product['id'],
        'source_kind':'supplier','supplier_account_id':account,'supplier_item_id':item_id,'price':'2400.00'})
    assert seller_response.status_code==201,seller_response.text
    offer=seller_response.json()['id']
    await client.patch('/api/v1/seller/offers/'+offer,headers=token,json={'status':'active'})
    listing=await client.get('/api/v1/marketplace/products/'+product['id']+'/offers?pickup_node_id='+node)
    assert listing.status_code==200,listing.text
    rows=[r for r in listing.json()['offers'] if r.get('offer_id')==offer]
    assert len(rows)==1,listing.text
    assert rows[0]['requires_live_validation'] is True
    count={'get':0,'post':0}
    async def fake_search(brand,article,config):
        count['get']+=1
        return [Offer('rossko','GUID:WH','BOSCH','0986479088',Decimal('1800.00'),
                      5,1,'GUID:WH','WH',1,1)]
    async def fake_checkout(**kwargs):
        count['post']+=1
        return {'success':True,'OrderIDS':{'id':1234}}
    async def permit(*args):return True
    monkeypatch.setattr(PROVIDERS['rossko'],'search',fake_search)
    monkeypatch.setattr(purchase,'checkout_rossko',fake_checkout)
    import app.modules.offer_engine.discovery as discovery
    monkeypatch.setattr(discovery,'claim_supplier_budget',permit)
    added=await client.post('/api/v1/marketplace/cart/items',headers=token,
        json={'seller_offer_id':offer,'pickup_node_id':node,'quantity':2})
    assert added.status_code==201,added.text
    assert added.json()['supplier_checked_live'] is True
    assert count=={'get':1,'post':0}
    preview=await client.get('/api/v1/marketplace/cart/preview',headers=token)
    assert preview.status_code==200,preview.text
    assert preview.json()['items'][0]['in_stock'] is True
    assert preview.json()['payment_enabled'] is False
    get_settings.cache_clear()


def test_rossko_checkout_details_normalization():
    from app.modules.marketplace.account_probe import normalize_checkout_details
    result=normalize_checkout_details({'success':True,
        'DeliveryType':{'delivery':[{'id':'0001','name':'Самовывоз'}]},
        'PaymentType':{'payment':{'id':1,'name':'Банковский платёж'}},
        'DeliveryAddress':{'address':{'id':'123','city':'Самара',
            'street':'Ленина','house':'1','Delivery':{'ids':{'id':['0001','0002']}}}},
        'CompanyList':{'company':{'requisite':'secret tax id'}}})
    assert result['addresses'][0]['allowed_delivery_ids']==['0001','0002']
    assert result['deliveries'][0]['id']=='0001'
    assert 'secret tax id' not in str(result)


@pytest.mark.asyncio
async def test_rossko_probe_requires_explicit_read_permission(client,admin_headers):
    unknown='00000000-0000-4000-8000-000000000001'
    assert (await client.post(M+'/supplier-accounts/'+unknown+'/checkout-details')).status_code==403
    assert (await client.post(M+'/supplier-accounts/'+unknown+'/checkout-details',
                              headers=admin_headers)).status_code==403
