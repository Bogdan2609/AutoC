"""Nonbinding supplier quote requests; zero provider calls, payments or purchases."""
import uuid
from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.accounts.models import utc
from app.modules.offer_engine.models import CacheState, OfferDetail
from app.modules.suppliers.models import SupplierItem, OfferSnapshot
from app.modules.pricing.models import PricePolicy
from app.modules.marketplace.account_search import account_key
from app.modules.marketplace.models import InventoryBalance, InventoryMovement, SellerOrder
from app.modules.commerce.models import CartLine, Order, OrderLine, Procurement
from app.modules.payments.models import Payment
from tests.test_catalog_integration import create_product
from tests.test_marketplace_foundation_v07 import new_merchant,new_store,new_user
from tests.test_v014_integration import new_node
from tests.test_supplier_offers_v015 import setup_account

M='/api/v1/admin/marketplace'
C='/api/v1/marketplace/cart'

async def make_quote(client,admin):
    brand,p=await create_product(client,admin)
    assert (await client.post('/api/v1/backoffice/products/'+p['id']+'/publish',headers=admin)).status_code==200
    merchant=await new_merchant(client,admin,'V17 Vendor','v17-vendor')
    receiving=await new_node(client,admin,merchant,'v17-in','warehouse','Самара')
    pickup=await new_node(client,admin,merchant,'v17-out','pickup','Самара')
    resp=await client.post(M+'/routes',headers=admin,json={'source_node_id':receiving,'target_node_id':pickup,'transit_days':2,'fee':'95.00'})
    assert resp.status_code==201,resp.text
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin,json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account,dest=await setup_account(client,admin,supplier,merchant,receiving,'V17')
    snaps=[]
    async for db in app.dependency_overrides[get_session]():
        db.add(PricePolicy(name='default',markup_percent=Decimal('20'),active=True))
        item=SupplierItem(supplier_id=uuid.UUID(supplier),product_id=uuid.UUID(p['id']),
            external_key='V17-ITEM',raw_brand='BOSCH',raw_article='0986479088',match_status='confirmed')
        db.add(item);await db.flush()
        # Two warehouse snapshots from the very same SupplierItem: legacy unique
        # (cart_id,supplier_item_id) must not collapse them into one cart line.
        for ix in (1,2):
            snap=OfferSnapshot(supplier_item_id=item.id,supplier_account_id=uuid.UUID(account),
                supplier_destination_id=uuid.UUID(dest),purchase_price=Decimal(str(500+ix)),
                currency='RUB',quantity=4,lead_time_days=1)
            db.add(snap);await db.flush()
            snaps.append(str(snap.id))
            db.add(OfferDetail(snapshot_id=snap.id,external_token=f'TOKEN{ix}',warehouse=f'WH-{ix}',
                verified_live=True,min_qty=1,pack_multiple=1,
                metadata_public={'_search_job_id':'job-v17'}))
        db.add(CacheState(supplier_code='rossko',lookup_key=account_key('BOSCH','0986479088',uuid.UUID(account),uuid.UUID(dest)),
            product_id=uuid.UUID(p['id']),status='ready',result_count=2,last_completed_job_id='job-v17',
            last_requested_at=utc(),last_finished_at=utc(),expires_at=utc()+timedelta(minutes=10)))
        await db.commit()
        break
    return {'product':p,'merchant':merchant,'receiving':receiving,'pickup':pickup,
            'account':account,'destination':dest,'snapshots':snaps}


def body(q,ix=0,quantity=1):
    return {'product_id':q['product']['id'],'snapshot_id':q['snapshots'][ix],
            'supplier_account_id':q['account'],'supplier_destination_id':q['destination'],
            'pickup_node_id':q['pickup'],'route_node_ids':[q['receiving'],q['pickup']],
            'quantity':quantity}


def request(revision):
    return {'expected_revision':revision,'contact':'Test Customer','phone':'79001234567',
            'acknowledge_estimate':True}


