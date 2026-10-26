"""v0.21: product identity preserved, safe taxonomy, typed facts and media lifecycle."""
import io
from PIL import Image
import pytest
from tests.test_catalog_integration import create_product

A='/api/v1/backoffice/catalog-v021'
P='/api/v1/catalog-v021'


@pytest.mark.asyncio
async def test_brand_profile_alias_governance_and_supplier_resolution(client,admin_headers):
    _,p=await create_product(client,admin_headers)
    brand=p['brand_id']
    r=await client.post(f'{A}/brands/{brand}/aliases',json={'alias':'FELIX LTD'})
    assert r.status_code==403
    r=await client.post(f'{A}/brands/{brand}/aliases',headers=admin_headers,json={'alias':' FELIX  LTD ','verified':False})
    assert r.status_code==201,r.text
    alias=r.json()['id']
    assert (await client.get(P+'/brands/resolve',params={'name':'felix ltd'})).json()['brand_id'] is None
    assert (await client.patch(f'{A}/brands/{brand}/aliases/{alias}',headers=admin_headers,params={'verified':True})).status_code==200
    resolved=(await client.get(P+'/brands/resolve',params={'name':'  felix  ltd '})).json()
    assert resolved['brand_id']==brand and resolved['source']=='verified_alias'
    assert (await client.post(f'{A}/brands/{brand}/aliases',headers=admin_headers,json={'alias':'Felix LTD'})).status_code==409
    assert (await client.put(f'{A}/brands/{brand}/profile',headers=admin_headers,
                             json={'website':'http://malicious.invalid','verified':True})).status_code==422
    assert (await client.put(f'{A}/brands/{brand}/profile',headers=admin_headers,
                             json={'legal_name':'Company LLC','website':'https://example.org','verified':True})).status_code==200
    assert (await client.get(f'{P}/brands/{brand}')).json()['profile']['website']=='https://example.org'
    assert (await client.delete(f'{A}/brands/{brand}/aliases/{alias}',headers=admin_headers)).status_code==200
    assert (await client.get(P+'/brands/resolve',params={'name':'felix ltd'})).json()['source']=='unresolved'


@pytest.mark.asyncio
async def test_categories_cycle_primary_characteristics_and_publish_gate(client,admin_headers):
    _,p=await create_product(client,admin_headers)
    pid=p['id']
    a=await client.post(A+'/categories',headers=admin_headers,json={'name':'Жидкости','slug':'fluid'})
    assert a.status_code==201,a.text
    parent=a.json()['id']
    b=await client.post(A+'/categories',headers=admin_headers,json={'name':'Антифриз','slug':'antifreeze','parent_id':parent})
    assert b.status_code==201,b.text
    cat=b.json()['id']
    assert (await client.put(f'{A}/categories/{parent}',headers=admin_headers,json={'name':'Жидкости','parent_id':cat})).status_code==409
    x=await client.post(A+'/products/'+pid+'/categories',headers=admin_headers,
                        json={'category_id':cat,'is_primary':True})
    assert x.status_code==201,x.text
    assert (await client.post(A+'/products/'+pid+'/categories',headers=admin_headers,
                        json={'category_id':parent,'is_primary':True})).status_code==409
    assert (await client.post(A+'/attributes',headers=admin_headers,
                         json={'name':'Масса','code':'mass_kg','data_type':'decimal','unit':'kg','filterable':True})).status_code==201
    e=await client.post(A+'/attributes',headers=admin_headers,
                        json={'name':'Класс','code':'coolant_class','data_type':'enum','enum_values':['G12','G12+'],'filterable':True})
    assert e.status_code==201,e.text
    aid=e.json()['id']
    bind=await client.post(f'{A}/categories/{parent}/attributes',headers=admin_headers,
                           json={'attribute_id':aid,'required':True})
    assert bind.status_code==201,bind.text
    # A category inherits its ancestor's mandatory attributes.
    fail=await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)
    assert fail.status_code==409 and 'coolant_class' in fail.text
    assert (await client.put(f'{A}/products/{pid}/attributes/{aid}',headers=admin_headers,
                             json={'value':'G13'})).status_code==422
    assert (await client.put(f'{A}/products/{pid}/attributes/{aid}',headers=admin_headers,
                             json={'value':'G12'})).status_code==200
    assert (await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)).status_code==200
    details=await client.get(f'{P}/products/{pid}/details')
    assert details.status_code==200,details.text
    assert details.json()['attributes'][0]['value']=='G12'
    assert details.json()['categories'][0]['is_primary'] is True
    assert (await client.get(P+'/categories')).status_code==200


