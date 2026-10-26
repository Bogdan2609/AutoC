"""Marketplace endpoints: real DB constraints and permissions, zero external HTTP."""
import os
from decimal import Decimal
import pytest
from app.modules.marketplace.account_credentials import ready
from app.modules.marketplace.models import SupplierAccount
from tests.test_catalog_integration import create_product

ADMIN = '/api/v1/admin/marketplace'


async def new_merchant(client, h, name, slug):
    r=await client.post(ADMIN+'/merchants',headers=h,json={'name':name,'slug':slug})
    assert r.status_code == 201, r.text
    return r.json()['id']


async def new_store(client,h,merchant,slug):
    r=await client.post(ADMIN+'/stores',headers=h,json={'merchant_id':merchant,'name':slug.title(),'slug':slug})
    assert r.status_code == 201, r.text
    return r.json()['id']


async def new_user(client,email):
    r=await client.post('/api/v1/accounts/register',json={'email':email,'password':'StrongPasswordXYZ123'})
    assert r.status_code==201,r.text
    logged=await client.post('/api/v1/accounts/login',json={'email':email,'password':'StrongPasswordXYZ123'})
    return r.json()['id'],{'Authorization':'Bearer '+logged.json()['access_token']}


@pytest.mark.asyncio
async def test_multistore_routes_stock_cart_and_tenant_isolation(client,admin_headers):
    _, product=await create_product(client,admin_headers)
    assert (await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)).status_code==200
    m1=await new_merchant(client,admin_headers,'My Shop','my-shop')
    m2=await new_merchant(client,admin_headers,'Other Store','other-shop')
    store=await new_store(client,admin_headers,m1,'store-one')
    other=await new_store(client,admin_headers,m2,'store-two')
    u1,token1=await new_user(client,'owner1@example.com')
    _,token2=await new_user(client,'stranger@example.com')
    member=await client.post(ADMIN+'/memberships',headers=admin_headers,json={'store_id':store,'user_id':u1,'role':'owner'})
    assert member.status_code==201,member.text
    for code,kind in [('whk','warehouse'),('pvzk','pickup')]:
        r=await client.post(ADMIN+'/nodes',headers=admin_headers,json={
            'merchant_id':m1,'store_id':store,'code':code,'name':code.upper(),
            'kind':kind,'city':'Казань','address':'Казань, Тестовая 12'})
        assert r.status_code==201,r.text
        if kind=='warehouse':warehouse=r.json()['id']
        else:pickup=r.json()['id']
    bad=await client.post(ADMIN+'/routes',headers=admin_headers,json={'source_node_id':warehouse,'target_node_id':warehouse,'transit_days':1})
    assert bad.status_code==409
    valid=await client.post(ADMIN+'/routes',headers=admin_headers,json={'source_node_id':warehouse,'target_node_id':pickup,'transit_days':2,'fee':'120.00'})
    assert valid.status_code==201,valid.text
    url='/api/v1/seller/offers'
    payload={'store_id':store,'product_id':product['id'],'sku':'bosch-01','source_kind':'own_stock','price':'2850.00'}
    forbidden=await client.post(url,headers=token2,json=payload)
    assert forbidden.status_code==403
    created=await client.post(url,headers=token1,json=payload)
    assert created.status_code==201,created.text
    offer_id=created.json()['id']
    assert (await client.patch(f'{url}/{offer_id}',headers=token2,json={'status':'active'})).status_code==403
    assert (await client.patch(f'{url}/{offer_id}',headers=token1,json={'status':'active'})).status_code==200
    bad_stock=await client.put(f'{url}/{offer_id}/stock',headers=token1,json={'node_id':pickup,'on_hand':5})
    assert bad_stock.status_code==409  # pickup-only nodes aren't storage warehouses
    ok_stock=await client.put(f'{url}/{offer_id}/stock',headers=token1,json={'node_id':warehouse,'on_hand':3})
    assert ok_stock.status_code==200
    no_city=(await client.get(f'/api/v1/marketplace/products/{product["id"]}/offers?city=Москва')).json()
    assert no_city['offers']==[]
    market=(await client.get(f'/api/v1/marketplace/products/{product["id"]}/offers?pickup_node_id={pickup}')).json()
    assert len(market['offers'])==1,market
    assert market['offers'][0]['fulfillment'][0]['days']==2
    assert market['offers'][0]['fulfillment'][0]['fee']=='120.00'
    assert (await client.get(f'/product/{product["slug"]}?pickup={pickup}')).status_code==200
    added=await client.post('/api/v1/marketplace/cart/items',headers=token1,json={'seller_offer_id':offer_id,'pickup_node_id':pickup,'quantity':2})
    assert added.status_code==201,added.text
    preview=(await client.get('/api/v1/marketplace/cart/preview',headers=token1)).json()
    assert preview['total']=='5700.00' and preview['items'][0]['in_stock']
    assert not preview['payment_enabled']
    too_many=await client.post('/api/v1/marketplace/cart/items',headers=token1,json={'seller_offer_id':offer_id,'pickup_node_id':pickup,'quantity':4})
    assert too_many.status_code==409
    # No stranger may change stock, even if offer IDs are known.
    assert (await client.put(f'{url}/{offer_id}/stock',headers=token2,json={'node_id':warehouse,'on_hand':999})).status_code==403
    assert (await client.get(f'/api/v1/seller/stores/{other}/offers',headers=token1)).status_code==403


