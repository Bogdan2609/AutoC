"""v0.23 R4 attribute editing and destructive action safety."""
import pytest
from app.modules.catalog_ext.catalog_api import admin as _legacy

A='/api/v1/backoffice/catalog-v021'
E='/api/v1/backoffice/catalog-editor'
P='/api/v1/catalog-v021'


async def create_category(client,h,slug):
    r=await client.post(A+'/categories',headers=h,json={'name':'Тестовый раздел','slug':slug})
    assert r.status_code==201,r.text
    return r.json()['id']


async def create_product(client,h,cat):
    b=await client.post('/api/v1/backoffice/brands',headers=h,json={'name':'R4TEST','slug':'r4test'})
    assert b.status_code==201,b.text
    pid=(await client.post('/api/v1/backoffice/products',headers=h,json={
        'brand_id':b.json()['id'],'article':'MODAL TEST','name':'Тест модальной формы'}))
    assert pid.status_code==201,pid.text
    p=pid.json()['id']
    assign=await client.post(f'{A}/products/{p}/categories',headers=h,json={'category_id':cat,'is_primary':True})
    assert assign.status_code==201,assign.text
    return p


@pytest.mark.asyncio
async def test_category_inline_create_code_rename_old_filter_works(client,admin_headers):
    h=admin_headers
    c=await create_category(client,h,'attr-admin-v4')
    p=await create_product(client,h,c)
    endpoint=f'{E}/categories/{c}/attribute-definition'
    created=await client.post(endpoint,headers=h,json={
        'code':'r4_kg','name':'Вес фасовки','data_type':'integer','filterable':True,
        'active':True,'required':False,'show_in_filters':True,'unit':'kg'})
    assert created.status_code==201,created.text
    aid=created.json()['definition']['id']
    assert (await client.put(f'{A}/products/{p}/attributes/{aid}',headers=h,json={'value':5})).status_code==200
    assert (await client.post('/api/v1/backoffice/products/'+p+'/publish',headers=h)).status_code==200
    public=await client.get(f'{P}/products',params={'category_id':c,'attr':['r4_kg:5']})
    assert public.status_code==200,public.text
    info=await client.get(f'{E}/attributes/{aid}/usage',headers=h)
    assert info.status_code==200
    assert info.json()['product_values']==1 and info.json()['category_bindings']==1
    assert (await client.get(f'{E}/attributes/{aid}/usage')).status_code==403

    schema={'code':'r4_pack_kg','name':'Масса фасовки','data_type':'decimal','unit':'kg',
            'enum_values':None,'filterable':True,'active':True,
            'expected_code':'r4_kg','expected_data_type':'integer','confirm_type_change':True}
    result=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=schema)
    assert result.status_code==200,result.text
    assert result.json()['converted_products']==1
    assert result.json()['previous_code_reserved']=='r4_kg'
    values=await client.get(f'{E}/products/{p}/identity',headers=h)
    assert values.status_code==200
    one=next(x for x in values.json()['attributes'] if x['attribute_id']==aid)
    assert one['data_type']=='decimal' and one['value']=='5.000000'
    for code in ('r4_pack_kg','r4_kg'):
        old=await client.get(f'{P}/products',params={'category_id':c,'attr':code+':5'})
        assert old.status_code==200,(code,old.text)
        assert any(x['id']==p for x in old.json()['items']),(code,old.json())
    assert (await client.post(A+'/attributes',headers=h,json={
        'code':'r4_kg','name':'Old code','data_type':'integer'})).status_code==409
    assert (await client.delete(f'{E}/attributes/{aid}',headers=h)).status_code==409
    assert (await client.delete(f'{A}/products/{p}/attributes/{aid}',headers=h)).status_code==200
    assert (await client.delete(f'{A}/categories/{c}/attributes/{aid}',headers=h)).status_code==200
    # Old filter URLs remain reserved; deleting the definition is intentionally blocked.
    assert (await client.delete(f'{E}/attributes/{aid}',headers=h)).status_code==409


