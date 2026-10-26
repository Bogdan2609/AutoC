"""Operator v0.8: real DB contracts and tenant-sensitive stock operations."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.db import get_session
from app.main import app
from app.modules.marketplace.models import InventoryBalance

W = '/api/v1/backoffice/workspace'
M = '/api/v1/admin/marketplace'
B = '/api/v1/backoffice'


async def merchant(client, h, name='Магазин', slug='merchant-a'):
    result = await client.post(M+'/merchants', headers=h, json={'name':name,'slug':slug})
    assert result.status_code == 201, result.text
    return result.json()['id']


async def store(client, h, owner):
    result = await client.post(M+'/stores', headers=h, json={'merchant_id':owner,
        'name':'Магазин запчастей','slug':'parts-store'})
    assert result.status_code == 201, result.text
    return result.json()['id']


async def node(client, h, owner, name, kind, code, store_id=None):
    data={'merchant_id':owner,'code':code,'name':name,'kind':kind,
          'city':'Казань','address':'Казань, Ленина 1'}
    if store_id:data['store_id']=store_id
    result=await client.post(M+'/nodes', headers=h, json=data)
    assert result.status_code==201, result.text
    return result.json()['id']


@pytest.mark.asyncio
async def test_workspace_auth_and_initial_dashboard(client,admin_headers):
    assert (await client.get(W+'/summary')).status_code==403
    r=await client.get(W+'/summary',headers=admin_headers)
    assert r.status_code==200
    assert r.json()['products']==0
    assert r.json()['supplier_accounts']==0
    page=await client.get('/operator')
    assert page.status_code==200
    assert 'Центр управления' in page.text
    assert 'X-Admin-Token' in page.text
    assert 'no-store' in page.headers['cache-control']
    for name in ('merchants','stores','nodes','routes','accounts','destinations','offers','inventory','used','catalog','brands','suppliers'):
        response=await client.get(W+'/'+name,headers=admin_headers)
        assert response.status_code==200, (name,response.text)


@pytest.mark.asyncio
async def test_merchants_nodes_routes_accounts_are_readable(client,admin_headers):
    owner=await merchant(client,admin_headers)
    shop=await store(client,admin_headers,owner)
    origin=await node(client,admin_headers,owner,'Главный склад','warehouse','wh-kzn',shop)
    pickup=await node(client,admin_headers,owner,'ПВЗ центр','pickup','pvz-kzn',shop)
    r=await client.post(M+'/routes',headers=admin_headers,json={'source_node_id':origin,
        'target_node_id':pickup,'transit_days':1,'fee':'150.00'})
    assert r.status_code==201,r.text
    supplier=await client.post(B+'/suppliers',headers=admin_headers,json={'name':'ПартКом','code':'partkom'})
    assert supplier.status_code==201,supplier.text
    account=await client.post(M+'/supplier-accounts',headers=admin_headers,json={
        'supplier_id':supplier.json()['id'],'merchant_id':owner,'label':'partkom Казань',
        'credential_profile':'KAZAN','requests_per_hour':20,'timeout_seconds':20})
    assert account.status_code==201,account.text
    dest=await client.post(M+'/supplier-destinations',headers=admin_headers,json={
        'account_id':account.json()['id'],'node_id':origin,
        'external_address_id':'M-1','external_delivery_id':'D-2'})
    assert dest.status_code==201,dest.text
    for name,need in [('merchants','Магазин'),('stores','Магазин запчастей'),
                      ('nodes','ПВЗ центр'),('routes','ПВЗ центр'),
                      ('accounts','partkom Казань'),('destinations','M-1')]:
        res=await client.get(W+'/'+name,headers=admin_headers)
        assert res.status_code==200,res.text
        assert need in res.text,(name,res.text)
    r=await client.get(W+'/summary',headers=admin_headers)
    assert r.json()['routes']==1
    assert r.json()['nodes']==2


@pytest.mark.asyncio
async def test_catalog_offer_inventory_and_guards(client,admin_headers):
    brand=await client.post(B+'/brands',headers=admin_headers,json={'name':'BOSCH','slug':'bosch'})
    assert brand.status_code==201,brand.text
    product=await client.post(B+'/products',headers=admin_headers,json={
        'brand_id':brand.json()['id'],'name':'Диск тормозной','article':'0 986 479 088'})
    assert product.status_code==201,product.text
    pid=product.json()['id']
    result=await client.get(W+'/catalog?q=0986',headers=admin_headers)
    assert result.status_code==200
    assert result.json()['total']==0  # literal substring, not normalized search
    result=await client.get(W+'/catalog?q=BOSCH',headers=admin_headers)
    assert result.json()['total']==1
    assert result.json()['items'][0]['status']=='draft'
    assert (await client.get(W+'/catalog?status=invalid',headers=admin_headers)).status_code==422
    owner=await merchant(client,admin_headers)
    shop=await store(client,admin_headers,owner)
    warehouse=await node(client,admin_headers,owner,'Главный склад','warehouse','wh-kzn',shop)
    other=await merchant(client,admin_headers,'Чужой магазин','other-seller')
    foreign=await node(client,admin_headers,other,'Склад другого продавца','warehouse','wh-msk')
    unpublished=await client.post(W+'/offers',headers=admin_headers,json={
        'store_id':shop,'sku':'BOSCH-1','product_id':pid,'price':'2500.20'})
    assert unpublished.status_code==409
    assert (await client.post(B+f'/products/{pid}/publish',headers=admin_headers)).status_code==200
    created=await client.post(W+'/offers',headers=admin_headers,json={
        'store_id':shop,'sku':'BOSCH-1','product_id':pid,'price':'2500.20'})
    assert created.status_code==201,created.text
    oid=created.json()['id']
    assert (await client.post(W+'/offers',headers=admin_headers,json={
        'store_id':shop,'sku':'BOSCH-1','product_id':pid,'price':'2500.20'})).status_code==409
    assert (await client.put(W+f'/offers/{oid}/stock',headers=admin_headers,
        json={'node_id':foreign,'on_hand':12})).status_code==409
    stock=await client.put(W+f'/offers/{oid}/stock',headers=admin_headers,
        json={'node_id':warehouse,'on_hand':5})
    assert stock.status_code==200,stock.text
    assert stock.json()['available']==5
    update=await client.patch(W+f'/offers/{oid}',headers=admin_headers,
        json={'price':'2600.33','status':'active'})
    assert update.status_code==200,update.text
    assert update.json()['price']=='2600.33'
    inv=(await client.get(W+'/inventory',headers=admin_headers)).json()
    assert inv[0]['on_hand']==5
    assert inv[0]['available']==5
    assert (await client.get(W+'/offers',headers=admin_headers)).json()[0]['price']=='2600.33'


@pytest.mark.asyncio
async def test_reserved_stock_cannot_be_undercut(client,admin_headers):
    from app.modules.catalog.models import Brand,Product
    from app.modules.marketplace.models import Merchant, Store, FulfillmentNode, SellerOffer
    from app.modules.marketplace.models import InventoryBalance
    async for db in app.dependency_overrides[get_session]():
        b=Brand(name='A',name_norm='A',slug='a');db.add(b);await db.flush()
        p=Product(brand_id=b.id,article='A1',article_norm='A1',name='Test',slug='a-a1',status='published');db.add(p)
        m=Merchant(name='M',slug='m',status='active');db.add(m);await db.flush()
        s=Store(merchant_id=m.id,name='S',slug='s');db.add(s);await db.flush()
        n=FulfillmentNode(merchant_id=m.id,code='n',name='N',city='X',address='XX',kind='warehouse');db.add(n);await db.flush()
        o=SellerOffer(merchant_id=m.id,store_id=s.id,product_id=p.id,sku='X',price=Decimal('100'),status='active');db.add(o);await db.flush()
        inv=InventoryBalance(seller_offer_id=o.id,node_id=n.id,on_hand=4,reserved=3)
        db.add(inv);await db.commit()
        offer_id=str(o.id);node_id=str(n.id)
        break
    r=await client.put(W+f'/offers/{offer_id}/stock',headers=admin_headers,json={'node_id':node_id,'on_hand':2})
    assert r.status_code==409,r.text
    r=await client.put(W+f'/offers/{offer_id}/stock',headers=admin_headers,json={'node_id':node_id,'on_hand':4})
    assert r.status_code==200,r.text
    assert r.json()['available']==1

@pytest.mark.asyncio
async def test_used_item_created_as_own_separate_draft(client,admin_headers):
    owner=await merchant(client,admin_headers,'Б/у продавец','used-merchant')
    shop=await store(client,admin_headers,owner)
    created=await client.post(W+'/used',headers=admin_headers,json={
        'store_id':shop,'title':'Б/у генератор Audi','condition':'used_good',
        'mileage_km':86000,'defects':'Есть царапины'})
    assert created.status_code==201,created.text
    item=(await client.get(W+'/used',headers=admin_headers)).json()[0]
    assert item['title']=='Б/у генератор Audi'
    assert item['status']=='draft'
    assert item['merchant']=='Б/у продавец'
    published=await client.patch(M+f"/used-parts/{item['id']}",headers=admin_headers,
        json={'status':'published'})
    assert published.status_code==200,published.text