@pytest.mark.asyncio
async def test_used_parts_are_separate_linked_items(client,admin_headers):
    _,product=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)
    merchant=await new_merchant(client,admin_headers,'Dismantler','dismantler')
    store=await new_store(client,admin_headers,merchant,'used-store')
    user_id,headers=await new_user(client,'dismantler@example.com')
    await client.post(ADMIN+'/memberships',headers=admin_headers,json={'store_id':store,'user_id':user_id,'role':'owner'})
    node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
        'merchant_id':merchant,'code':'used-stock','name':'Склад разборки',
        'kind':'warehouse_pickup','city':'Самара','address':'Самара, Тестовая 1'},)).json()['id'] if False else None
    # Admin authentication required even for storage provisioning
    assert (await client.post(ADMIN+'/nodes',json={'merchant_id':merchant,'code':'invalid','name':'demo','kind':'warehouse','city':'Самара','address':'Самара Тест'})).status_code==403
    node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
        'merchant_id':merchant,'code':'used-stock','name':'Склад разборки',
        'kind':'warehouse_pickup','city':'Самара','address':'Самара, Тестовая 1'})).json()['id']
    r=await client.post('/api/v1/seller/used-parts',headers=headers,json={
        'store_id':store,'original_product_id':product['id'],'title':'Тормозной диск б/у №1',
        'condition':'used_good','defects':'Следы эксплуатации','mileage_km':83000})
    assert r.status_code==201,r.text
    used_id=r.json()['id']
    # Unpublished listing cannot be activated.
    offer=(await client.post('/api/v1/seller/offers',headers=headers,json={
        'store_id':store,'used_part_id':used_id,'sku':'unique-used-1',
        'source_kind':'own_stock','price':'850.00'})).json()['id']
    assert (await client.patch(f'/api/v1/seller/offers/{offer}',headers=headers,json={'status':'active'})).status_code==409
    await client.patch(f'{ADMIN}/used-parts/{used_id}',headers=admin_headers,json={'status':'published'})
    await client.patch(f'/api/v1/seller/offers/{offer}',headers=headers,json={'status':'active'})
    stock=await client.put(f'/api/v1/seller/offers/{offer}/stock',headers=headers,json={'node_id':node,'on_hand':1})
    assert stock.status_code==200,stock.text
    listing=(await client.get(f'/api/v1/marketplace/products/{product["id"]}/used')).json()
    assert len(listing)==1 and listing[0]['id']==used_id and listing[0]['price']=='850.00'
    used_html=await client.get('/used/'+used_id)
    assert used_html.status_code==200 and 'Тормозной диск б/у' in used_html.text
    assert (await client.get(f'/api/v1/marketplace/products/{product["id"]}/offers')).json()['offers']==[]
    assert (await client.get(f'/api/v1/marketplace/nodes?city=Самара')).json()[0]['id']==node