@pytest.mark.asyncio
async def test_attributes_all_five_types_and_category_binding(client,admin_headers):
    _,p=await create_product(client,admin_headers);pid=p['id']
    cat=(await client.post(A+'/categories',headers=admin_headers,json={'name':'Свечи зажигания','slug':'plugs'})).json()['id']
    assert (await client.post(f'{A}/products/{pid}/categories',headers=admin_headers,json={'category_id':cat})).status_code==201
    for code,kind,good,bad in [('cold','integer',7,True),('diameter','decimal','14.25','NaN'),
                               ('ignition','boolean',False,1),('material','text','Iridium',''),
                               ('grade','enum','A','Z')]:
        r=await client.post(A+'/attributes',headers=admin_headers,json={
            'name':code+' value','code':code,'data_type':kind,**({'enum_values':['A','B']} if kind=='enum' else {})})
        assert r.status_code==201,r.text
        aid=r.json()['id']
        assert (await client.post(f'{A}/categories/{cat}/attributes',headers=admin_headers,
                                  json={'attribute_id':aid})).status_code==201
        assert (await client.put(f'{A}/products/{pid}/attributes/{aid}',headers=admin_headers,
                                 json={'value':bad})).status_code==422
        assert (await client.put(f'{A}/products/{pid}/attributes/{aid}',headers=admin_headers,
                                 json={'value':good})).status_code==200
    assert (await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)).status_code==200
    r=(await client.get(f'{P}/products/{pid}/details')).json()
    assert len(r['attributes'])==5
    assert next(x['value'] for x in r['attributes'] if x['code']=='ignition') is False


@pytest.mark.asyncio
async def test_image_upload_approval_thumbnail_draft_protection_and_pdf(client,admin_headers,monkeypatch,tmp_path):
    monkeypatch.setenv('CATALOG_MEDIA_ROOT',str(tmp_path/'media'))
    _,p=await create_product(client,admin_headers)
    pid=p['id']
    im=Image.new('RGB',(900,550),'red');buffer=io.BytesIO();im.save(buffer,'PNG')
    data={'kind':'image','product_id':pid,'position':'0','alt_text':'Red coolant'}
    denied=await client.post(A+'/media',data=data,files={'upload':('test.png',buffer.getvalue(),'image/png')})
    assert denied.status_code==403
    created=await client.post(A+'/media',headers=admin_headers,data=data,
                              files={'upload':('test.png',buffer.getvalue(),'image/png')})
    assert created.status_code==201,created.text
    mid=created.json()['id']
    assert created.json()['width']==900 and created.json()['mime_type']=='image/webp'
    assert (await client.get(P+f'/media/{mid}/file')).status_code==404
    assert (await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)).status_code==200
    assert (await client.get(f'{P}/products/{pid}/media')).json()['items']==[]
    assert (await client.patch(f'{A}/media/{mid}',headers=admin_headers,params={'status':'approved'})).status_code==200
    assert (await client.get(f'{P}/products/{pid}/media')).json()['items'][0]['id']==mid
    public_thumb=await client.get(f'{P}/media/{mid}/file',params={'variant':'thumb'})
    assert public_thumb.status_code==200 and public_thumb.headers['content-type'].startswith('image/webp')
    with Image.open(io.BytesIO(public_thumb.content)) as thumb:assert max(thumb.size)<=480
    assert (await client.post(A+'/media',headers=admin_headers,data=data,
                              files={'upload':('bad.svg',b'<svg><script>evil()</script></svg>','image/svg+xml')})).status_code==415
    from pypdf import PdfWriter
    writer=PdfWriter();writer.add_blank_page(width=72,height=72)
    pdf_buffer=io.BytesIO();writer.write(pdf_buffer)
    pdf=await client.post(A+'/media',headers=admin_headers,data={'kind':'document','product_id':pid},
                           files={'upload':('instructions.pdf',pdf_buffer.getvalue(),'application/pdf')})
    assert pdf.status_code==201,pdf.text
    p_id=pdf.json()['id']
    assert (await client.patch(f'{A}/media/{p_id}',headers=admin_headers,params={'status':'approved'})).status_code==200
    file=await client.get(f'{P}/media/{p_id}/file')
    assert file.status_code==200 and file.headers['content-disposition'].startswith('attachment;')
    assert (await client.delete(f'{A}/media/{mid}',headers=admin_headers)).status_code==200
    assert (await client.get(f'{P}/media/{mid}/file')).status_code==404


