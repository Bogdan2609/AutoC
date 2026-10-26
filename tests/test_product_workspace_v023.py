"""v0.23 critical operator contract: atomic SEO changes, redirect history, protected drafts.

Disposable test DB only; no supplier HTTP, orders, procurement or physical stock changes.
"""
import pytest


async def product_fixture(client, admin_headers):
    b = await client.post('/api/v1/backoffice/brands', json={'name':'TEST SEO', 'slug':'test-seo'}, headers=admin_headers)
    assert b.status_code == 201, b.text
    p = await client.post('/api/v1/backoffice/products', json={
        'brand_id':b.json()['id'], 'article':'K-123', 'name':'Product test name'}, headers=admin_headers)
    assert p.status_code == 201, p.text
    return p.json()


async def identity(client, headers, pid):
    res=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/identity',headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def save(client, headers, p, **changes):
    body={'name':p['name'],'description':p['description'],'slug':p['slug'],
          'seo_title':p['seo_title'],'seo_description':p['seo_description'],
          'seo_h1':p['seo_h1'],'seo_noindex':p['seo_noindex'],
          'expected_updated_at':p['updated_at']}
    body.update(changes)
    return await client.patch(f'/api/v1/backoffice/catalog-editor/products/{p["id"]}/workspace',
                              json=body,headers=headers)


@pytest.mark.asyncio
async def test_atomic_fields_slug_and_permanent_direct_301(client,admin_headers):
    prod=await product_fixture(client,admin_headers)
    pid=prod['id'];old=prod['slug']
    first=await identity(client,admin_headers,pid)
    assert first['status']=='draft' and first['seo_title'] is None
    unauth=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/slug-history')
    assert unauth.status_code==403
    wrong=await save(client,admin_headers,first,slug='ABC INVALID')
    assert wrong.status_code==422
    a=await save(client,admin_headers,first,slug='test-seo-first',name='SEO changed title',
                 seo_title='SEO title',seo_h1='SEO headline',seo_description='SEO description',seo_noindex=True)
    assert a.status_code==200,a.text
    after=await identity(client,admin_headers,pid)
    assert after['slug']=='test-seo-first' and after['seo_noindex'] is True
    assert after['seo_title']=='SEO title' and after['name']=='SEO changed title'
    assert after['id']==pid and after['status']=='draft'
    # Lock-free optimistic protection against two editors overwriting each other.
    stale=await save(client,admin_headers,first,slug='test-seo-conflict')
    assert stale.status_code==409
    reserved=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/slug-availability',
                              params={'slug':old},headers=admin_headers)
    assert reserved.json()['available'] is False and reserved.json()['reason']=='history'
    reused=await save(client,admin_headers,after,slug=old)
    assert reused.status_code==409
    # Second rename: old links go straight to newest URL, no redirect chain.
    second=await save(client,admin_headers,after,slug='test-seo-final')
    assert second.status_code==200, second.text
    h=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/slug-history',headers=admin_headers)
    assert h.status_code==200 and len(h.json()['items'])==2
    changes=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/change-history',headers=admin_headers)
    assert changes.status_code==200 and len(changes.json()['items'])==2
    assert changes.json()['items'][0]['action']=='workspace_save'
    assert all(x['status_code']==301 and x['redirect_to']=='/product/test-seo-final' for x in h.json()['items'])
    # Draft product must not leak via either the current URL or 301 history.
    for slug in (old,'test-seo-first','test-seo-final'):
        response=await client.get('/product/'+slug,follow_redirects=False)
        assert response.status_code==404
    pub=await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)
    assert pub.status_code==200,pub.text
    for slug in (old,'test-seo-first'):
        r=await client.get('/product/'+slug,follow_redirects=False)
        assert r.status_code==301 and r.headers['location']=='/product/test-seo-final'
    page=await client.get('/product/test-seo-final',follow_redirects=False)
    assert page.status_code==200
    assert 'SEO title' in page.text and 'SEO headline' in page.text
    assert 'SEO description' in page.text
    assert 'rel="canonical"' in page.text
    assert 'noindex' in page.headers.get('x-robots-tag','')
    off=await client.post(f'/api/v1/backoffice/catalog-editor/products/{pid}/unpublish',headers=admin_headers)
    assert off.status_code==200 and off.json()['status']=='draft'
    changes=await client.get(f'/api/v1/backoffice/catalog-editor/products/{pid}/change-history',headers=admin_headers)
    assert changes.json()['items'][0]['action']=='unpublish'
    assert (await client.get('/product/test-seo-final',follow_redirects=False)).status_code==404
    assert (await client.get('/product/'+old,follow_redirects=False)).status_code==404


@pytest.mark.asyncio
async def test_duplicate_slug_conflict_does_not_update_description(client,admin_headers):
    a=await product_fixture(client,admin_headers)
    b=await client.post('/api/v1/backoffice/products',headers=admin_headers,json={
        'brand_id':a['brand_id'],'article':'OTHER','name':'Other product'})
    assert b.status_code==201
    before=await identity(client,admin_headers,a['id'])
    r=await save(client,admin_headers,before,slug=b.json()['slug'],description='SHOULD NOT SAVE')
    assert r.status_code==409
    after=await identity(client,admin_headers,a['id'])
    assert after['description']==before['description'] and after['slug']==before['slug']


@pytest.mark.asyncio
async def test_operator_layout_contains_all_three_sections(client):
    page=await client.get('/operator')
    assert page.status_code==200
    js=await client.get('/static/catalog_editor.js')
    css=await client.get('/static/catalog_editor.css')
    assert js.status_code==200 and css.status_code==200
    for label in ('Товар и контент','Структура и связи','Цены и предложения'):
        assert label in js.text
    assert 'grid-template-columns' in css.text
    assert 'slug-availability' in js.text and 'pw-save' in js.text
