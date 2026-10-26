"""No real supplier/payment/network calls; semantics and controlled draft repair."""
import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_ext.models import ProductCategory, Category
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate
from app.modules.catalog_bulk.service import scan_batch
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal
from app.modules.multi_enrichment.service import bootstrap,analyze_chunk
from app.modules.multi_enrichment.classifier import reconcile, classify_source
from app.modules.multi_enrichment.taxonomy_db import snapshot, TaxonomyRevision
from app.modules.multi_enrichment.semantic_guard import detect_head,source_checks
from app.modules.draft_materialization.service import preflight_chunk,materialize_chunk
from app.modules.draft_materialization.models import DraftPreflight
from tests.test_catalog_bulk_v027 import seed_feed

async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen,await anext(gen)

@pytest.mark.parametrize(('description','code'), [
 ('Успокоитель цепи ГРМ левый','timing_chain_guide'),
 ('Натяжитель цепи ГРМ','timing_chain_tensioner'),
 ('Натяжитель цепи','timing_chain_tensioner'),
 ('Ролик натяжителя ремня','belt_tensioner'),
 ('Ролик натяжителя ремня ГРМ','timing_belt_roller'),
 ('Сайлентблок переднего рычага','arm_bushing'),
 ('Сайлентблок рамы','suspension_frame_bushing'),
 ('Поршень тормозного суппорта','brake_caliper_piston'),
 ('Ремкомплект тормозного суппорта','brake_caliper_repair_kit'),
 ('Опора задней стойки амортизатора','strut_mount'),
 ('Пыльник наружнего ШРУСа','cv_boot'),
 ('Подшипник ступицы колеса','wheel_bearing'),
 ('Ступица колеса','wheel_hub'),
])
def test_detects_specific_physical_part(description,code):
    assert detect_head(description).code==code


def test_compatibility_vs_real_mismatch():
    assert source_checks('bushing',['Сайлентблок переднего рычага'])['issues']==[]
    assert 'physical_part_type_mismatch' in source_checks('timing_chain',['Успокоитель цепи ГРМ'])['issues']
    assert 'supplier_physical_part_disagreement' in source_checks('timing_chain',[
        'Цепь ГРМ','Успокоитель цепи ГРМ'])['issues']
    assert 'part_subsystem_unconfirmed' in source_checks('timing_chain_guide',['Успокоитель цепи'])['issues']
    assert 'non_automotive_item' in source_checks(None,['Жидкость для розжига 0.5л'])['issues']


