"""Master Catalog 0.18: identity, visibility, quantity and conflict regression."""
import uuid
import pytest
from sqlalchemy import select
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Product
from app.modules.marketplace.models import SellerOffer
from app.modules.suppliers.models import SupplierItem
from tests.test_catalog_integration import create_product
from tests.test_checkout_requests_v017 import make_quote, body as quote_body
from tests.test_marketplace_foundation_v07 import new_merchant, new_store, new_user

A='/api/v1/backoffice/master-catalog'
P='/api/v1/master-catalog'


async def items(client,admin,count=4):
    _,base=await create_product(client,admin)
    out=[base]
    for i in range(2,count+1):
        r=await client.post('/api/v1/backoffice/products',headers=admin,json={
            'brand_id':base['brand_id'],'article':f'MC-018-{i}',
            'name':f'G12 0.18 {i} kg'})
        assert r.status_code==201,r.text
        out.append(r.json())
    return out


async def publish(client,admin,product):
    r=await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin)
    assert r.status_code==200,r.text


@pytest.mark.asyncio
async def test_family_variants_different_products_hidden_drafts_conflicts(client,admin_headers):
    it=await items(client,admin_headers)
    assert (await client.post(A+'/families',json={'name':'G12','slug':'g12-demo','axes':['mass_g']})).status_code==403
    created=await client.post(A+'/families',headers=admin_headers,
                              json={'name':'FELIX G12','slug':'felix-g12','axes':['mass_g']})
    assert created.status_code==201,created.text
    fid=created.json()['id']
    for p,mass in zip(it,[1000,3000,5000,10000]):
        resp=await client.post(A+f'/families/{fid}/members',headers=admin_headers,
                               json={'product_id':p['id'],'axis_values':{'mass_g':mass}})
        assert resp.status_code==201,resp.text
    for p in it[:3]:await publish(client,admin_headers,p)
    links=(await client.get(P+f'/products/{it[0]["id"]}/links')).json()
    assert links['family']['axes']==['mass_g']
    assert [m['axis_values']['mass_g'] for m in links['family']['members']]==[1000,3000,5000]
    assert len({m['product_id'] for m in links['family']['members']})==3
    assert (await client.get(P+f'/products/{it[3]["id"]}/links')).status_code==404
    listed=(await client.get(A+f'/products/{it[0]["id"]}/links',headers=admin_headers)).json()
    assert len(listed['family']['members'])==4
    assert (await client.post(A+f'/families/{fid}/members',headers=admin_headers,
                              json={'product_id':it[0]['id'],'axis_values':{'mass_g':1000}})).status_code==409
    assert (await client.post(A+f'/families/{fid}/members',headers=admin_headers,
                              json={'product_id':it[0]['id'],'axis_values':{'different_key':1000}})).status_code==422
    assert (await client.post(A+'/families',headers=admin_headers,
                              json={'name':'Same axes','slug':'other-line','axes':['mass_g','mass_g']})).status_code==422
    assert (await client.delete(A+f'/families/{fid}/members/{it[3]["id"]}',headers=admin_headers)).status_code==200
    links=(await client.get(P+f'/products/{it[0]["id"]}/links')).json()
    assert len(links['family']['members'])==3