@pytest.mark.asyncio
async def test_media_cannot_leak_draft_product_even_when_approved(client,admin_headers,monkeypatch,tmp_path):
    monkeypatch.setenv('CATALOG_MEDIA_ROOT',str(tmp_path/'media'))
    _,p=await create_product(client,admin_headers)
    image=io.BytesIO();Image.new('RGB',(30,30),'blue').save(image,'PNG')
    r=await client.post(A+'/media',headers=admin_headers,data={'kind':'image','product_id':p['id']},
                     files={'upload':('a.png',image.getvalue(),'image/png')})
    assert r.status_code==201,r.text
    mid=r.json()['id']
    assert (await client.patch(f'{A}/media/{mid}',headers=admin_headers,params={'status':'approved'})).status_code==200
    assert (await client.get(f'{P}/media/{mid}/file')).status_code==404
    assert (await client.get(f'{P}/products/{p["id"]}/media')).status_code==404

@pytest.mark.asyncio
async def test_category_subtree_and_typed_filterable_search(client,admin_headers):
    _,p=await create_product(client,admin_headers)
    other=(await client.post('/api/v1/backoffice/products',headers=admin_headers,json={
        'brand_id':p['brand_id'],'article':'FILTER-2','name':'Product alternate'})).json()
    root=(await client.post(A+'/categories',headers=admin_headers,json={'name':'Масла','slug':'lubricants'})).json()['id']
    sub=(await client.post(A+'/categories',headers=admin_headers,json={'name':'Моторное масло','slug':'engine-oil',
                    'parent_id':root})).json()['id']
    for pid in (p['id'],other['id']):
        assert (await client.post(A+f'/products/{pid}/categories',headers=admin_headers,json={'category_id':sub})).status_code==201
    r=await client.post(A+'/attributes',headers=admin_headers,json={'name':'Вязкость','code':'viscosity_grade',
                              'data_type':'enum','enum_values':['5W-30','0W-20'],'filterable':True})
    assert r.status_code==201,r.text
    aid=r.json()['id']
    assert (await client.post(A+f'/categories/{root}/attributes',headers=admin_headers,json={'attribute_id':aid})).status_code==201
    assert (await client.put(A+f'/products/{p["id"]}/attributes/{aid}',headers=admin_headers,json={'value':'5W-30'})).status_code==200
    assert (await client.put(A+f'/products/{other["id"]}/attributes/{aid}',headers=admin_headers,json={'value':'0W-20'})).status_code==200
    for pid in (p['id'],other['id']):
        assert (await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=admin_headers)).status_code==200
    attr=(await client.get(P+f'/categories/{sub}/attributes')).json()['items']
    assert attr[0]['code']=='viscosity_grade'
    result=await client.get(P+'/products',params=[('category_id',root),('attr','viscosity_grade:5W-30')])
    assert result.status_code==200,result.text
    assert result.json()['count']==1 and result.json()['items'][0]['id']==p['id']
    assert (await client.get(P+'/products',params={'attr':'viscosity_grade:BAD'})).status_code==422
    assert (await client.get(P+'/products',params={'attr':'nonexistent:example'})).status_code==422
    assert (await client.get(P+'/products',params=[('attr','viscosity_grade:5W-30'),('attr','viscosity_grade:0W-20')])).status_code==422