@pytest.mark.asyncio
async def test_multi_warehouse_quote_cart_and_idempotent_nonbinding_request(client,admin_headers):
    q=await make_quote(client,admin_headers)
    user,auth=await new_user(client,'buyer-v17@example.com')
    for ix in (0,1):
        resp=await client.post(C+'/quotes',headers=auth,json=body(q,ix))
        assert resp.status_code==201,resp.text
        assert not resp.json()['payment_created']
    preview=await client.get(C+'/v017/preview',headers=auth)
    assert preview.status_code==200,preview.text
    v=preview.json()
    assert v['revision']==2 and v['item_count']==2 and v['ready_for_request']
    assert v['grand_total'] is None and not v['final_price_complete']
    assert {x['snapshot_id'] for x in v['items']}==set(q['snapshots'])
    assert all(x['supplier_fee_known'] is False for x in v['items'])
    key='v17-unique-request-000001'
    endpoint=C+'/v017/submit'
    hdr={**auth,'Idempotency-Key':key}
    submitted=await client.post(endpoint,headers=hdr,json=request(v['revision']))
    assert submitted.status_code==201,submitted.text
    data=submitted.json()
    assert data['status']=='request_awaiting_delivery_quote' and data['line_count']==2
    assert not data['payment_created'] and not data['supplier_purchase_created']
    again=await client.post(endpoint,headers=hdr,json=request(v['revision']))
    assert again.status_code==201 and again.json()['reused']
    assert again.json()['order_id']==data['order_id']
    assert (await client.post(endpoint,headers=hdr,json={**request(v['revision']),'phone':'79998887766'})).status_code==409
    assert (await client.post(endpoint,headers={**auth,'Idempotency-Key':'different-v17-key'},json=request(v['revision']))).status_code==409
    detail=await client.get(C+'/v017/orders/'+data['order_id'],headers=auth)
    assert detail.status_code==200 and len(detail.json()['lines'])==2
    for line in detail.json()['lines']:
        s=line['selection_snapshot']
        assert s['brand']=='BOSCH' and s['article'].replace(' ','')=='0986479088' and s['product_id']==q['product']['id']
        assert s['supplier_account_id']==q['account'] and s['pickup_node_id']==q['pickup']
        assert s['commercial_snapshot_version']==1
    _,intruder=await new_user(client,'intruder-v17@example.com')
    assert (await client.get(C+'/v017/orders/'+data['order_id'],headers=intruder)).status_code==404
    assert (await client.post(C+'/v017/orders/'+data['order_id']+'/cancel',headers=intruder)).status_code==404
    assert (await client.get(M+'/order-requests')).status_code==403
    listed=await client.get(M+'/order-requests',headers=admin_headers)
    assert listed.status_code==200 and any(x['order_id']==data['order_id'] for x in listed.json())
    async for db in app.dependency_overrides[get_session]():
        rows=(await db.scalars(select(CartLine).where(CartLine.line_kind=='supplier_quote'))).all()
        assert len(rows)==2 and all(x.supplier_item_id is None for x in rows)
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
        assert (await db.scalar(select(func.count()).select_from(SellerOrder)))==1
        assert (await db.scalar(select(func.count()).select_from(Order)))==1
    cancelled=await client.post(C+'/v017/orders/'+data['order_id']+'/cancel',headers=auth)
    assert cancelled.status_code==200 and cancelled.json()['status']=='request_cancelled'
    repeat=await client.post(C+'/v017/orders/'+data['order_id']+'/cancel',headers=auth)
    assert repeat.status_code==200 and repeat.json()['reused']


@pytest.mark.asyncio
async def test_quote_cart_rejects_stale_identity_and_price_requires_explicit_reconfirmation(client,admin_headers):
    q=await make_quote(client,admin_headers)
    _,auth=await new_user(client,'buyer-changes-v17@example.com')
    assert (await client.post(C+'/quotes',headers=auth,json={**body(q),'quantity':5})).status_code==409
    assert (await client.post(C+'/quotes',headers=auth,json={**body(q),'supplier_account_id':str(uuid.uuid4())})).status_code==409
    assert (await client.post(C+'/quotes',headers=auth,json={**body(q),'route_node_ids':[q['pickup'],q['receiving']]})).status_code==409
    added=await client.post(C+'/quotes',headers=auth,json=body(q))
    assert added.status_code==201,added.text
    preview=(await client.get(C+'/v017/preview',headers=auth)).json()
    assert preview['ready_for_request']
    # Retail markup changed after customer added the quote.
    async for db in app.dependency_overrides[get_session]():
        pol=await db.scalar(select(PricePolicy))
        pol.markup_percent=Decimal('25.0')
        await db.commit()
        break
    preview=(await client.get(C+'/v017/preview',headers=auth)).json()
    assert not preview['ready_for_request'] and 'Price' in preview['items'][0]['reason']
    post=await client.post(C+'/v017/submit',headers={**auth,'Idempotency-Key':'pricechange-v17-00001'},json=request(preview['revision']))
    assert post.status_code==409
    corrected=await client.patch(C+'/v017/items/'+added.json()['line_id'],headers=auth,json={'quantity':2})
    assert corrected.status_code==200,corrected.text
    assert corrected.json()['accepted_unit_price']!=str(added.json()['known_total'])
    preview=(await client.get(C+'/v017/preview',headers=auth)).json()
    assert preview['ready_for_request']
    # Latest successful batch dropped the warehouse after confirmation.
    async for db in app.dependency_overrides[get_session]():
        state=await db.scalar(select(CacheState).where(CacheState.supplier_code=='rossko'))
        state.last_completed_job_id='new-job-with-no-warehouse'
        await db.commit()
        break
    preview=(await client.get(C+'/v017/preview',headers=auth)).json()
    assert not preview['ready_for_request']
    assert (await client.post(C+'/v017/submit',headers={**auth,'Idempotency-Key':'stale-v17-key-000001'},json=request(preview['revision']))).status_code==409
    assert (await client.patch(C+'/v017/items/'+added.json()['line_id'],headers=auth,json={'quantity':1})).status_code==409
    async for db in app.dependency_overrides[get_session]():
        assert (await db.scalar(select(func.count()).select_from(Order)))==0
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0


