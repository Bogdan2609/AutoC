"""Category templates, inherited metadata and faceted navigation on disposable test DB."""
import pytest

A = '/api/v1/backoffice/catalog-v021'
E = '/api/v1/backoffice/catalog-editor'
P = '/api/v1/catalog-v021'


async def make_product(client,headers, brand, article, name):
    response=await client.post('/api/v1/backoffice/products', headers=headers,
                               json={'brand_id':brand,'article':article,'name':name})
    assert response.status_code==201,response.text
    return response.json()['id']


@pytest.mark.asyncio
async def test_template_inheritance_filter_ranges_and_atomic_batch(client,admin_headers):
    h=admin_headers
    brand=(await client.post('/api/v1/backoffice/brands',headers=h,
                             json={'name':'R3TEST','slug':'r3test'})).json()['id']
    parent=(await client.post(A+'/categories',headers=h,json={
        'name':'Жидкости','slug':'r3-fluids'})).json()['id']
    child=(await client.post(A+'/categories',headers=h,json={
        'name':'Антифризы','slug':'r3-antifreeze','parent_id':parent})).json()['id']
    unrelated=(await client.post(A+'/categories',headers=h,json={
        'name':'Ремни','slug':'r3-belts'})).json()['id']
    weight=(await client.post(A+'/attributes',headers=h,json={
        'name':'Масса фасовки','code':'r3_mass_kg','data_type':'decimal','unit':'kg','filterable':True})).json()['id']
    typ=(await client.post(A+'/attributes',headers=h,json={
        'name':'Класс','code':'r3_coolant_class','data_type':'enum',
        'enum_values':['G11','G12+'],'filterable':True})).json()['id']
    belt=(await client.post(A+'/attributes',headers=h,json={
        'name':'Число зубьев','code':'r3_belt_teeth','data_type':'integer','filterable':True})).json()['id']
    for cat,attr in ((parent,typ),(child,weight),(unrelated,belt)):
        r=await client.post(f'{A}/categories/{cat}/attributes',headers=h,
                            json={'attribute_id':attr,'required':False})
        assert r.status_code==201,r.text
    # Parent attr inherited; overriding child layout does not create a second definition.
    update=await client.put(f'{E}/categories/{child}/template/{typ}',headers=h,
                            json={'required':True,'show_in_filters':True,'sort_order':5,'group_name':'Химические свойства'})
    assert update.status_code==200,update.text
    upd2=await client.put(f'{E}/categories/{child}/template/{weight}',headers=h,
                          json={'required':True,'show_in_filters':True,'sort_order':1,'group_name':'Фасовка'})
    assert upd2.status_code==200,upd2.text
    d=await client.get(f'{E}/categories/{child}/template',headers=h)
    assert d.status_code==200,d.text
    assert len(d.json()['items'])==2
    assert d.json()['items'][0]['code']=='r3_mass_kg'
    assert next(x for x in d.json()['items'] if x['code']=='r3_coolant_class')['group_name']=='Химические свойства'
    assert (await client.get(f'{E}/categories/{child}/template')).status_code==403
    p1=await make_product(client,h,brand,'01','Антифриз 1 кг')
    p5=await make_product(client,h,brand,'05','Антифриз 5 кг')
    p_draft=await make_product(client,h,brand,'10','Антифриз 10 кг')
    for pid in (p1,p5,p_draft):
        r=await client.post(f'{A}/products/{pid}/categories',headers=h,
                            json={'category_id':child,'is_primary':True})
        assert r.status_code==201,r.text
    form=(await client.get(f'{E}/products/{p1}/attribute-form',headers=h)).json()
    assert len(form['missing_required'])==2
    invalid=await client.put(f'{E}/products/{p1}/attribute-values',headers=h,json={'items':[
        {'attribute_id':weight,'value':'1'}, {'attribute_id':typ,'value':'NOT_AN_ENUM'}]})
    assert invalid.status_code==422
    form=(await client.get(f'{E}/products/{p1}/attribute-form',headers=h)).json()
    assert len(form['missing_required'])==2, 'Invalid batch cannot partially persist'
    for pid,mass in ((p1,'1'),(p5,'5'),(p_draft,'10')):
        r=await client.put(f'{E}/products/{pid}/attribute-values',headers=h,json={'items':[
            {'attribute_id':weight,'value':mass},{'attribute_id':typ,'value':'G12+'}]})
        assert r.status_code==200,r.text
    assert (await client.post(f'/api/v1/backoffice/products/{p1}/publish',headers=h)).status_code==200
    assert (await client.post(f'/api/v1/backoffice/products/{p5}/publish',headers=h)).status_code==200
    facets=await client.get(f'{P}/categories/{child}/facets')
    assert facets.status_code==200,facets.text
    f={x['code']:x for x in facets.json()['facets']}
    assert f['r3_mass_kg']['min']=='1.000000'
    assert f['r3_mass_kg']['max']=='5.000000'
    assert f['r3_mass_kg']['products']==2
    assert f['r3_coolant_class']['options']==[{'value':'G12+','count':2}]
    for expr,expected in [('r3_mass_kg:1',1),('r3_mass_kg:1..5',2),('r3_mass_kg:2..6',1)]:
        r=await client.get(f'{P}/products',params={'category_id':child,'attr':expr})
        assert r.status_code==200,r.text
        assert r.json()['count']==expected
    assert (await client.get(f'{P}/products',params={'category_id':child,'attr':'r3_belt_teeth:12'})).status_code==422
    assert (await client.get(f'{P}/products',params={'category_id':child,'attr':'r3_mass_kg:5..1'})).status_code==422
    assert (await client.get(f'{P}/products',params={'category_id':child,'attr':'r3_mass_kg:NaN..10'})).status_code==422
    # A hidden filter is removed from public facets & rejected as category-scoped query.
    off=await client.put(f'{E}/categories/{child}/template/{typ}',headers=h,
                         json={'required':True,'show_in_filters':False,'sort_order':5,'group_name':'Химия'})
    assert off.status_code==200
    facets=(await client.get(f'{P}/categories/{child}/facets')).json()['facets']
    assert [r['code'] for r in facets]==['r3_mass_kg']
    assert (await client.get(f'{P}/products',params={'category_id':child,'attr':'r3_coolant_class:G12+'})).status_code==422


