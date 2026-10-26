"""v0.22 editor API integration tests: auth, identity, inheritance and media safety."""
import io
from PIL import Image
import pytest

B = '/api/v1/backoffice'
C = B + '/catalog-v021'
E = B + '/catalog-editor'
M = B + '/master-catalog'


async def brand_product(client, headers):
    brand = await client.post(B + '/brands', headers=headers, json={'name': 'FELIX', 'slug': 'felix'})
    assert brand.status_code == 201, brand.text
    bid = brand.json()['id']
    p = await client.post(B + '/products', headers=headers, json={
        'brand_id': bid, 'article': 'G12/1L', 'name': 'Антифриз 1 л'})
    assert p.status_code == 201, p.text
    return bid, p.json()['id']


@pytest.mark.asyncio
async def test_editor_routes_and_security(client, admin_headers):
    from app.main import app
    from scripts.smoke_v022 import documented_api_routes
    route_map = documented_api_routes(app)
    for method, path in [('GET',E+'/families'),
                         ('GET',E+'/products/{product_id}/identity'),
                         ('PATCH',E+'/products/{product_id}/content'),
                         ('GET',E+'/media/{media_id}/preview'),
                         ('GET',C+'/brands'), ('POST',M+'/families'),
                         ('POST',C+'/media')]:
        assert (method, path) in route_map, path
    # /operator is excluded from OpenAPI and is verified with a live ASGI request.
    for path in [E+'/families', E+'/media?product_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                 E+'/products/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/identity']:
        assert (await client.get(path)).status_code == 403
    pg = await client.get('/operator')
    assert pg.status_code == 200
    assert '/static/catalog_editor.js' in pg.text
    assert 'ce-families' in pg.text
    assert (await client.get('/static/catalog_editor.js')).status_code == 200


@pytest.mark.asyncio
async def test_brand_profile_rename_and_collision(client, admin_headers):
    bid, pid = await brand_product(client, admin_headers)
    assert (await client.get(E+f'/brands/{bid}/profile')).status_code == 403
    empty = (await client.get(E+f'/brands/{bid}/profile', headers=admin_headers)).json()
    assert empty['profile']['verified'] is False
    rename = await client.patch(E+f'/brands/{bid}', headers=admin_headers,json={'name':'FELIX INDUSTRIES'})
    assert rename.status_code == 200,rename.text
    record = (await client.get(E+f'/products/{pid}/identity',headers=admin_headers)).json()
    assert record['brand']=='FELIX INDUSTRIES' and record['brand_id']==bid
    p=await client.post(C+f'/brands/{bid}/aliases',headers=admin_headers,
                        json={'alias':'FELIX PREMIUM','verified':True})
    assert p.status_code==201,p.text
    other=await client.post(B+'/brands',headers=admin_headers,json={'name':'OTHER','slug':'other'})
    assert other.status_code==201,other.text
    r=await client.patch(E+f"/brands/{other.json()['id']}",headers=admin_headers,
                         json={'name':'Felix premium'})
    assert r.status_code==409,r.text


@pytest.mark.asyncio
async def test_category_binding_edit_and_typed_values(client,admin_headers):
    bid,pid=await brand_product(client,admin_headers)
    parent=(await client.post(C+'/categories',headers=admin_headers,
                              json={'name':'Жидкости','slug':'fluids'})).json()['id']
    child=(await client.post(C+'/categories',headers=admin_headers,
                             json={'name':'Антифриз','slug':'antifreeze','parent_id':parent})).json()['id']
    attr=(await client.post(C+'/attributes',headers=admin_headers,json={
        'code':'color_tint','name':'Цвет','data_type':'enum','enum_values':['Red','Blue']})).json()['id']
    bind=await client.post(C+f'/categories/{parent}/attributes',headers=admin_headers,
                           json={'attribute_id':attr,'required':True})
    assert bind.status_code==201,bind.text
    inherited=(await client.get(E+f'/categories/{child}/bindings',headers=admin_headers)).json()['items']
    assert len(inherited)==1 and inherited[0]['required'] and not inherited[0]['direct']
    assert (await client.put(E+f'/categories/{child}/attributes/{attr}',headers=admin_headers,
                             json={'required':False})).status_code==404
    assert (await client.put(E+f'/categories/{parent}/attributes/{attr}',headers=admin_headers,
                             json={'required':False})).status_code==200
    assert (await client.post(C+f'/products/{pid}/categories',headers=admin_headers,
                              json={'category_id':child,'is_primary':True})).status_code==201
    r=await client.put(C+f'/products/{pid}/attributes/{attr}',headers=admin_headers,json={'value':'Red'})
    assert r.status_code==200,r.text
    bad=await client.patch(E+f'/attributes/{attr}',headers=admin_headers,json={
        'name':'Цвет','unit':None,'enum_values':['Blue'],'filterable':False,'active':True})
    assert bad.status_code==409,bad.text
    good=await client.patch(E+f'/attributes/{attr}',headers=admin_headers,json={
        'name':'Цвет антифриза','unit':None,'enum_values':['Red','Blue','Green'],
        'filterable':True,'active':True})
    assert good.status_code==200,good.text
    identity=(await client.get(E+f'/products/{pid}/identity',headers=admin_headers)).json()
    assert identity['attributes'][0]['value']=='Red'


@pytest.mark.asyncio
async def test_product_content_families_and_links(client,admin_headers):
    bid,pid=await brand_product(client,admin_headers)
    new=await client.patch(E+f'/products/{pid}/content',headers=admin_headers,json={
        'name':'Антифриз G12 однолитровый','description':'Для системы охлаждения'})
    assert new.status_code==200,new.text
    identity=(await client.get(E+f'/products/{pid}/identity',headers=admin_headers)).json()
    assert identity['id']==pid and identity['article']=='G12/1L' and identity['status']=='draft'
    family=await client.post(M+'/families',headers=admin_headers,json={
        'name':'Фасовки FELIX G12','slug':'felix-g12-packs','kind':'variant','axes':['volume']})
    assert family.status_code==201,family.text
    fid=family.json()['id']
    list_=await client.get(E+'/families?q=FELIX',headers=admin_headers)
    assert list_.status_code==200 and list_.json()['total']==1
    member=await client.post(M+f'/families/{fid}/members',headers=admin_headers,
                             json={'product_id':pid,'axis_values':{'volume':'1L'}})
    assert member.status_code==201,member.text
    detail=(await client.get(E+f'/families/{fid}',headers=admin_headers)).json()
    assert detail['members'][0]['product_id']==pid
    other=await client.post(B+'/products',headers=admin_headers,json={
        'brand_id':bid,'article':'G12/5L','name':'Антифриз 5 л'})
    assert other.status_code==201,other.text
    oid=other.json()['id']
    relation=await client.post(M+'/relations',headers=admin_headers,json={
        'product_id':pid,'related_product_id':oid,'relation':'related','verified':False})
    assert relation.status_code==201,relation.text
    pack=await client.post(M+'/packages',headers=admin_headers,json={
        'outer_product_id':oid,'inner_product_id':pid,'units_per_package':5})
    assert pack.status_code==201,pack.text
    links=(await client.get(M+f'/products/{pid}/links',headers=admin_headers)).json()
    assert links['relations'][0]['id']
    assert links['factory_packages'][0]['id'] and links['factory_packages'][0]['verified'] is False
    assert (await client.get(E+f'/products/{pid}/sale-terms',headers=admin_headers)).json()['seller_offers']==[]


@pytest.mark.asyncio
async def test_draft_media_preview_is_only_for_admin(client,admin_headers,monkeypatch,tmp_path):
    monkeypatch.setenv('CATALOG_MEDIA_ROOT',str(tmp_path/'images'))
    bid,pid=await brand_product(client,admin_headers)
    img=Image.new('RGB',(160,100),'blue');b=io.BytesIO();img.save(b,'PNG')
    response=await client.post(C+'/media',headers=admin_headers,
        data={'kind':'image','product_id':pid,'position':'0'},files={'upload':('blue.png',b.getvalue(),'image/png')})
    assert response.status_code==201,response.text
    mid=response.json()['id']
    d=(await client.get(E+f'/media?product_id={pid}',headers=admin_headers)).json()
    assert d['items'][0]['status']=='draft'
    assert (await client.get(E+f'/media/{mid}/preview')).status_code==403
    private=await client.get(E+f'/media/{mid}/preview',headers=admin_headers)
    assert private.status_code==200 and private.headers['content-type'].startswith('image/webp')
    assert private.headers['cache-control']=='no-store'
    assert (await client.get(f'/api/v1/catalog-v021/media/{mid}/file')).status_code==404


@pytest.mark.asyncio
async def test_product_offer_stock_separated_from_supplier_snapshots(client,admin_headers):
    from tests.test_operator_workspace_v080 import merchant,store,node
    bid,pid=await brand_product(client,admin_headers)
    assert (await client.post(B+f'/products/{pid}/publish',headers=admin_headers)).status_code==200
    owner=await merchant(client,admin_headers,'Основной продавец','seller-test')
    shop=await store(client,admin_headers,owner)
    warehouse=await node(client,admin_headers,owner,'Склад №1','warehouse','wh-ce-test',shop)
    offer=await client.post(B+'/workspace/offers',headers=admin_headers,json={
        'store_id':shop,'sku':'FELIX-1L','product_id':pid,'price':'430.00'})
    assert offer.status_code==201,offer.text
    soid=offer.json()['id']
    assert (await client.put(B+f'/workspace/offers/{soid}/stock',headers=admin_headers,
                             json={'node_id':warehouse,'on_hand':7})).status_code==200
    supplier=await client.post(B+'/suppliers',headers=admin_headers,json={
        'code':'sample_supplier','name':'Example distributor'})
    assert supplier.status_code==201,supplier.text
    item=await client.post(B+'/supplier-items',headers=admin_headers,json={
        'supplier_id':supplier.json()['id'],'external_key':'G12-1L',
        'raw_brand':'FELIX','raw_article':'G12/1L','product_id':pid})
    assert item.status_code==201,item.text
    snapshot=await client.post(B+'/offer-snapshots',headers=admin_headers,json={
        'supplier_item_id':item.json()['id'],'purchase_price':'250.50','tax_status':'unknown',
        'quantity':50,'lead_time_days':4})
    assert snapshot.status_code==201,snapshot.text
    row=await client.get(E+f'/products/{pid}/sale-terms',headers=admin_headers)
    assert row.status_code==200,row.text
    data=row.json()
    assert data['seller_offers'][0]['stock_by_node'][0]['on_hand']==7
    assert data['seller_offers'][0]['stock_by_node'][0]['available']==7
    assert data['supplier_item_count']==1
    assert data['supplier_offer_snapshots'][0]['quantity']==50
    assert data['supplier_offer_snapshots'][0]['purchase_price']=='250.50'
    assert data['supplier_offer_snapshots'][0]['supplier_code']=='sample_supplier'


def test_openapi_route_listing_does_not_depend_on_flattened_app_routes():
    """Regression: FastAPI >=0.137 puts lazy _IncludedRouter in app.routes."""
    from types import SimpleNamespace
    from scripts.smoke_v022 import documented_api_routes

    class NoIteration:
        def __iter__(self):
            raise AssertionError('Never enumerate app.routes for included routers')

    fake_app = SimpleNamespace(
        routes=NoIteration(),
        openapi=lambda: {'paths': {
            '/api/v1/backoffice/catalog-editor/families': {'get': {}},
            '/api/v1/backoffice/master-catalog/families': {'post': {}}
        }}
    )
    got = documented_api_routes(fake_app)
    assert ('GET', '/api/v1/backoffice/catalog-editor/families') in got
    assert ('POST', '/api/v1/backoffice/master-catalog/families') in got