@pytest.mark.asyncio
async def test_supplier_account_destination_isolation_and_secrets(client,admin_headers,monkeypatch):
    merchant=await new_merchant(client,admin_headers,'Primary','primary-merchant')
    stranger=await new_merchant(client,admin_headers,'Other Merchant','other-merchant')
    node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
        'merchant_id':merchant,'code':'kazan','name':'Kazan Warehouse',
        'kind':'warehouse','city':'Казань','address':'Казань, адрес 8'})).json()['id']
    foreign=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
        'merchant_id':stranger,'code':'moscow','name':'Moscow Warehouse',
        'kind':'warehouse','city':'Москва','address':'Москва, адрес 8'})).json()['id']
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
       json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account=(await client.post(ADMIN+'/supplier-accounts',headers=admin_headers,json={
        'supplier_id':supplier,'merchant_id':merchant,'label':'ROSSKO Kazan',
        'credential_profile':'KAZAN'})).json()['id']
    wrong=await client.post(ADMIN+'/supplier-destinations',headers=admin_headers,json={'account_id':account,'node_id':foreign})
    assert wrong.status_code==409
    mapping=(await client.post(ADMIN+'/supplier-destinations',headers=admin_headers,json={
        'account_id':account,'node_id':node,'external_address_id':'A1',
        'external_delivery_id':'D1'})).json()['id']
    assert (await client.patch(f'{ADMIN}/supplier-destinations/{mapping}',headers=admin_headers,
       json={'enabled':True,'verified':False})).status_code==409
    # Plaintext credentials are never written or shown in the API.
    accounts=(await client.get(ADMIN+'/supplier-accounts',headers=admin_headers)).json()
    assert 'password' not in str(accounts).lower()
    assert accounts[0]['destinations'][0]['enabled'] is False
    # Named profile never inherits default ROSSKO key material.
    from app.modules.marketplace.account_credentials import for_account
    mock=SupplierAccount(credential_profile='KAZAN')
    monkeypatch.setenv('ROSSKO_KEY1','SHOULD_NOT_BE_USED')
    assert not ready('rossko',mock)
    monkeypatch.setenv('SUPPLIER_PROFILE_KAZAN_ROSSKO_KEY1','k1')
    monkeypatch.setenv('SUPPLIER_PROFILE_KAZAN_ROSSKO_KEY2','k2')
    monkeypatch.setenv('SUPPLIER_PROFILE_KAZAN_ROSSKO_DELIVERY_ID','d')
    assert ready('rossko',mock)
    assert for_account('rossko',mock).rossko_key1=='k1'


