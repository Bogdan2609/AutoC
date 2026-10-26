"""Supplier engine contract tests do not access live supplier/payment endpoints."""
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import select
import pytest
from app.core.db import get_session
from app.main import app
from app.modules.commerce.models import Job
from app.modules.offer_engine.models import CacheState,SupplierLog
from app.modules.offer_engine.providers import Offer
import app.workers.runner as runner
from tests.test_catalog_integration import create_product

async def facade():
    async for db in app.dependency_overrides[get_session]():
        yield db

@pytest.mark.asyncio
async def test_supplier_policy_and_access(client,admin_headers,monkeypatch):
    # This access-policy test must not depend on any local .env credentials.
    # Real credential readiness is exercised by dedicated adapter tests.
    monkeypatch.setattr('app.modules.offer_engine.operations.credentials_ready', lambda code: False)
    assert (await client.get('/api/v1/backoffice/offer-engine/suppliers')).status_code==403
    r=await client.get('/api/v1/backoffice/offer-engine/suppliers',headers=admin_headers)
    assert r.status_code==200,r.text
    assert {x['code'] for x in r.json()}=={'adeo','rossko','partkom','forum-auto'}
    assert all(x['procurement_enabled'] is False for x in r.json())
    assert all(x['credentials_configured'] is False for x in r.json())
    resp=await client.patch('/api/v1/backoffice/offer-engine/suppliers/adeo/configuration',headers=admin_headers,
         json={'enabled':False,'positive_ttl_seconds':450,'empty_ttl_seconds':80,'timeout_seconds':30,'min_interval_seconds':5})
    assert resp.status_code==200,resp.text
    assert (resp.json()['enabled'],resp.json()['positive_ttl_seconds'])==(False,450)
    assert (await client.patch('/api/v1/backoffice/offer-engine/suppliers/adeo/configuration',headers=admin_headers,
                               json={'timeout_seconds':400})).status_code==422
    assert (await client.get('/api/v1/backoffice/offer-engine/suppliers/unknown/cache',headers=admin_headers)).status_code==404

@pytest.mark.asyncio
async def test_enqueue_dedup_and_disable(client,admin_headers):
    _,product=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)
    assert (await client.post('/api/v1/offer-cache/products/'+product['id']+'/refresh')).status_code==403
    page=await client.get('/product/'+product['slug'])
    assert page.status_code==200
    page=await client.get('/product/'+product['slug'])
    assert page.status_code==200
    jobs=(await client.get('/api/v1/backoffice/operations/jobs',headers=admin_headers)).json()
    assert len([j for j in jobs if j['kind']=='search'])==4
    r=await client.post('/api/v1/backoffice/offer-engine/products/'+product['id']+'/refresh?code=adeo',headers=admin_headers)
    assert r.status_code==202
    assert r.json()['refresh'][0]['enqueued'] is False  # active five-minute lease
    await client.patch('/api/v1/backoffice/offer-engine/suppliers/partkom/configuration',headers=admin_headers,json={'enabled':False})
    c=await client.get('/api/v1/backoffice/offer-engine/suppliers/partkom/cache',headers=admin_headers)
    assert c.status_code==200 and len(c.json())==1
    assert c.json()[0]['product_id']==product['id']

@pytest.mark.asyncio
async def test_worker_normalizes_real_offer_and_logs(client,admin_headers,monkeypatch):
    _,p=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{p["id"]}/publish',headers=admin_headers)
    await client.post('/api/v1/backoffice/price-policy',headers=admin_headers,json={'markup_percent':'25.00'})
    await client.get('/product/'+p['slug'])
    class Fake:
        async def search(self,brand,article):
            return [Offer('partkom','maker:0986479088:provider:0',brand,article,
                          Decimal('200.00'),4,1,'provider','WH1',1,1,{'provider_id':'provider'})]
    monkeypatch.setitem(runner.PROVIDERS,'partkom',Fake())
    async for db in facade():
        job=await db.scalar(select(Job).where(Job.kind=='search'))
        alljobs=(await db.scalars(select(Job))).all()
        job=next(j for j in alljobs if j.payload['supplier']=='partkom')
        await runner.search(db,job)
        await db.commit()
        state=await db.scalar(select(CacheState).where(CacheState.supplier_code=='partkom'))
        assert state.status=='ready'
        assert state.result_count==1
        assert state.failure_count==0
        assert state.last_success_at is not None
        log=await db.scalar(select(SupplierLog).where(SupplierLog.code=='partkom'))
        assert log.outcome=='success' and log.result_count==1
    offers=await client.get('/api/v1/products/'+p['id']+'/offers')
    assert offers.status_code==200,offers.text
    assert offers.json()[0]['retail_price']=='250.00'
    admin=await client.get('/api/v1/backoffice/operations/suppliers/partkom/offers',headers=admin_headers)
    assert admin.status_code==200
    assert admin.json()[0]['current_offer']['warehouse']=='WH1'
    assert 'external_token' not in str(admin.json())
    logs=await client.get('/api/v1/backoffice/offer-engine/suppliers/partkom/logs',headers=admin_headers)
    assert logs.json()[0]['duration_ms']>=0

