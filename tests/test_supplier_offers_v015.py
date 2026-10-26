"""v0.15 contract: latest successful batch, account isolation, grouping and route provenance."""
from decimal import Decimal
from uuid import UUID
from types import SimpleNamespace

import pytest

from app.main import app
from app.core.db import get_session
from app.modules.accounts.models import utc
from app.modules.marketplace.grouping import group_marketplace_offers
from app.modules.marketplace.routing import describe_supplier_route
from app.modules.marketplace.account_search import account_key
from app.modules.offer_engine.models import CacheState, OfferDetail
from app.modules.pricing.models import PricePolicy
from app.modules.suppliers.models import SupplierItem, OfferSnapshot
from tests.test_catalog_integration import create_product
from tests.test_marketplace_foundation_v07 import new_merchant
from tests.test_v014_integration import new_node

M='/api/v1/admin/marketplace'


async def setup_account(client, h, supplier, merchant, node, suffix):
    resp=await client.post(M+'/supplier-accounts',headers=h,json={
        'supplier_id':supplier,'merchant_id':merchant,'label':'Account '+suffix,
        'credential_profile':'A'+suffix})
    assert resp.status_code==201,resp.text
    account=resp.json()['id']
    resp=await client.post(M+'/supplier-destinations',headers=h,json={
        'account_id':account,'node_id':node,'external_delivery_id':'000000002'})
    assert resp.status_code==201,resp.text
    dest=resp.json()['id']
    assert (await client.patch(M+'/supplier-accounts/'+account,headers=h,
            json={'enabled':True,'read_enabled':True})).status_code==200
    assert (await client.patch(M+'/supplier-destinations/'+dest,headers=h,
            json={'verified':True,'enabled':True})).status_code==200
    return account,dest


@pytest.mark.asyncio
async def test_current_search_excludes_disappeared_warehouse_but_keeps_other_account(client,admin_headers):
    brand, product=await create_product(client,admin_headers)
    assert (await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)).status_code==200
    merchant=await new_merchant(client,admin_headers,'Supplier Merchant','supply-v015')
    node=await new_node(client,admin_headers,merchant,'node-v015','warehouse','Самара')
    node2=await new_node(client,admin_headers,merchant,'node2-v015','warehouse','Казань')
    resp=await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
                           json={'code':'rossko','name':'ROSSKO'})
    assert resp.status_code==201,resp.text
    supplier=resp.json()['id']
    account1,dest1=await setup_account(client,admin_headers,supplier,merchant,node,'ONE')
    account2,dest2=await setup_account(client,admin_headers,supplier,merchant,node2,'TWO')
    async for db in app.dependency_overrides[get_session]():
        db.add(PricePolicy(name='default',markup_percent=Decimal('20'),active=True))
        itemA=SupplierItem(supplier_id=UUID(supplier),product_id=UUID(product['id']),
             external_key='A:WH',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed',
             last_search_token='other-account-token')
        itemB=SupplierItem(supplier_id=UUID(supplier),product_id=UUID(product['id']),
             external_key='B:WH',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed',
             last_search_token='other-account-token')
        db.add(itemA);db.add(itemB);await db.flush()
        async def snap(item,account,dest,job,warehouse,price,qty=7):
            s=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
                supplier_destination_id=UUID(dest),purchase_price=Decimal(price),quantity=qty,
                lead_time_days=2)
            db.add(s);await db.flush()
            db.add(OfferDetail(snapshot_id=s.id,external_token=warehouse,warehouse=warehouse,
                   verified_live=True,metadata_public={'_search_job_id':job}))
            return s.id
        # Old job: both warehouses existed. Last successful job: warehouse B disappeared.
        await snap(itemA,account1,dest1,'old-job','WH-A','90.00')
        await snap(itemB,account1,dest1,'old-job','WH-B','80.00')
        current=await snap(itemA,account1,dest1,'new-job','WH-A','100.00')
        # Independent second account: the same warehouse has a different price.
        other=await snap(itemA,account2,dest2,'other-job','WH-A','70.00')
        db.add(CacheState(supplier_code='rossko',lookup_key=account_key('BOSCH','0986479088',UUID(account1),UUID(dest1)),
                 product_id=UUID(product['id']),status='ready',last_completed_job_id='new-job',
                 result_count=1,expires_at=utc()))
        db.add(CacheState(supplier_code='rossko',lookup_key=account_key('BOSCH','0986479088',UUID(account2),UUID(dest2)),
                 product_id=UUID(product['id']),status='ready',last_completed_job_id='other-job',
                 result_count=1,expires_at=utc()))
        await db.commit()
    url='/api/v1/marketplace/products/'+product['id']+'/offers'
    response=await client.get(url)
    assert response.status_code==200,response.text
    quotes=response.json()['offers']
    assert len(quotes)==2,quotes
    assert {x['snapshot_id'] for x in quotes}=={str(current),str(other)}
    assert {x['supplier_account_id'] for x in quotes}=={account1,account2}
    assert {x['price'] for x in quotes}=={'120.00','84.00'}
    assert all(x['fulfillment'][0]['delivery_scope']=='receiving_only' for x in quotes)
    assert all(x['fulfillment'][0]['route_legs'][0]['kind']=='supplier_to_receiving' for x in quotes)
    grouped=(await client.get(url+'/grouped')).json()
    assert grouped['group_count']==1
    assert grouped['variant_count']==2
    assert grouped['groups'][0]['account_count']==2
    assert grouped['groups'][0]['min_price']=='84.00'
    html=(await client.get('/product/'+product['slug']))
    assert html.status_code==200,html.text
    assert 'class="supplier-group"' in html.text
    assert 'До пункта приёмки, не до покупателя' in html.text

    # A buyer-selected pickup is not interchangeable with the account's
    # supplier receiving node. Only configured in-merchant edges may bridge it.
    hub=await new_node(client,admin_headers,merchant,'hub-v015','warehouse','Казань')
    pickup=await new_node(client,admin_headers,merchant,'pickup-v015','pickup','Казань')
    for origin,target,days,fee in [(node,hub,2,'90.00'),(hub,pickup,1,'45.00')]:
        added=await client.post(M+'/routes',headers=admin_headers,json={
            'source_node_id':origin,'target_node_id':target,'transit_days':days,'fee':fee})
        assert added.status_code==201,added.text
    picked=await client.get(url+'?pickup_node_id='+pickup)
    assert picked.status_code==200,picked.text
    picked_quotes=picked.json()['offers']
    assert len(picked_quotes)==1,picked.text
    pickup_option=picked_quotes[0]['fulfillment'][0]
    assert pickup_option['pickup_node_id']==pickup
    assert pickup_option['receiving_node_id']==node
    assert pickup_option['supplier_warehouse']=='WH-A'
    assert pickup_option['days']==5
    assert pickup_option['fee']=='135.00'
    assert pickup_option['supplier_fee_known'] is False
    assert [leg['kind'] for leg in pickup_option['route_legs']]==[
        'supplier_to_receiving','receiving_to_pickup','receiving_to_pickup']
    assert [leg['to_node_id'] for leg in pickup_option['route_legs']]==[node,hub,pickup]
    assert (await client.get(url+'?pickup_node_id='+node2)).status_code==404

    # Success with 0 results removes ALL current account1 quotes, not account2.
    async for db in app.dependency_overrides[get_session]():
        state=await db.scalar(__import__('sqlalchemy').select(CacheState).where(
            CacheState.lookup_key==account_key('BOSCH','0986479088',UUID(account1),UUID(dest1))))
        state.status='empty';state.result_count=0;state.last_completed_job_id='zero-job'
        await db.commit()
    quotes=(await client.get(url)).json()['offers']
    assert len(quotes)==1 and quotes[0]['supplier_account_id']==account2