@pytest.mark.asyncio
async def test_two_accounts_same_supplier_never_merge_snapshot_context(client,admin_headers,monkeypatch):
    from tests.test_discovery_v06 import db_session
    from app.core.config import get_settings
    from app.modules.offer_engine.providers import Offer
    from app.modules.commerce.models import Job
    from app.modules.suppliers.models import OfferSnapshot
    import app.workers.runner as runner
    from sqlalchemy import select,func
    merchant=await new_merchant(client,admin_headers,'Shop Supplier','supplier-merchant')
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
       json={'code':'partkom','name':'ПартКом'})).json()['id']
    for city,profile,code,price in [('Казань','KAZ','kz',Decimal('1200')),
                                    ('Москва','MSK','ms',Decimal('1300'))]:
        node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
            'merchant_id':merchant,'code':code,'name':city,'kind':'warehouse_pickup',
            'city':city,'address':city+', тест 12'})).json()['id']
        account=(await client.post(ADMIN+'/supplier-accounts',headers=admin_headers,json={
            'supplier_id':supplier,'merchant_id':merchant,'label':'partkom '+city,
            'credential_profile':profile})).json()['id']
        mapping=(await client.post(ADMIN+'/supplier-destinations',headers=admin_headers,json={
            'account_id':account,'node_id':node})).json()['id']
        await client.patch(f'{ADMIN}/supplier-destinations/{mapping}',headers=admin_headers,
                          json={'verified':True,'enabled':True})
        await client.patch(f'{ADMIN}/supplier-accounts/{account}',headers=admin_headers,
                          json={'enabled':True,'read_enabled':True})
        monkeypatch.setenv('SUPPLIER_PROFILE_'+profile+'_PARTKOM_LOGIN',code)
        monkeypatch.setenv('SUPPLIER_PROFILE_'+profile+'_PARTKOM_PASSWORD','test')
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    get_settings.cache_clear()
    resp=await client.get('/catalog?q=BOSCH+0986479088')
    assert resp.status_code==200,resp.text
    async for db in db_session():
        all_jobs=(await db.scalars(select(Job).where(Job.kind=='search'))).all()
        # Other suppliers are allowed to schedule their own legacy read-only
        # jobs. Only PARTKOM is configured for independent API accounts here.
        jobs=[j for j in all_jobs if j.payload['supplier']=='partkom']
        assert len(jobs)==2,[(j.kind,j.payload) for j in all_jobs]
        assert all(j.payload.get('account_id') and j.payload.get('destination_id') for j in jobs)
        assert len({j.payload['account_id'] for j in jobs})==2
        assert len({j.payload['key'] for j in jobs})==2
        class PartkomFake:
            async def search(self,brand,article,config=None):
                assert config.partkom_login in ('kz','ms')
                price=Decimal('1200') if config.partkom_login=='kz' else Decimal('1300')
                return [Offer('partkom','same-key',brand,article,price,5,1,'tk','warehouse')]
        monkeypatch.setitem(runner.PROVIDERS,'partkom',PartkomFake())
        for job in jobs:
            await runner.search(db,job)
            await db.commit()
        snaps=(await db.scalars(select(OfferSnapshot))).all()
        assert len(snaps)==2
        assert len({s.supplier_account_id for s in snaps})==2
        assert {s.purchase_price for s in snaps}=={Decimal('1200'),Decimal('1300')}
        # Legacy supplier offer display can't take the other account's price.
        assert (await db.scalar(select(func.count()).select_from(OfferSnapshot).where(
            OfferSnapshot.supplier_account_id.is_(None))))==0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_demo_orders_are_admin_only_and_lock_unique_used_stock(client,admin_headers,monkeypatch):
    from app.core.config import get_settings
    merchant=await new_merchant(client,admin_headers,'Unique','unique-merchant')
    store=await new_store(client,admin_headers,merchant,'unique-store')
    customer_id,customer=await new_user(client,'unique@example.com')
    await client.post(ADMIN+'/memberships',headers=admin_headers,json={
        'store_id':store,'user_id':customer_id,'role':'owner'})
    node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
        'merchant_id':merchant,'code':'unique-stock','name':'Unique stock',
        'kind':'warehouse_pickup','city':'Тула','address':'Тула, Тест 5'})).json()['id']
    used=(await client.post('/api/v1/seller/used-parts',headers=customer,json={
        'store_id':store,'title':'Unique starter','condition':'used_good'})).json()['id']
    await client.patch(f'{ADMIN}/used-parts/{used}',headers=admin_headers,json={'status':'published'})
    offer=(await client.post('/api/v1/seller/offers',headers=customer,json={
        'store_id':store,'used_part_id':used,'sku':'unique-1','price':'4000.00'})).json()['id']
    await client.patch(f'/api/v1/seller/offers/{offer}',headers=customer,json={'status':'active'})
    await client.put(f'/api/v1/seller/offers/{offer}/stock',headers=customer,json={'node_id':node,'on_hand':1})
    added=await client.post('/api/v1/marketplace/cart/items',headers=customer,json={
        'seller_offer_id':offer,'pickup_node_id':node,'quantity':1})
    assert added.status_code==201
    params={'customer_id':customer_id,'idempotency_key':'simulate-unique-001'}
    assert (await client.post(ADMIN+'/simulate-order',json=params)).status_code==403
    blocked=await client.post(ADMIN+'/simulate-order',headers=admin_headers,json=params)
    assert blocked.status_code==403
    monkeypatch.setenv('MARKETPLACE_DEMO_MODE','true')
    get_settings.cache_clear()
    simulated=await client.post(ADMIN+'/simulate-order',headers=admin_headers,json=params)
    assert simulated.status_code==200,simulated.text
    assert simulated.json()['simulation_only'] and not simulated.json()['payment_created']
    repeat=await client.post(ADMIN+'/simulate-order',headers=admin_headers,json=params)
    assert repeat.status_code==200 and repeat.json()['reused']
    from app.modules.marketplace.models import InventoryBalance,SellerOrder
    from app.modules.commerce.models import Procurement
    from tests.test_discovery_v06 import db_session
    from sqlalchemy import select,func
    async for db in db_session():
        bal=await db.scalar(select(InventoryBalance))
        assert bal.on_hand==bal.reserved==1
        assert (await db.scalar(select(func.count()).select_from(SellerOrder)))==1
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_simulated_cart_splits_two_sellers_no_external_operations(client,admin_headers,monkeypatch):
    from app.core.config import get_settings
    buyer_id,buyer=await new_user(client,'buyer-multi@example.com')
    for ix in (1,2):
        _,product=await create_unique_product(client,admin_headers,ix)
        m=await new_merchant(client,admin_headers,'Shop '+str(ix),'shop-'+str(ix))
        store=await new_store(client,admin_headers,m,'store-'+str(ix))
        owner_id,owner=await new_user(client,f'owner-multi{ix}@example.com')
        await client.post(ADMIN+'/memberships',headers=admin_headers,json={
            'store_id':store,'user_id':owner_id,'role':'owner'})
        node=(await client.post(ADMIN+'/nodes',headers=admin_headers,json={
            'merchant_id':m,'code':f'n{ix}','name':'Warehouse '+str(ix),
            'kind':'warehouse_pickup','city':'Казань','address':'Казань, улица 12'})).json()['id']
        offer=(await client.post('/api/v1/seller/offers',headers=owner,json={
            'store_id':store,'product_id':product['id'],'sku':f'o{ix}',
            'price':str(1000*ix)+'.00'})).json()['id']
        await client.patch('/api/v1/seller/offers/'+offer,headers=owner,json={'status':'active'})
        await client.put('/api/v1/seller/offers/'+offer+'/stock',headers=owner,
                         json={'node_id':node,'on_hand':10})
        cart=await client.post('/api/v1/marketplace/cart/items',headers=buyer,json={
            'seller_offer_id':offer,'pickup_node_id':node,'quantity':1})
        assert cart.status_code==201,cart.text
    monkeypatch.setenv('MARKETPLACE_DEMO_MODE','true')
    get_settings.cache_clear()
    done=await client.post(ADMIN+'/simulate-order',headers=admin_headers,json={
        'customer_id':buyer_id,'idempotency_key':'two-sellers-simulated'})
    assert done.status_code==200,done.text
    assert done.json()['seller_orders']==2 and done.json()['total']=='3000.00'
    assert done.json()['payment_created'] is False
    from app.modules.marketplace.models import SellerOrder
    from app.modules.payments.models import Payment
    from app.modules.commerce.models import Procurement
    from tests.test_discovery_v06 import db_session
    from sqlalchemy import select,func
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(SellerOrder)))==2
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
    get_settings.cache_clear()


async def create_unique_product(client,headers,ix):
    brand=await client.post('/api/v1/backoffice/brands',headers=headers,
                            json={'name':'BRAND'+str(ix),'slug':'brand'+str(ix)})
    product=await client.post('/api/v1/backoffice/products',headers=headers,json={
        'brand_id':brand.json()['id'],'article':'999900'+str(ix),
        'name':'Test product '+str(ix)})
    assert product.status_code==201,product.text
    await client.post('/api/v1/backoffice/products/'+product.json()['id']+'/publish',headers=headers)
    return brand.json(),product.json()