@pytest.mark.asyncio
async def test_type_change_requires_explicit_confirmation_and_lossless_conversion(client,admin_headers):
    h=admin_headers
    c=await create_category(client,h,'r4-compat')
    p=await create_product(client,h,c)
    defn=await client.post(f'{E}/categories/{c}/attribute-definition',headers=h,json={
        'code':'r4_ratio','name':'Дробный показатель','data_type':'decimal','filterable':True})
    assert defn.status_code==201,defn.text
    aid=defn.json()['definition']['id']
    await client.put(f'{A}/products/{p}/attributes/{aid}',headers=h,json={'value':'1.5'})
    update={'code':'r4_ratio','name':'Дробный показатель','data_type':'integer',
            'enum_values':None,'unit':None,'filterable':True,'active':True,
            'expected_code':'r4_ratio','expected_data_type':'decimal','confirm_type_change':False}
    no=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=update)
    assert no.status_code==409
    update['confirm_type_change']=True
    invalid=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=update)
    assert invalid.status_code==409,invalid.text
    current=await client.get(f'{E}/products/{p}/identity',headers=h)
    item=next(v for v in current.json()['attributes'] if v['attribute_id']==aid)
    assert item['data_type']=='decimal' and item['value']=='1.500000'
    assert (await client.put(f'{A}/products/{p}/attributes/{aid}',headers=h,json={'value':'2.0'})).status_code==200
    good=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=update)
    assert good.status_code==200,good.text
    stale=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=update)
    assert stale.status_code==409


@pytest.mark.asyncio
async def test_delete_only_unreferenced_attribute_and_enum_usage(client,admin_headers):
    h=admin_headers
    c=await create_category(client,h,'r4-deletion')
    a=await client.post(f'{E}/categories/{c}/attribute-definition',headers=h,json={
        'code':'r4_color','name':'Цвет','data_type':'enum','enum_values':['red','blue'],
        'filterable':True,'show_in_filters':True})
    assert a.status_code==201,a.text
    aid=a.json()['definition']['id']
    p=await create_product(client,h,c)
    assert (await client.put(f'{A}/products/{p}/attributes/{aid}',headers=h,json={'value':'red'})).status_code==200
    update={'code':'r4_color','name':'Цвет кузова','data_type':'enum',
            'enum_values':['blue'],'unit':None,'filterable':True,'active':True,
            'expected_code':'r4_color','expected_data_type':'enum','confirm_type_change':False}
    assert (await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=update)).status_code==409
    assert (await client.delete(f'{E}/attributes/{aid}',headers=h)).status_code==409
    assert (await client.delete(f'{A}/products/{p}/attributes/{aid}',headers=h)).status_code==200
    assert (await client.delete(f'{E}/attributes/{aid}',headers=h)).status_code==409
    assert (await client.delete(f'{A}/categories/{c}/attributes/{aid}',headers=h)).status_code==200
    deleted=await client.delete(f'{E}/attributes/{aid}',headers=h)
    assert deleted.status_code==200,deleted.text
    assert (await client.get(f'{E}/attributes/{aid}/usage',headers=h)).status_code==404


@pytest.mark.asyncio
async def test_empty_type_change_ui_endpoints_and_auth(client,admin_headers):
    h=admin_headers
    c=await create_category(client,h,'r4-empty')
    assert (await client.post(f'{E}/categories/{c}/attribute-definition',json={
        'code':'r4_unauth','name':'Анкета','data_type':'text'})).status_code==403
    created=await client.post(f'{E}/categories/{c}/attribute-definition',headers=h,json={
        'code':'r4_empty','name':'Пустое поле','data_type':'text'})
    assert created.status_code==201,created.text
    aid=created.json()['definition']['id']
    updated=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json={
        'code':'r4_empty','name':'Пустой список','data_type':'enum','enum_values':['yes','no'],
        'unit':None,'filterable':True,'active':True,
        'expected_code':'r4_empty','expected_data_type':'text','confirm_type_change':True})
    assert updated.status_code==200,updated.text
    js=await client.get('/static/catalog_editor.js')
    assert js.status_code==200
    for token in ('create-category-attribute','delete-attribute','confirm_type_change','create-product-attribute'):
        assert token in js.text