@pytest.mark.asyncio
async def test_factory_kit_component_links_unlisted_and_assembly_block(client,admin_headers):
    it=await items(client,admin_headers)
    for p in it[:3]:await publish(client,admin_headers,p)
    head=it[0]['id']; belt=it[1]['id']; plan=it[3]['id']
    c=await client.put(A+f'/products/{head}/composition',headers=admin_headers,
                       json={'kind':'factory_kit','verified':True})
    assert c.status_code==200,c.text
    linked=await client.post(A+f'/products/{head}/components',headers=admin_headers,json={
        'position':1,'component_product_id':belt,'name':'Ремень ГРМ','quantity':'1',
        'sale_status':'independent'})
    assert linked.status_code==201,linked.text
    unlisted=await client.post(A+f'/products/{head}/components',headers=admin_headers,json={
        'position':2,'name':'Помпа Gates (не продаётся отдельно)','quantity':'1',
        'sale_status':'not_listed'})
    assert unlisted.status_code==201,unlisted.text
    assert (await client.post(A+f'/products/{head}/components',headers=admin_headers,json={
        'position':3,'component_product_id':head,'name':'Петля','quantity':'1'})).status_code==409
    read=(await client.get(P+f'/products/{head}/links')).json()['composition']
    assert read['inventory_mode']=='finished_product_stock'
    assert len(read['parts'])==2
    assert read['parts'][0]['component_product_id']==belt
    assert read['parts'][1]['component_product_id'] is None
    assert read['parts'][1]['sale_status']=='not_listed'
    assert (await client.put(A+f'/products/{head}/composition',headers=admin_headers,
                             json={'kind':'assembled_plan'})).status_code==409
    assert (await client.put(A+f'/products/{plan}/composition',headers=admin_headers,
                             json={'kind':'assembled_plan','verified':True})).status_code==200
    assert (await client.post(f'/api/v1/backoffice/products/{plan}/publish',headers=admin_headers)).status_code==409
    # Prove transitive component cycles are rejected.
    assert (await client.put(A+f'/products/{belt}/composition',headers=admin_headers,
                             json={'kind':'factory_kit'})).status_code==200
    assert (await client.post(A+f'/products/{belt}/components',headers=admin_headers,
                              json={'position':1,'component_product_id':head,'name':'Цикл','quantity':'1'})).status_code==409
    assert (await client.delete(A+f'/products/{head}/components/{linked.json()["id"]}',headers=admin_headers)).status_code==200


@pytest.mark.asyncio
async def test_factory_pack_has_distinct_uuid_one_parent_and_no_cycle(client,admin_headers):
    it=await items(client,admin_headers)
    for p in it[:3]:await publish(client,admin_headers,p)
    base,box,case=(p['id'] for p in it[:3])
    raw={'outer_product_id':box,'inner_product_id':base,'units_per_package':4,'verified':True}
    assert (await client.post(A+'/packages',json=raw)).status_code==403
    first=await client.post(A+'/packages',headers=admin_headers,json=raw)
    assert first.status_code==201,first.text
    result=(await client.get(P+f'/products/{base}/links')).json()
    assert result['factory_packages'][0]['outer_product_id']==box
    assert result['factory_packages'][0]['units_per_package']==4
    assert result['factory_packages'][0]['role']=='inner'
    assert (await client.post(A+'/packages',headers=admin_headers,
                              json={**raw,'inner_product_id':case})).status_code==409
    second=await client.post(A+'/packages',headers=admin_headers,
                             json={'outer_product_id':case,'inner_product_id':box,'units_per_package':6})
    assert second.status_code==201,second.text
    assert (await client.post(A+'/packages',headers=admin_headers,
                              json={'outer_product_id':base,'inner_product_id':case,'units_per_package':2})).status_code==409
    assert (await client.post(A+'/packages',headers=admin_headers,
                              json={'outer_product_id':base,'inner_product_id':base,'units_per_package':4})).status_code==409
    assert (await client.post(A+'/packages',headers=admin_headers,
                              json={'outer_product_id':base,'inner_product_id':box,'units_per_package':1})).status_code==422
    assert (await client.patch(A+'/packages/'+first.json()['id'],headers=admin_headers,json={'verified':False})).status_code==200
    assert (await client.get(P+f'/products/{base}/links')).json()['factory_packages']==[]
    assert (await client.delete(A+'/packages/'+first.json()['id'],headers=admin_headers)).status_code==200


@pytest.mark.asyncio
async def test_directed_replacements_are_not_automatically_reciprocal(client,admin_headers):
    it=await items(client,admin_headers,2)
    for p in it:await publish(client,admin_headers,p)
    resp=await client.post(A+'/relations',headers=admin_headers,json={
        'product_id':it[0]['id'],'related_product_id':it[1]['id'],
        'relation':'supersedes','verified':True})
    assert resp.status_code==201,resp.text
    one=(await client.get(P+f'/products/{it[0]["id"]}/links')).json()
    two=(await client.get(P+f'/products/{it[1]["id"]}/links')).json()
    assert one['relations'][0]['relation']=='supersedes' and not two['relations']
    rid=resp.json()['id']
    assert (await client.patch(A+'/relations/'+rid,headers=admin_headers,json={'verified':False})).status_code==200
    assert (await client.get(P+f'/products/{it[0]["id"]}/links')).json()['relations']==[]
    assert (await client.delete(A+'/relations/'+rid,headers=admin_headers)).status_code==200


