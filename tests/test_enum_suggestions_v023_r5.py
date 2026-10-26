"""R5: existing text values are discoverable before converting to enum."""
import pytest
A='/api/v1/backoffice/catalog-v021'
E='/api/v1/backoffice/catalog-editor'

@pytest.mark.asyncio
async def test_color_text_to_enum_prefilled_no_loss(client,admin_headers):
    h=admin_headers
    cat=await client.post(A+'/categories',headers=h,json={'name':'Краски','slug':'r5-paints'})
    assert cat.status_code==201,cat.text
    cid=cat.json()['id']
    attr=await client.post(f'{E}/categories/{cid}/attribute-definition',headers=h,json={
        'code':'r5_color','name':'Цвет','data_type':'text','filterable':True,'show_in_filters':True})
    assert attr.status_code==201,attr.text
    aid=attr.json()['definition']['id']
    brand=await client.post('/api/v1/backoffice/brands',headers=h,json={'name':'R5 TEST','slug':'r5-test'})
    assert brand.status_code==201,brand.text
    for i,color in enumerate(('Красный','Зелёный','Красный')):
        prod=await client.post('/api/v1/backoffice/products',headers=h,json={
            'brand_id':brand.json()['id'],'article':f'R5-COLOR-{i}','name':f'Тест {i}'})
        assert prod.status_code==201,prod.text
        product_id=prod.json()['id']
        c=await client.post(f'{A}/products/{product_id}/categories',headers=h,json={'category_id':cid,'is_primary':True})
        assert c.status_code==201,c.text
        v=await client.put(f'{A}/products/{product_id}/attributes/{aid}',headers=h,json={'value':color})
        assert v.status_code==200,v.text
    usage=await client.get(f'{E}/attributes/{aid}/usage',headers=h)
    assert usage.status_code==200,usage.text
    assert usage.json()['observed_values']==['Зелёный','Красный']
    assert usage.json()['observed_values_truncated'] is False
    payload={'code':'r5_color','name':'Цвет','data_type':'enum','unit':None,
       'enum_values':['Красный'],'filterable':True,'active':True,
       'expected_code':'r5_color','expected_data_type':'text','confirm_type_change':True}
    blocked=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=payload)
    assert blocked.status_code==409,blocked.text
    payload['enum_values']=['Красный','Зелёный','Синий']
    converted=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=payload)
    assert converted.status_code==200,converted.text
    assert converted.json()['converted_products']==3
    after=await client.get(f'{E}/attributes/{aid}/usage',headers=h)
    assert after.status_code==200
    assert after.json()['observed_values']==['Зелёный','Красный']
    assert after.json()['attribute']['enum_values']==['Красный','Зелёный','Синий']
    assert (await client.get(f'{E}/attributes/{aid}/usage')).status_code==403
    js=(await client.get('/static/catalog_editor.js')).text
    assert 'observed_values_truncated' in js and 'Список значений' in js