@pytest.mark.asyncio
async def test_rule_bootstrap_is_additive_and_routing_respects_part_head(client):
    gen,db=await getdb()
    try:
        x=await bootstrap(db)
        assert x['new_patterns']>0
        book=await snapshot(db)
        assert 'timing_chain_guide' in book.types
        assert 'timing_chain_tensioner' in book.types
        for desc,code in [('Успокоитель цепи ГРМ левый','timing_chain_guide'),
                          ('Натяжитель цепи ГРМ','timing_chain_tensioner'),
                          ('Ролик натяжителя ремня','belt_tensioner')]:
            row=classify_source(desc,book,'rossko')
            assert row['category_code']==code,row
            assert 'multiple_item_types' not in row['flags'],row
        b=await bootstrap(db)
        assert b['new_patterns']==0
        assert b['rule_revision']==x['rule_revision']
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_classification_refuses_supplier_conflict_and_underspecified_head(client):
    gen,db=await getdb()
    try:
        await bootstrap(db);book=await snapshot(db)
        sources=[{'supplier':'rossko','batch_id':'1','source_line':1,'description':'Цепь ГРМ'},
                 {'supplier':'partkom','batch_id':'2','source_line':2,'description':'Успокоитель цепи ГРМ'}]
        result=reconcile('Mitsubishi','MN183892',sources,book)
        assert 'supplier_physical_part_disagreement' in result.conflicts
        assert result.category_code is None
        ambiguous=reconcile('Mitsubishi','MN183892',[
            {'supplier':'rossko','batch_id':'1','source_line':1,'description':'Успокоитель цепи'}],book)
        assert ambiguous.conflicts
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_preflight_blocks_stored_wrong_category_even_if_quality_92(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('Mitsubishi','MN183892','Успокоитель цепи ГРМ','a')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        p=await db.scalar(select(Proposal)); wrong=await db.scalar(select(Category).where(Category.name=='Цепи ГРМ'))
        assert wrong is not None
        p.category_code='timing_chain';p.category_id=wrong.id;p.quality=92
        await db.commit()
        res=await preflight_chunk(db,limit=100)
        assert res['reviewed']==1 and res['eligible']==0
        pf=await db.scalar(select(DraftPreflight))
        assert 'physical_part_type_mismatch' in pf.reasons
        assert (await materialize_chunk(db,limit=10))['created']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_one_by_one_repair_with_revision_and_manual_edit_guards(client,admin_headers):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('Mitsubishi','MN183892','Успокоитель цепи ГРМ левый','a')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        assert (await materialize_chunk(db,limit=10))['created']==1
        p=await db.scalar(select(Proposal));product=await db.scalar(select(Product))
        old_category=await db.scalar(select(Category).where(Category.name=='Цепи ГРМ'))
        primary=await db.scalar(select(ProductCategory).where(ProductCategory.product_id==product.id))
        product.name='Цепь ГРМ левая Mitsubishi MN183892'
        primary.category_id=old_category.id
        p.title=product.name;p.category_code='timing_chain';p.category_id=old_category.id
        await db.commit()
        base=f'/api/v1/admin/draft-materialization/quality-guard/drafts/{product.id}/repair'
        preview=await client.get('/api/v1/admin/draft-materialization/quality-guard/drafts/suspects',headers=admin_headers)
        assert preview.status_code==200,preview.text
        assert preview.json()['suspects']==1
        assert preview.json()['items'][0]['semantic']['issues']==['physical_part_type_mismatch']
        body={'expected_name':product.name,'expected_category_code':'timing_chain',
              'expected_revision':(await snapshot(db)).revision,'confirm_draft_only':True}
        assert (await client.post(base,json=body)).status_code in (401,403)
        assert (await client.post(base,headers=admin_headers,json={**body,'confirm_draft_only':False})).status_code==422
        assert (await client.post(base,headers=admin_headers,json={**body,'expected_revision':999})).status_code==409
        result=await client.post(base,headers=admin_headers,json=body)
        assert result.status_code==200,result.text
        assert result.json()['new_category']=='timing_chain_guide'
        assert result.json()['status']=='draft' and result.json()['seo_noindex']==True
        await db.refresh(product)
        assert product.status=='draft'
        assert product.name.startswith('Успокоитель цепи ГРМ')
        assert (await client.post(base,headers=admin_headers,json=body)).status_code==409
        assert await db.scalar(select(func.count()).select_from(ProductChangeLog))==2
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_manually_edited_draft_is_never_repaired(client,admin_headers):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('Mitsubishi','MN183892','Успокоитель цепи ГРМ','a')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        assert (await materialize_chunk(db,limit=10))['created']==1
        product=await db.scalar(select(Product));product.name='Edited by operator'
        await db.commit()
        url=f'/api/v1/admin/draft-materialization/quality-guard/drafts/{product.id}/repair'
        res=await client.post(url,headers=admin_headers,json={'expected_name':product.name,
            'expected_category_code':'timing_chain_guide','expected_revision':(await snapshot(db)).revision,
            'confirm_draft_only':True})
        assert res.status_code==409
        assert (await db.scalar(select(Product))).name=='Edited by operator'
    finally:await gen.aclose()


def test_real_vendor_regression_descriptions_are_recognized():
    """Cross-supplier descriptions taken from the user's full-catalog audit.

    These are *positive* linguistic examples, not independent proof of fitment.
    GRM is not inferred if absent from 'ролик натяжителя ремня'.
    """
    import json
    from pathlib import Path
    cases=json.loads((Path(__file__).parent/'fixtures/v0311_supplier_heads.json').read_text(encoding='utf-8'))
    assert len(cases)>=100
    for case in cases:
        head=detect_head(case['description'])
        assert head and head.code==case['expected_head'],case


@pytest.mark.asyncio
async def test_applied_proposals_and_published_products_not_auto_changed(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('Mitsubishi','MN183892','Успокоитель цепи ГРМ','a')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        assert (await materialize_chunk(db,limit=10))['created']==1
        prop=await db.scalar(select(Proposal));product=await db.scalar(select(Product))
        saved=(prop.title,prop.fingerprint,product.name)
        rev=await db.scalar(select(TaxonomyRevision));rev.revision+=1
        await db.commit()
        await analyze_chunk(db,None,100)  # protected applied; no forced rewrite
        await db.refresh(prop);await db.refresh(product)
        assert (prop.title,prop.fingerprint,product.name)==saved
        assert product.status=='draft' and product.seo_noindex is True
    finally:await gen.aclose()