@pytest.mark.asyncio
async def test_supplier_warehouse_terms_tighten_feed_rules_and_prevent_mismatched_scope(client,admin_headers):
    q=await make_quote(client,admin_headers)
    async for db in app.dependency_overrides[get_session]():
        snap=await db.get(__import__('app.modules.suppliers.models',fromlist=['OfferSnapshot']).OfferSnapshot,
                          uuid.UUID(q['snapshots'][0]))
        item_id=str(snap.supplier_item_id)
        break
    term={'supplier_item_id':item_id,'supplier_account_id':q['account'],
          'supplier_destination_id':q['destination'],'warehouse_id':'WH-1',
          'minimum_units':2,'step_units':2,'units_per_package':2,
          'order_unit':'piece','status':'draft'}
    endpoint=A+'/sale-terms/supplier'
    assert (await client.put(endpoint,json=term)).status_code==403
    draft=await client.put(endpoint,headers=admin_headers,json=term)
    assert draft.status_code==200,draft.text
    u='/api/v1/marketplace/products/'+q['product']['id']+'/offers/preview'
    first=await client.post(u,json={k:v for k,v in quote_body(q,0,1).items() if k!='product_id'})
    assert first.status_code==200,first.text  # draft has no effect
    live=await client.put(endpoint,headers=admin_headers,json={**term,'status':'active'})
    assert live.status_code==200,live.text
    one=await client.post(u,json={k:v for k,v in quote_body(q,0,1).items() if k!='product_id'})
    assert one.status_code==409,one.text
    two=await client.post(u,json={k:v for k,v in quote_body(q,0,2).items() if k!='product_id'})
    assert two.status_code==200,two.text
    assert two.json()['effective_pack_multiple']==2
    assert two.json()['units_per_package']==2
    # Only warehouse WH-1 has the rule; WH-2 remains piece-sellable.
    other=await client.post(u,json={k:v for k,v in quote_body(q,1,1).items() if k!='product_id'})
    assert other.status_code==200,other.text
    mismatch=await client.put(endpoint,headers=admin_headers,
                              json={**term,'supplier_account_id':str(uuid.uuid4())})
    assert mismatch.status_code==404 or mismatch.status_code==409
    pack=await client.put(endpoint,headers=admin_headers,
                          json={**term,'order_unit':'pack','status':'active'})
    assert pack.status_code==422
    # New tighter requirements must never loosen provider-defined min / step.
    async for db in app.dependency_overrides[get_session]():
        from app.modules.offer_engine.models import OfferDetail
        detail=await db.get(OfferDetail,uuid.UUID(q['snapshots'][0]));detail.min_qty=4;detail.pack_multiple=4
        await db.commit()
        break
    check=await client.post(u,json={k:v for k,v in quote_body(q,0,2).items() if k!='product_id'})
    assert check.status_code==409
    four=await client.post(u,json={k:v for k,v in quote_body(q,0,4).items() if k!='product_id'})
    assert four.status_code==200 and four.json()['effective_pack_multiple']==4