@pytest.mark.asyncio
async def test_existing_template_requires_filterable_definition(client,admin_headers):
    h=admin_headers
    c=(await client.post(A+'/categories',headers=h,json={'name':'Оборудование','slug':'r3-devices'})).json()['id']
    a=(await client.post(A+'/attributes',headers=h,json={'name':'Internal','code':'r3_internal',
                                                       'data_type':'text','filterable':False})).json()['id']
    denied=await client.put(f'{E}/categories/{c}/template/{a}',headers=h,json={
        'required':False,'show_in_filters':True,'sort_order':0,'group_name':'Основные'})
    assert denied.status_code==422
    ok=await client.put(f'{E}/categories/{c}/template/{a}',headers=h,json={
        'required':False,'show_in_filters':False,'sort_order':0,'group_name':'Основные'})
    assert ok.status_code==200
    assert (await client.get(f'{P}/categories/{c}/facets')).json()['facets']==[]


@pytest.mark.asyncio
async def test_category_page_renders_controls_and_excludes_drafts(client,admin_headers):
    h=admin_headers
    brand=(await client.post('/api/v1/backoffice/brands',headers=h,json={'name':'R3PAGES','slug':'r3pages'})).json()['id']
    cat=(await client.post(A+'/categories',headers=h,json={'name':'Свечи','slug':'r3-spark-page'})).json()['id']
    aid=(await client.post(A+'/attributes',headers=h,json={
        'name':'Калильное число','code':'r3_heat_rating','data_type':'integer','filterable':True})).json()['id']
    assert (await client.put(f'{E}/categories/{cat}/template/{aid}',headers=h,json={
        'required':False,'show_in_filters':True,'sort_order':1,'group_name':'Размеры'})).status_code==200
    published=await make_product(client,h,brand,'ONE','Свеча первая')
    draft=await make_product(client,h,brand,'TWO','Свеча в черновике')
    for pid,heat in ((published,6),(draft,12)):
        assert (await client.post(f'{A}/products/{pid}/categories',headers=h,json={
            'category_id':cat,'is_primary':True})).status_code==201
        assert (await client.put(f'{E}/products/{pid}/attribute-values',headers=h,json={
            'items':[{'attribute_id':aid,'value':heat}]})).status_code==200
    assert (await client.post(f'/api/v1/backoffice/products/{published}/publish',headers=h)).status_code==200
    html=await client.get('/category/r3-spark-page')
    assert html.status_code==200,html.text[:700]
    assert 'Свеча первая' in html.text and 'Свеча в черновике' not in html.text
    assert 'data-facet-min="r3_heat_rating"' in html.text
    assert (await client.get('/static/category_facets.js')).status_code==200
    filtered=await client.get('/category/r3-spark-page',params={'attr':'r3_heat_rating:7..12'})
    assert filtered.status_code==200
    assert 'Товары не найдены' in filtered.text
    invalid=await client.get('/category/r3-spark-page',params={'attr':'r3_nonexistent:42'})
    assert invalid.status_code==422
    assert (await client.get('/category/not-an-existing-r3-category')).status_code==404


@pytest.mark.asyncio
async def test_readiness_flags_missing_primary_and_restricts_guest(client,admin_headers):
    h=admin_headers
    brand=(await client.post('/api/v1/backoffice/brands',headers=h,json={
        'name':'CHECK BRAND','slug':'check-brand'})).json()['id']
    pid=await make_product(client,h,brand,'900','Тест готовности')
    path=f'{E}/products/{pid}/readiness'
    assert (await client.get(path)).status_code==403
    check=await client.get(path,headers=h)
    assert check.status_code==200,check.text
    assert check.json()['ready'] is False
    codes={x['code'] for x in check.json()['issues']}
    assert {'primary_category','no_approved_photo'}.issubset(codes)
