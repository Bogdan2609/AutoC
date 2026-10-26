"""Read-only preview and mutable admin network: no supplier HTTP or orders."""
import uuid
from decimal import Decimal
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.modules.marketplace.quote_preview import quote_breakdown
from tests.test_marketplace_foundation_v07 import new_merchant
from tests.test_v014_integration import new_node
from tests.test_supplier_offers_v015 import setup_account

M='/api/v1/admin/marketplace'


def variant(**kwargs):
    return {'source_kind':'supplier_quote','supplier_code':'rossko',
            'snapshot_id':'sample','supplier_account_id':'abc','supplier_destination_id':'dest',
            'warehouse_id':'WH','price':'590.72',**kwargs}


def option(stock=5, fee='135.00', pickup='pvz'):
    return {'stock':stock,'stock_display':'>4','fee':fee,'days':4,
            'supplier_fee_known':False,'pickup_node_id':pickup,
            'route_node_ids':['receiving','pvz'], 'route_legs':[]}


def test_price_breakdown_is_explicitly_not_grand_total():
    result=quote_breakdown(variant(),2,option())
    assert result['goods_subtotal']=='1181.44'
    assert result['merchant_delivery_fee']=='135.00'
    assert result['known_components_total']=='1316.44'
    assert result['grand_total'] is None
    assert not result['payment_enabled'] and not result['cart_enabled']
    assert result['available_stock_lower_bound']==5 and result['stock_display']=='>4'
    assert result['pickup_selected']
    assert quote_breakdown(variant(),1,option(pickup=None))['grand_total'] is None


def test_preview_honors_min_qty_and_pack_and_stock():
    item=variant(min_qty=4,pack_multiple=2)
    for quantity in (1,3,5,6):
        if quantity==6:continue
        with pytest.raises(HTTPException): quote_breakdown(item,quantity,option(stock=5))
    assert quote_breakdown(item,4,option(stock=5))['quantity']==4
    with pytest.raises(HTTPException):quote_breakdown(item,6,option(stock=5))
    with pytest.raises(HTTPException):quote_breakdown(variant(),2,option(stock=1))


@pytest.mark.asyncio
async def test_quote_preview_rejects_unverified_and_bad_identity(client):
    unknown=str(uuid.uuid4()); endpoint=f'/api/v1/marketplace/products/{unknown}/offers/preview'
    payload={'snapshot_id':unknown,'supplier_account_id':unknown,
             'supplier_destination_id':unknown,'quantity':1}
    resp=await client.post(endpoint,json=payload)
    assert resp.status_code==404  # not published, no supplier calls
    assert (await client.post(endpoint,json={**payload,'quantity':0})).status_code==422


@pytest.mark.asyncio
async def test_admin_route_edit_and_receiving_change_revokes_destination(client,admin_headers):
    merchant=await new_merchant(client,admin_headers,'Merchant Sixteen','m-sixteen')
    node=await new_node(client,admin_headers,merchant,'v016-receiving','warehouse','Самара')
    pvz=await new_node(client,admin_headers,merchant,'v016-pickup','pickup','Самара')
    foreign_merchant=await new_merchant(client,admin_headers,'Another Merchant','m-other-sixteen')
    foreign=await new_node(client,admin_headers,foreign_merchant,'v016-foreign','pickup','Казань')
    created=await client.post(M+'/routes',headers=admin_headers,json={
        'source_node_id':node,'target_node_id':pvz,'transit_days':2,'fee':'99.00'})
    assert created.status_code==201,created.text
    route=created.json()['id']
    assert (await client.patch(M+'/routes/'+route,json={'fee':'200.00'})).status_code==403
    changed=await client.patch(M+'/routes/'+route,headers=admin_headers,json={
        'fee':'135.00','transit_days':3,'handling_days':1,'active':False})
    assert changed.status_code==200,changed.text
    assert changed.json()['fee']=='135.00' and changed.json()['active'] is False
    assert (await client.patch(M+'/routes/'+route,headers=admin_headers,json={'fee':'-1.00'})).status_code==422
    assert (await client.patch(M+'/routes/'+route,headers=admin_headers,json={'active':True})).status_code==200
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,json={
       'code':'rossko','name':'ROSSKO'})).json()['id']
    account,dest=await setup_account(client,admin_headers,supplier,merchant,node,'V016')
    reply=await client.patch(M+'/nodes/'+node,headers=admin_headers,json={'address':'Самара, новый адрес 42'})
    assert reply.status_code==200,reply.text
    assert reply.json()['destinations_requiring_reverification']==1
    accounts=(await client.get(M+'/supplier-accounts',headers=admin_headers)).json()
    item=next(x for x in accounts if x['id']==account)
    dst=next(x for x in item['destinations'] if x['id']==dest)
    assert dst['verified'] is False and dst['enabled'] is False
    # Re-approval is explicit and must not be restored by another harmless edit.
    assert (await client.patch(M+'/nodes/'+node,headers=admin_headers,json={'name':'Receiving 16'})).json()['destinations_requiring_reverification']==0
    assert (await client.patch(M+'/nodes/'+node,headers=admin_headers,json={'city':None})).status_code==422
    assert (await client.patch(M+'/routes/'+route,headers=admin_headers,json={'fee':None})).status_code==422

