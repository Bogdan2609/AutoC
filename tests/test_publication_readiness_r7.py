"""R7 strict publication gates, disposable DB only; existing fixtures untouched."""
import pytest
from app.core.config import get_settings

B='/api/v1/backoffice'
C='/api/v1/backoffice/catalog-v021'
E='/api/v1/backoffice/catalog-editor'

async def seed(client, headers, suffix):
    h=headers
    brand=(await client.post(B+'/brands',headers=h,json={
        'name':'READINESS'+suffix, 'slug':'readiness'+suffix})).json()['id']
    product=(await client.post(B+'/products',headers=h,json={
        'brand_id':brand,'article':'ABC'+suffix,'name':'Тест проверки готовности'})).json()['id']
    return product

def strict(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    get_settings.cache_clear()

@pytest.mark.asyncio
async def test_strict_primary_gate_matches_operator_readiness(client, admin_headers, monkeypatch):
    h=admin_headers
    pid=await seed(client,h,'one')
    strict(monkeypatch)
    pre=(await client.get(f'{E}/products/{pid}/readiness',headers=h)).json()
    assert pre['ready'] is False
    assert 'primary_category' in [i['code'] for i in pre['issues']]
    r=await client.post(f'{B}/products/{pid}/publish',headers=h)
    assert r.status_code==409,r.text
    assert r.json()['detail']['code']=='PRODUCT_NOT_READY'
    assert 'primary_category' in [i['code'] for i in r.json()['detail']['issues']]
    assert (await client.get(f'{E}/products/{pid}/identity',headers=h)).json()['status']=='draft'
    cat=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Ремни ГРМ', 'slug':'r7-timing-one'})).json()['id']
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':cat,'is_primary':False})).status_code==201
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==409
    # Make the existing binding primary by removing it while product is draft.
    assert (await client.delete(f'{C}/products/{pid}/categories/{cat}',headers=h)).status_code==200
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':cat,'is_primary':True})).status_code==201
    assert (await client.get(f'{E}/products/{pid}/readiness',headers=h)).json()['ready'] is True
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==200
    assert (await client.delete(f'{C}/products/{pid}/categories/{cat}',headers=h)).status_code==409

@pytest.mark.asyncio
async def test_required_inherited_enum_and_postpublication_mutations(client, admin_headers, monkeypatch):
    h=admin_headers;pid=await seed(client,h,'two')
    root=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Автожидкости','slug':'r7-auto-root'})).json()['id']
    cat=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Антифризы','slug':'r7-antifreeze', 'parent_id':root})).json()['id']
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':cat,'is_primary':True})).status_code==201
    color=(await client.post(f'{C}/attributes',headers=h,json={
        'code':'r7_color','name':'Цвет','data_type':'enum','enum_values':['Красный','Зелёный'],
        'filterable':True})).json()['id']
    assert (await client.post(f'{C}/categories/{root}/attributes',headers=h,json={
        'attribute_id':color,'required':True})).status_code==201
    strict(monkeypatch)
    pre=(await client.get(f'{E}/products/{pid}/readiness',headers=h)).json()
    assert any(i['code']=='required_attribute' for i in pre['issues'])
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==409
    assert (await client.put(f'{C}/products/{pid}/attributes/{color}',headers=h,
        json={'value':'Синий'})).status_code==422
    assert (await client.put(f'{C}/products/{pid}/attributes/{color}',headers=h,
        json={'value':'Красный'})).status_code==200
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==200
    assert (await client.delete(f'{C}/products/{pid}/attributes/{color}',headers=h)).status_code==409
    assert (await client.patch(f'{E}/products/{pid}/attribute-changes',headers=h,json={
        'changes':[{'attribute_id':color,'action':'remove','expected_value':'Красный'}]})).status_code==409
    assert (await client.put(f'{C}/categories/{root}',headers=h,json={
        'name':'Автожидкости','active':False})).status_code==409
    # Making a new characteristic required may not invalidate an already-published product.
    weight=(await client.post(f'{C}/attributes',headers=h,json={
        'code':'r7_weight','name':'Вес','data_type':'decimal','unit':'kg',
        'filterable':True})).json()['id']
    r=await client.put(f'{E}/categories/{cat}/template/{weight}',headers=h,json={
        'required':True,'show_in_filters':True})
    assert r.status_code==409,r.text
    # No binding is persisted on rejection.
    tmpl=(await client.get(f'{E}/categories/{cat}/template',headers=h)).json()['items']
    assert all(x['attribute_id']!=weight for x in tmpl)

@pytest.mark.asyncio
async def test_strict_published_secondary_assignment_requires_complete_fields(client, admin_headers, monkeypatch):
    h=admin_headers;pid=await seed(client,h,'three')
    primary=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Первичная','slug':'r7-primary-cat'})).json()['id']
    secondary=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Вторичная','slug':'r7-secondary-cat'})).json()['id']
    aid=(await client.post(f'{C}/attributes',headers=h,json={
        'code':'r7_secondary_required','name':'Параметр','data_type':'text'})).json()['id']
    assert (await client.post(f'{C}/categories/{secondary}/attributes',headers=h,json={
        'attribute_id':aid,'required':True})).status_code==201
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':primary,'is_primary':True})).status_code==201
    strict(monkeypatch)
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==200
    add=await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':secondary,'is_primary':False})
    assert add.status_code==409,add.text
    original=(await client.get(f'{E}/products/{pid}/identity',headers=h)).json()
    assert len(original['categories'])==1

@pytest.mark.asyncio
async def test_qa_not_published_in_legacy_test_mode(client,admin_headers):
    pid=await seed(client,admin_headers,'four')
    r=await client.get(f'{E}/products/{pid}/readiness',headers=admin_headers)
    assert r.status_code==200
    assert r.json()['ready'] is False

@pytest.mark.asyncio
async def test_strict_attribute_deactivation_and_inline_creation_guards(client, admin_headers, monkeypatch):
    h=admin_headers;pid=await seed(client,h,'five')
    cat=(await client.post(f'{C}/categories',headers=h,json={
        'name':'Категория выпуска','slug':'r7-live-cat'})).json()['id']
    assert (await client.post(f'{C}/products/{pid}/categories',headers=h,json={
        'category_id':cat,'is_primary':True})).status_code==201
    aid=(await client.post(f'{C}/attributes',headers=h,json={
        'code':'r7_live_enum','name':'Тип смеси','data_type':'enum',
        'enum_values':['G12','G13'],'filterable':True})).json()['id']
    assert (await client.put(f'{E}/categories/{cat}/template/{aid}',headers=h,json={
        'required':True, 'show_in_filters':True})).status_code==200
    assert (await client.put(f'{C}/products/{pid}/attributes/{aid}',headers=h,json={
        'value':'G12'})).status_code==200
    strict(monkeypatch)
    assert (await client.post(f'{B}/products/{pid}/publish',headers=h)).status_code==200
    change={'code':'r7_live_enum','name':'Тип смеси','data_type':'enum',
        'enum_values':['G12','G13'],'filterable':True,'active':False,
        'expected_code':'r7_live_enum','expected_data_type':'enum'}
    r=await client.put(f'{E}/attributes/{aid}/schema',headers=h,json=change)
    assert r.status_code==409,r.text
    create=await client.post(f'{E}/categories/{cat}/attribute-definition',headers=h,json={
        'code':'r7_inline_required','name':'Новая обязательная характеристика',
        'data_type':'integer', 'required':True})
    assert create.status_code==409,create.text
    definitions=(await client.get(f'{C}/attributes',headers=h)).json()['items']
    assert all(x['code']!='r7_inline_required' for x in definitions)