@pytest.mark.asyncio
async def test_own_stock_request_does_not_reserve_or_trigger_payment(client,admin_headers):
    brand,p=await create_product(client,admin_headers)
    assert (await client.post('/api/v1/backoffice/products/'+p['id']+'/publish',headers=admin_headers)).status_code==200
    merchant=await new_merchant(client,admin_headers,'Seller Own','seller-v17-own')
    store=await new_store(client,admin_headers,merchant,'own-v17')
    seller_id,auth_seller=await new_user(client,'seller-v17@example.com')
    _,auth_buyer=await new_user(client,'buyer-own-v17@example.com')
    await client.post(M+'/memberships',headers=admin_headers,json={'store_id':store,'user_id':seller_id,'role':'owner'})
    node=await new_node(client,admin_headers,merchant,'own-stock-v17','warehouse_pickup','Самара')
    offer=(await client.post('/api/v1/seller/offers',headers=auth_seller,json={
        'store_id':store,'product_id':p['id'],'sku':'TEST-OWN','price':'799.00'})).json()['id']
    assert (await client.patch('/api/v1/seller/offers/'+offer,headers=auth_seller,json={'status':'active'})).status_code==200
    assert (await client.put('/api/v1/seller/offers/'+offer+'/stock',headers=auth_seller,json={'node_id':node,'on_hand':3})).status_code==200
    added=await client.post(C+'/items',headers=auth_buyer,json={'seller_offer_id':offer,'pickup_node_id':node,'quantity':2})
    assert added.status_code==201,added.text
    preview=(await client.get(C+'/v017/preview',headers=auth_buyer)).json()
    assert preview['ready_for_request'] and preview['known_components_total']=='1598.00'
    assert (await client.post(C+'/v017/submit',headers={**auth_buyer,'Idempotency-Key':'order-own-v17-00001'},json={**request(preview['revision']),'acknowledge_estimate':False})).status_code==422
    submitted=await client.post(C+'/v017/submit',headers={**auth_buyer,'Idempotency-Key':'order-own-v17-00001'},json=request(preview['revision']))
    assert submitted.status_code==201,submitted.text
    order_id=submitted.json()['order_id']
    assert submitted.json()['status']=='request_awaiting_operator_review'
    # A nonbinding request cannot block inventory or create payment obligations.
    async for db in app.dependency_overrides[get_session]():
        b=await db.scalar(select(InventoryBalance).where(InventoryBalance.seller_offer_id==uuid.UUID(offer)))
        assert b.on_hand==3 and b.reserved==0
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
    cancelled=await client.post(C+'/v017/orders/'+order_id+'/cancel',headers=auth_buyer)
    assert cancelled.status_code==200,cancelled.text
    assert (await client.post(C+'/v017/orders/'+order_id+'/cancel',headers=auth_buyer)).json()['reused']
    async for db in app.dependency_overrides[get_session]():
        b=await db.scalar(select(InventoryBalance).where(InventoryBalance.seller_offer_id==uuid.UUID(offer)))
        assert b.reserved==0 and b.on_hand==3
        records=(await db.scalars(select(InventoryMovement))).all()
        assert not any(x.delta_reserved for x in records)  # no reservation to release


@pytest.mark.asyncio
async def test_quote_workflow_rejects_unauthenticated_and_invalid_requests(client):
    assert (await client.get(C+'/v017/preview')).status_code==401
    assert (await client.post(C+'/v017/submit',json=request(0),headers={'Idempotency-Key':'v17-test-key'})).status_code==401
    assert (await client.get(M+'/order-requests')).status_code==403

