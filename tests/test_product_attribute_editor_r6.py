"""R6 modal API: typed facts, concurrency, atomicity and synthetic publication safety."""
import pytest

B='/api/v1/backoffice'
C='/api/v1/backoffice/catalog-v021'
E='/api/v1/backoffice/catalog-editor'

async def setup(client,h, *, brand_name='R6 CHECK',slug='r6-check',article='R6TEST'):
    b=await client.post(B+'/brands',headers=h,json={'name':brand_name,'slug':slug})
    assert b.status_code==201,b.text
    p=await client.post(B+'/products',headers=h,json={'brand_id':b.json()['id'],'article':article,'name':'Тест карточки R6'})
    assert p.status_code==201,p.text
    return p.json()['id']

async def make_fields(client,h,pid):
    c=(await client.post(C+'/categories',headers=h,json={'name':'Тест характеристик','slug':'r6-attrs'})).json()['id']
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={'category_id':c,'is_primary':True})).status_code==201
    attrs=[]
    for code,ty,vals,required in [('r6_color','enum',['Красный','Зелёный'],True),('r6_weight','decimal',None,False),('r6_stock_bool','boolean',None,False)]:
        body={'code':code,'name':code,'data_type':ty,'filterable':True}
        if vals is not None:body['enum_values']=vals
        result=await client.post(C+'/attributes',headers=h,json=body)
        assert result.status_code==201,result.text
        aid=result.json()['id'];attrs.append(aid)
        assert (await client.put(f'{E}/categories/{c}/template/{aid}',headers=h,json={'required':required,'show_in_filters':True,'sort_order':0,'group_name':'Тест'})).status_code==200
    return c,attrs

@pytest.mark.asyncio
async def test_r6_crud_bulk_and_optimistic_conflict(client,admin_headers):
    h=admin_headers;pid=await setup(client,h);_,(color,weight,boolean)=await make_fields(client,h,pid)
    url=f'{E}/products/{pid}/attribute-changes'
    first=await client.patch(url,headers=h,json={'changes':[
      {'attribute_id':color,'action':'set','value':'Красный','expected_value':None},
      {'attribute_id':weight,'action':'set','value':'5.125','expected_value':None},
      {'attribute_id':boolean,'action':'set','value':False,'expected_value':None}]})
    assert first.status_code==200,first.text
    frm=(await client.get(f'{E}/products/{pid}/attribute-form',headers=h)).json()
    assert {r['code']:r['value'] for r in frm['items']}=={'r6_color':'Красный','r6_weight':'5.125000','r6_stock_bool':False}
    # Someone else changed color after the modal opened: both requested writes must roll back.
    fail=await client.patch(url,headers=h,json={'changes':[
      {'attribute_id':color,'action':'set','value':'Зелёный','expected_value':None},
      {'attribute_id':weight,'action':'remove','expected_value':'5.125000'}]})
    assert fail.status_code==409
    same=(await client.get(f'{E}/products/{pid}/attribute-form',headers=h)).json()
    assert next(x['value'] for x in same['items'] if x['code']=='r6_weight')=='5.125000'
    bad=await client.patch(url,headers=h,json={'changes':[
      {'attribute_id':color,'action':'set','value':'Зелёный','expected_value':'Красный'},
      {'attribute_id':weight,'action':'set','value':'NaN','expected_value':'5.125000'}]})
    assert bad.status_code==422
    assert next(x['value'] for x in (await client.get(f'{E}/products/{pid}/attribute-form',headers=h)).json()['items'] if x['code']=='r6_color')=='Красный'
    remove=await client.patch(url,headers=h,json={'changes':[
      {'attribute_id':weight,'action':'remove','expected_value':'5.125000'},
      {'attribute_id':color,'action':'set','value':'Зелёный','expected_value':'Красный'}]})
    assert remove.status_code==200,remove.text
    last=(await client.get(f'{E}/products/{pid}/attribute-form',headers=h)).json()
    assert next(x['value'] for x in last['items'] if x['code']=='r6_weight') is None
    assert next(x['value'] for x in last['items'] if x['code']=='r6_color')=='Зелёный'
    dup=await client.patch(url,headers=h,json={'changes':[
       {'attribute_id':color,'action':'set','value':'Красный','expected_value':'Зелёный'},
       {'attribute_id':color,'action':'remove','expected_value':'Зелёный'}]})
    assert dup.status_code==422
    denied=await client.patch(url,json={'changes':[{'attribute_id':color,'action':'remove','expected_value':'Зелёный'}]})
    assert denied.status_code==403
    # Valid required category means publishing is possible; removal is then blocked.
    pub=await client.post(f'{B}/products/{pid}/publish',headers=h)
    assert pub.status_code==200,pub.text
    del_required=await client.patch(url,headers=h,json={'changes':[{'attribute_id':color,'action':'remove','expected_value':'Зелёный'}]})
    assert del_required.status_code==409
    still=(await client.get(f'{E}/products/{pid}/attribute-form',headers=h)).json()
    assert next(x['value'] for x in still['items'] if x['code']=='r6_color')=='Зелёный'

@pytest.mark.asyncio
async def test_r6_synthetic_qa_cannot_publish_or_be_public(client,admin_headers):
    h=admin_headers
    pid=await setup(client,h,brand_name='CATALOG FIXTURE',slug='r6-qa',article='FIXTURE-PLUG-UNIT')
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==409
    assert (await client.get(f'/api/v1/products/{pid}')).status_code==404
    assert (await client.get('/product/r6-qa-fixtureplugunit')).status_code==404
    assert (await client.get(f'{E}/products/{pid}/identity',headers=h)).status_code==200

@pytest.mark.asyncio
async def test_r6_route_and_static_admin_controls(client,admin_headers):
    assert (await client.get('/static/catalog_editor.js')).status_code==200
    js=(await client.get('/static/catalog_editor.js')).text
    assert "attribute-changes" in js
    assert 'Удалить значение только у товара' in js
    assert (await client.get('/operator')).status_code==200
    assert 'catalog_editor.js?v=0236' in (await client.get('/operator')).text

@pytest.mark.asyncio
async def test_r6_legacy_published_synthetic_is_hidden_everywhere(client,admin_headers):
    import uuid
    from app.main import app
    from app.core.db import get_session
    from app.modules.catalog.models import Product
    h=admin_headers
    pid=await setup(client,h,brand_name='CATALOG FIXTURE',slug='r6-hidden',article='BOX-FOUR')
    # Model a legacy bad publication status without invoking the newly guarded
    # publish endpoint. Only the disposable SQLite test DB is changed.
    session_gen=app.dependency_overrides[get_session]()
    db=await anext(session_gen)
    row=await db.get(Product,uuid.UUID(pid));row.status='published';await db.commit()
    await session_gen.aclose()
    assert (await client.get('/api/v1/search',params={'q':'BOX-FOUR'})).json()['count']==0
    assert (await client.get(f'/api/v1/products/{pid}')).status_code==404
    assert (await client.get(f'/api/v1/catalog-v021/products/{pid}/details')).status_code==404
    assert (await client.get('/product/r6-hidden-boxfour')).status_code==404
    assert (await client.get(f'{E}/products/{pid}/identity',headers=h)).json()['status']=='published'