@pytest.mark.asyncio
async def test_seller_lot_terms_apply_to_cart_and_own_stock_request(client,admin_headers):
    it=await items(client,admin_headers,1)
    await publish(client,admin_headers,it[0])
    m=await new_merchant(client,admin_headers,'Lot seller','lotseller')
    store=await new_store(client,admin_headers,m,'lotseller-store')
    uid,auth=await new_user(client,'lotseller@example.org')
    await client.post('/api/v1/admin/marketplace/memberships',headers=admin_headers,
                      json={'store_id':store,'user_id':uid,'role':'owner'})
    async def node(code,kind):
        response=await client.post('/api/v1/admin/marketplace/nodes',headers=admin_headers,
            json={'merchant_id':m,'store_id':store,'code':code,'name':code,
                  'kind':kind,'city':'Самара','address':'Самара, пример 12'})
        assert response.status_code==201,response.text
        return response.json()['id']
    wh=await node('lot-warehouse','warehouse');pk=await node('lot-pickup','pickup')
    assert (await client.post('/api/v1/admin/marketplace/routes',headers=admin_headers,
                              json={'source_node_id':wh,'target_node_id':pk,'transit_days':1,'fee':'20.00'})).status_code==201
    created=await client.post('/api/v1/seller/offers',headers=auth,json={
        'store_id':store,'product_id':it[0]['id'],'sku':'lot-candle','source_kind':'own_stock','price':'100.00'})
    assert created.status_code==201,created.text
    offer=created.json()['id']
    await client.patch('/api/v1/seller/offers/'+offer,headers=auth,json={'status':'active'})
    assert (await client.put('/api/v1/seller/offers/'+offer+'/stock',headers=auth,json={
        'node_id':wh,'on_hand':16})).status_code==200
    url=A+'/sale-terms/seller/'+offer
    rules={'minimum_units':4,'step_units':4,'units_per_package':4,'order_unit':'piece','status':'active'}
    assert (await client.put(url,json=rules)).status_code==403
    assert (await client.put(url,headers=admin_headers,json=rules)).status_code==200
    post='/api/v1/marketplace/cart/items'
    async def put_qty(qty):
        return await client.post(post,headers=auth,
                                 json={'seller_offer_id':offer,'pickup_node_id':pk,'quantity':qty})
    assert (await put_qty(1)).status_code==409
    assert (await put_qty(5)).status_code==409
    four=await put_qty(4)
    assert four.status_code==201,four.text
    cart=await client.get('/api/v1/marketplace/cart/v017/preview',headers=auth)
    assert cart.status_code==200 and cart.json()['ready_for_request']
    # Change the required step AFTER item is saved: v0.17 must not submit stale terms.
    assert (await client.put(url,headers=admin_headers,json={**rules,'step_units':8,'units_per_package':4})).status_code==200
    report=(await client.get('/api/v1/marketplace/cart/v017/preview',headers=auth)).json()
    assert not report['ready_for_request']
    assert 'Sale terms' in report['items'][0]['reason']

@pytest.mark.asyncio
async def test_catalog_structure_frozen_in_operator_request_snapshot(client,admin_headers):
    from tests.test_checkout_requests_v017 import request
    q=await make_quote(client,admin_headers)
    product_id=q['product']['id']
    family=(await client.post(A+'/families',headers=admin_headers,json={
        'name':'Service variant family','slug':'service-variant-family','axes':['capacity_g']})).json()['id']
    assert (await client.post(A+f'/families/{family}/members',headers=admin_headers,json={
        'product_id':product_id,'axis_values':{'capacity_g':5000}})).status_code==201
    _,auth=await new_user(client,'snapshot-test-v18@example.org')
    added=await client.post('/api/v1/marketplace/cart/quotes',headers=auth,json=quote_body(q,0,1))
    assert added.status_code==201,added.text
    preview=(await client.get('/api/v1/marketplace/cart/v017/preview',headers=auth)).json()
    order=await client.post('/api/v1/marketplace/cart/v017/submit',headers={**auth,'Idempotency-Key':'v18-master-snapshot-00001'},json=request(preview['revision']))
    assert order.status_code==201,order.text
    oid=order.json()['order_id']
    detail=(await client.get('/api/v1/marketplace/cart/v017/orders/'+oid,headers=auth)).json()
    config=detail['lines'][0]['selection_snapshot']['master_catalog_v018']
    assert config['family_at_request']['id']==family
    assert config['family_at_request']['members'][0]['axis_values']=={'capacity_g':5000}
    # Future catalog unlink cannot rewrite an old customer order snapshot.
    removed=await client.delete(A+f'/families/{family}/members/{product_id}',headers=admin_headers)
    assert removed.status_code==200
    again=(await client.get('/api/v1/marketplace/cart/v017/orders/'+oid,headers=auth)).json()
    assert again['lines'][0]['selection_snapshot']['master_catalog_v018']==config


def test_migration_0007_round_trip_on_disposable_sqlite():
    """Apply actual migration on previous baseline tables, then downgrade."""
    from importlib import import_module
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.pool import StaticPool
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.core.db import Base
    revision=import_module('migrations.versions.20261019_0007_master_catalog_v018')
    new_tables={'product_families','product_family_members','product_compositions',
                'product_component_lines','factory_packages','offer_sale_terms'}
    engine=create_engine('sqlite+pysqlite:///:memory:',poolclass=StaticPool)
    try:
        Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables])
        with engine.connect() as connection:
            ctx=MigrationContext.configure(connection)
            with Operations.context(ctx):
                revision.upgrade()
            assert new_tables.issubset(inspect(connection).get_table_names())
            with Operations.context(ctx):
                revision.downgrade()
            assert not new_tables.intersection(inspect(connection).get_table_names())
    finally:
        engine.dispose()