@pytest.mark.asyncio
async def test_scoped_quote_preview_with_pickup_and_latest_batch(client,admin_headers):
    from app.main import app
    from app.core.db import get_session
    from app.modules.pricing.models import PricePolicy
    from app.modules.offer_engine.models import OfferDetail, CacheState
    from app.modules.suppliers.models import SupplierItem, OfferSnapshot
    from app.modules.marketplace.account_search import account_key
    from app.modules.accounts.models import utc
    from tests.test_catalog_integration import create_product
    from sqlalchemy import select
    from uuid import UUID
    from datetime import timedelta
    brand,product=await create_product(client,admin_headers)
    assert (await client.post('/api/v1/backoffice/products/'+product['id']+'/publish',headers=admin_headers)).status_code==200
    merchant=await new_merchant(client,admin_headers,'Quote Merchant','quote-v016')
    receiving=await new_node(client,admin_headers,merchant,'receiving-v016','warehouse','Самара')
    pickup=await new_node(client,admin_headers,merchant,'pickup-v016','pickup','Самара')
    res=await client.post(M+'/routes',headers=admin_headers,json={
        'source_node_id':receiving,'target_node_id':pickup,'transit_days':2,'fee':'135.00'})
    assert res.status_code==201,res.text
    supp=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,json={
        'code':'rossko','name':'ROSSKO'})).json()['id']
    account,destination=await setup_account(client,admin_headers,supp,merchant,receiving,'QUOTE')
    async for db in app.dependency_overrides[get_session]():
        db.add(PricePolicy(name='default',markup_percent=Decimal('20'),active=True))
        item=SupplierItem(supplier_id=UUID(supp),product_id=UUID(product['id']),
           external_key='CF705-S-1',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed')
        db.add(item);await db.flush()
        snap=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=UUID(account),
            supplier_destination_id=UUID(destination),purchase_price=Decimal('492.27'),
            currency='RUB',quantity=5,lead_time_days=1)
        db.add(snap);await db.flush()
        sid=str(snap.id)
        db.add(OfferDetail(snapshot_id=snap.id,external_token='S1',warehouse='WH-S1',
            min_qty=1,pack_multiple=1,verified_live=True,
            metadata_public={'_search_job_id':'job-v016-current','stock_raw':'>4',
                             'stock_is_lower_bound':True}))
        state=CacheState(supplier_code='rossko',
           lookup_key=account_key('BOSCH','0986479088',UUID(account),UUID(destination)),
           product_id=UUID(product['id']),status='ready',result_count=1,
           last_completed_job_id='job-v016-current',last_requested_at=utc(),
           last_finished_at=utc(),expires_at=utc()+timedelta(minutes=10))
        db.add(state)
        await db.commit()
        break
    url='/api/v1/marketplace/products/'+product['id']+'/offers/preview'
    payload={'snapshot_id':sid,'supplier_account_id':account,
         'supplier_destination_id':destination,'pickup_node_id':pickup,
         'route_node_ids':[receiving,pickup],'quantity':2}
    preview=await client.post(url,json=payload)
    assert preview.status_code==200,preview.text
    data=preview.json()
    assert data['goods_subtotal']=='1181.44'
    assert data['merchant_delivery_fee']=='135.00'
    assert data['known_components_total']=='1316.44'
    assert data['grand_total'] is None and data['estimated_days']==3
    assert data['route_node_ids']==[receiving,pickup]
    assert data['stock_display']=='>4'
    product_html=await client.get('/product/'+product['slug']+'?pickup='+pickup)
    assert product_html.status_code==200,product_html.text[:400]
    assert 'quote-preview-form' in product_html.text
    assert '/static/offer_preview.js?v=016' in product_html.text
    wrong=await client.post(url,json={**payload,'supplier_account_id':str(uuid.uuid4())})
    assert wrong.status_code==409
    too_many=await client.post(url,json={**payload,'quantity':6})
    assert too_many.status_code==409
    no_path=await client.post(url,json={**payload,'route_node_ids':[pickup,receiving]})
    assert no_path.status_code==409
    async for db in app.dependency_overrides[get_session]():
        state=await db.scalar(select(CacheState).where(CacheState.supplier_code=='rossko'))
        state.last_completed_job_id='new-job-removed-warehouse'
        await db.commit()
        break
    gone=await client.post(url,json=payload)
    assert gone.status_code==409,gone.text