def test_supplier_route_known_and_unknown_legs():
    receiving=SimpleNamespace(id='node-a',name='Our Samara hub',city='Самара',kind='warehouse')
    detail=SimpleNamespace(warehouse='HST181')
    quote=SimpleNamespace(lead_time_days=1)
    bare=describe_supplier_route(quote,detail,receiving)
    assert bare['supplier_warehouse']=='HST181'
    assert bare['delivery_scope']=='receiving_only'
    assert bare['pickup_node_id'] is None
    assert bare['supplier_fee_known'] is False
    assert bare['days']==1
    route={'city':'Казань','days':3,'fee':'135.00','pickup_node_id':'pickup-b',
           'route_node_ids':['node-a','hub','pickup-b'],
           'legs':[{'kind':'receiving_to_pickup','from_node_id':'node-a','to_node_id':'hub',
                    'days':2,'fee':'90.00'},
                   {'kind':'receiving_to_pickup','from_node_id':'hub','to_node_id':'pickup-b',
                    'days':1,'fee':'45.00'}]}
    full=describe_supplier_route(quote,detail,receiving,route,pickup_selected=True)
    assert full['days']==4
    assert full['delivery_scope']=='pickup'
    assert [x['kind'] for x in full['route_legs']]==['supplier_to_receiving','receiving_to_pickup','receiving_to_pickup']
    assert full['fee']=='135.00' and full['supplier_fee_known'] is False
    quote.lead_time_days=None
    unknown=describe_supplier_route(quote,detail,receiving,route,pickup_selected=True)
    assert unknown['days'] is None and unknown['supplier_days'] is None