@pytest.mark.asyncio
async def test_mixed_two_merchants_and_expiring_own_reservation(client,admin_headers):
    q=await make_quote(client,admin_headers)
    buyer_id,buyer=await new_user(client,'mixed-buyer-v17@example.com')
    owner_id,owner=await new_user(client,'mixed-owner-v17@example.com')
    own_merchant=await new_merchant(client,admin_headers,'Separate Seller','mixed-seller-v17')
    store=await new_store(client,admin_headers,own_merchant,'mixed-store-v17')
    await client.post(M+'/memberships',headers=admin_headers,json={
        'store_id':store,'user_id':owner_id,'role':'owner'})
    node=await new_node(client,admin_headers,own_merchant,'mixed-own-node','warehouse_pickup','Самара')
    product=q['product']['id']
    offer=(await client.post('/api/v1/seller/offers',headers=owner,json={
        'store_id':store,'product_id':product,'sku':'MIXED-SKU','price':'111.11'})).json()['id']
    await client.patch('/api/v1/seller/offers/'+offer,headers=owner,json={'status':'active'})
    await client.put('/api/v1/seller/offers/'+offer+'/stock',headers=owner,json={'node_id':node,'on_hand':1})
    assert (await client.post(C+'/items',headers=buyer,json={
        'seller_offer_id':offer,'pickup_node_id':node,'quantity':1})).status_code==201
    assert (await client.post(C+'/quotes',headers=buyer,json=body(q))).status_code==201
    view=(await client.get(C+'/v017/preview',headers=buyer)).json()
    assert view['ready_for_request'] and view['item_count']==2
    sent=await client.post(C+'/v017/submit',headers={**buyer,'Idempotency-Key':'mixed-order-v17-00001'},json=request(view['revision']))
    assert sent.status_code==201,sent.text
    order_id=sent.json()['order_id']
    assert sent.json()['seller_orders']==2
    assert sent.json()['status']=='request_awaiting_delivery_quote'
    from app.modules.commerce.models import Job
    from app.modules.marketplace.checkout_v017 import expire_reservation
    async for db in app.dependency_overrides[get_session]():
        o=await db.get(Order,uuid.UUID(order_id))
        assert o.request_expires_at is None
        assert (await db.scalar(select(Job).where(Job.kind=='marketplace_reservation_expire'))) is None
        bal=await db.scalar(select(InventoryBalance).where(InventoryBalance.seller_offer_id==uuid.UUID(offer)))
        assert bal.reserved==0 and bal.on_hand==1
        # A legacy reservation from v0.17 remains releasable after deploying the new code.
        from app.modules.marketplace.service import reserve_stock
        old_line=await db.scalar(select(OrderLine).where(OrderLine.order_id==o.id,OrderLine.seller_offer_id==uuid.UUID(offer)))
        reserve_key=f'v017-reserve:{o.id}:migrated'
        await reserve_stock(db,bal.id,1,reserve_key)
        old_line.commercial_snapshot={**old_line.commercial_snapshot,
            'reservation_key':reserve_key,'stock_reserved':True}
        o.request_expires_at=utc()-timedelta(seconds=1)
        await db.commit()
        await expire_reservation(db,uuid.UUID(order_id))
        await db.commit()
        assert (await db.get(Order,uuid.UUID(order_id))).status=='request_expired'
        bal=await db.scalar(select(InventoryBalance).where(InventoryBalance.seller_offer_id==uuid.UUID(offer)))
        assert bal.reserved==0
        await expire_reservation(db,uuid.UUID(order_id))
        await db.commit()
        releases=(await db.scalars(select(InventoryMovement).where(
            InventoryMovement.reason=='v017_request_expired'))).all()
        assert len(releases)==1
    repeated=await client.post(C+'/v017/submit',headers={**buyer,'Idempotency-Key':'mixed-order-v17-00001'},json=request(view['revision']))
    assert repeated.status_code==201 and repeated.json()['reused']
    assert repeated.json()['status']=='request_expired'



@pytest.mark.asyncio
async def test_change_of_merchant_route_fee_requires_buyer_acknowledgement(client,admin_headers):
    q=await make_quote(client,admin_headers)
    _,buyer=await new_user(client,'route-buyer-v17@example.com')
    added=await client.post(C+'/quotes',headers=buyer,json=body(q))
    assert added.status_code==201
    async for db in app.dependency_overrides[get_session]():
        from app.modules.marketplace.models import DeliveryRoute
        route=await db.scalar(select(DeliveryRoute))
        route.fee=Decimal('125.00')
        await db.commit()
    view=(await client.get(C+'/v017/preview',headers=buyer)).json()
    assert view['ready_for_request'] is False and 'tariff' in view['items'][0]['reason']
    assert (await client.post(C+'/v017/submit',headers={**buyer,'Idempotency-Key':'route-v17-00001'},json=request(view['revision']))).status_code==409
    confirm=await client.patch(C+'/v017/items/'+added.json()['line_id'],headers=buyer,json={'quantity':1})
    assert confirm.status_code==200 and confirm.json()['accepted_merchant_delivery_fee']=='125.00'
    view=(await client.get(C+'/v017/preview',headers=buyer)).json()
    assert view['ready_for_request'] is True
    assert (await client.post(C+'/v017/submit',headers={**buyer,'Idempotency-Key':'route-v17-00001'},json=request(view['revision']))).status_code==201