@pytest.mark.asyncio
async def test_error_backoff_and_negative_results(client,admin_headers,monkeypatch):
    _,p=await create_product(client,admin_headers)
    await client.post(f'/api/v1/backoffice/products/{p["id"]}/publish',headers=admin_headers)
    await client.get('/product/'+p['slug'])
    class Missing:
        async def search(self,brand,article):
            return []
    class Fail:
        async def search(self,brand,article):
            raise RuntimeError('masked supplier failure')
    monkeypatch.setitem(runner.PROVIDERS,'rossko',Missing())
    monkeypatch.setitem(runner.PROVIDERS,'adeo',Fail())
    async for db in facade():
        jobs=(await db.scalars(select(Job))).all()
        for name in ('rossko','adeo'):
            await runner.search(db,next(j for j in jobs if j.payload['supplier']==name))
            await db.commit()
        states=(await db.scalars(select(CacheState))).all()
        assert next(x for x in states if x.supplier_code=='rossko').status=='empty'
        err=next(x for x in states if x.supplier_code=='adeo')
        assert err.status=='error' and err.error_type=='RuntimeError' and err.failure_count==1
    rows=(await client.get('/api/v1/backoffice/offer-engine/suppliers/adeo/logs',headers=admin_headers)).json()
    assert rows[0]['error_type']=='RuntimeError'
    assert 'masked supplier failure' not in str(rows)

@pytest.mark.asyncio
async def test_empty_search_hides_previous_supplier_offer(client,admin_headers,monkeypatch):
    _,p=await create_product(client,admin_headers)
    await client.post('/api/v1/backoffice/products/'+p['id']+'/publish',headers=admin_headers)
    await client.post('/api/v1/backoffice/price-policy',headers=admin_headers,json={'markup_percent':'25'})
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,json={'code':'partkom','name':'Partkom'})).json()
    item=(await client.post('/api/v1/backoffice/supplier-items',headers=admin_headers,json={
        'supplier_id':supplier['id'],'product_id':p['id'],'external_key':'old-offer',
        'raw_brand':'BOSCH','raw_article':'0986479088'})).json()
    await client.post('/api/v1/backoffice/offer-snapshots',headers=admin_headers,json={'supplier_item_id':item['id'],
        'purchase_price':'200','quantity':4})
    assert len((await client.get('/api/v1/products/'+p['id']+'/offers')).json())==1
    await client.get('/product/'+p['slug'])
    class Empty:
        async def search(self,brand,article):return []
    monkeypatch.setitem(runner.PROVIDERS,'partkom',Empty())
    async for db in facade():
        jobs=(await db.scalars(select(Job))).all()
        await runner.search(db,next(j for j in jobs if j.payload['supplier']=='partkom'))
        await db.commit()
    assert (await client.get('/api/v1/products/'+p['id']+'/offers')).json()==[]
    # Historical snapshot is still visible to operators.
    admin=(await client.get('/api/v1/backoffice/operations/suppliers/partkom/offers',headers=admin_headers)).json()
    assert len(admin)==1 and admin[0]['current_offer']['purchase']=='200.00'

@pytest.mark.asyncio
async def test_hot_refresh_requires_repeated_customer_demand(client,admin_headers):
    from datetime import timedelta
    from app.modules.accounts.models import utc
    from app.modules.offer_engine.service import hot_refresh,enqueue_product
    from app.modules.catalog.models import Product,Brand
    _,p=await create_product(client,admin_headers)
    await client.post('/api/v1/backoffice/products/'+p['id']+'/publish',headers=admin_headers)
    await client.get('/product/'+p['slug'])
    async for db in facade():
        states=(await db.scalars(select(CacheState))).all()
        assert len(states)==4
        for state in states:
            state.expires_at=utc()-timedelta(seconds=1)
        await db.commit()
        assert await hot_refresh(db)==0  # one cold view must not trigger periodic polling
        states=(await db.scalars(select(CacheState))).all()
        for state in states:
            state.last_demand_at=utc()-timedelta(minutes=2)
        await db.commit()
        from uuid import UUID
        product=await db.get(Product,UUID(p['id']))
        brand=await db.get(Brand,product.brand_id)
        await enqueue_product(db,product,brand)
        states=(await db.scalars(select(CacheState))).all()
        assert all(x.demand_count==2 for x in states)
        for state in states:
            state.expires_at=utc()-timedelta(seconds=1)
        await db.commit()
        assert await hot_refresh(db)>=1
        # Background work does not itself reset the customer-demand clock.
        states=(await db.scalars(select(CacheState))).all()
        assert all(x.demand_count==2 for x in states)