def test_grouping_preserves_81_distinct_snapshots_and_exact_lower_bound():
    rows=[]
    for index in range(81):
        rows.append({'source_kind':'supplier_quote','supplier_code':'adeo','merchant':'AutoCodex',
            'merchant_id':'auto','store':'AutoCodex','supplier_account_id':'account-adeo',
            'supplier_destination_id':'destination','snapshot_id':f'adeo-{index}',
            'warehouse_id':f'Cella{index:03d}/1', 'price':'688.19',
            'fulfillment':[{'stock':5,'stock_display':'>4','days':1,'fee':'0.00',
                            'route_node_ids':['node-a']}],'requires_live_validation':True})
    for code,price in [('rossko','590.72'),('forum-auto','590.76')]:
        rows.append({**rows[0], 'supplier_code':code, 'snapshot_id':code,
                      'warehouse_id':code,'price':price})
    groups=group_marketplace_offers(rows)
    assert len(groups)==3
    assert sum(g['variant_count'] for g in groups)==83
    adeo=next(g for g in groups if g['supplier_code']=='adeo')
    assert adeo['variant_count']==81 and adeo['warehouse_count']==81
    assert adeo['variants'][0]['fulfillment'][0]['stock_display']=='>4'
    assert groups[0]['supplier_code']=='rossko'

@pytest.mark.asyncio
async def test_worker_marks_live_quotes_with_own_completed_search_job(client,admin_headers,monkeypatch):
    """Worker, not an admin/UI fixture, creates authoritative provenance."""
    from app.core.config import get_settings
    from app.modules.commerce.models import Job
    from app.modules.offer_engine.providers import Offer,PROVIDERS
    from app.workers.runner import search as run_search
    from app.modules.marketplace.account_search import account_key
    from sqlalchemy import select
    from datetime import timedelta
    brand,product=await create_product(client,admin_headers)
    assert (await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)).status_code==200
    merchant=await new_merchant(client,admin_headers,'Worker Source','worker-v015')
    node=await new_node(client,admin_headers,merchant,'work-v015-node','warehouse','Самара')
    supp=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
            json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account,dest=await setup_account(client,admin_headers,supp,merchant,node,'WORK')
    monkeypatch.setenv('SUPPLIER_PROFILE_AWORK_ROSSKO_KEY1','mock-key-1')
    monkeypatch.setenv('SUPPLIER_PROFILE_AWORK_ROSSKO_KEY2','mock-key-2')
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    get_settings.cache_clear()
    async def fake_search(brand_name,article,config):
        return [Offer('rossko','GUID:A','BOSCH','0986479088',Decimal('200.00'),8,2,
                      'GUID:A','WAREHOUSE-A')]
    monkeypatch.setattr(PROVIDERS['rossko'],'search',fake_search)
    async for db in app.dependency_overrides[get_session]():
        request_time=utc()
        job=Job(kind='search',status='pending',dedup_key='v015-search-one',payload={
            'supplier':'rossko','brand':'BOSCH','article':'0986479088',
            'account_id':account,'destination_id':dest,'product_id':product['id'],
            'key':account_key('BOSCH','0986479088',UUID(account),UUID(dest)),
            'requested_at':request_time.isoformat()})
        db.add(job)
        state=CacheState(supplier_code='rossko',lookup_key=job.payload['key'],
           product_id=UUID(product['id']),status='refreshing',last_requested_at=request_time,
           expires_at=request_time+timedelta(minutes=5))
        db.add(state)
        db.add(PricePolicy(name='default',markup_percent=Decimal('20'),active=True))
        await db.flush()
        await run_search(db,job)
        await db.commit()
        state=await db.scalar(select(CacheState).where(CacheState.lookup_key==job.payload['key']))
        assert state.last_completed_job_id==str(job.id)
        result=(await db.execute(select(OfferSnapshot,OfferDetail)
            .join(OfferDetail,OfferDetail.snapshot_id==OfferSnapshot.id))).all()
        assert len(result)==1
        snap,detail=result[0]
        assert detail.metadata_public['_search_job_id']==str(job.id)
        assert snap.supplier_account_id==UUID(account)
        assert snap.supplier_destination_id==UUID(dest)
    response=await client.get('/api/v1/marketplace/products/'+product['id']+'/offers')
    assert response.status_code==200,response.text
    assert len(response.json()['offers'])==1
    get_settings.cache_clear()


def test_grouped_seller_stock_keeps_multiple_configured_pickup_paths():
    stock={'source_kind':'own_stock','offer_id':'seller-1','merchant':'Owner',
           'merchant_id':'merchant-1','store':'Store','price':'100.00',
           'fulfillment':[{'stock':2,'days':1,'fee':'0.00','route_node_ids':['source','pickup-A']},
                          {'stock':2,'days':3,'fee':'10.00','route_node_ids':['source','pickup-B']}]}
    groups=group_marketplace_offers([stock])
    assert len(groups)==1 and groups[0]['variant_count']==2
    assert groups[0]['variants'][0]['fulfillment'][0]['route_node_ids'][-1]=='pickup-A'
