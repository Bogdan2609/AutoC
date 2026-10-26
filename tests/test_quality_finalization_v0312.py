"""v0.31.2: legacy recoveries are explicit individual operator actions; fail closed."""
import uuid
import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, Brand, ProductChangeLog
from app.modules.catalog_ext.models import ProductCategory, Category
from app.modules.supplier_feeds.models import FeedOffer
from app.modules.multi_enrichment.service import bootstrap,analyze_chunk
from app.modules.multi_enrichment.taxonomy_db import snapshot
from app.modules.catalog_bulk.service import scan_batch
from app.modules.draft_materialization.service import preflight_chunk,materialize_chunk
from app.modules.draft_materialization.finalization_api import evaluate_legacy
from tests.test_catalog_bulk_v027 import seed_feed

async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen,await anext(gen)

async def seed_legacy(db,description='Успокоитель цепи ГРМ левый',product_status='draft',noindex=True):
    batch=await seed_feed(db,'rossko',[('MITSUBISHI','MN183892',description,'sku')])
    brand=Brand(name='Mitsubishi',name_norm='MITSUBISHI',slug='mitsubishi')
    db.add(brand);await db.flush()
    p=Product(id=uuid.uuid4(),brand_id=brand.id,article='MN183892',article_norm='MN183892',
              name='Mitsubishi MN183892',slug='mitsubishi-mn183892',status=product_status,
              seo_noindex=noindex)
    db.add(p);await db.flush()
    fo=await db.scalar(select(FeedOffer));fo.product_id=p.id
    await db.commit()
    await bootstrap(db)
    return p

@pytest.mark.asyncio
async def test_legacy_recovery_preview_and_one_only_manual_write(client,admin_headers):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db)
        url=f'/api/v1/admin/draft-materialization/quality-guard/legacy/{p.id}'
        a=await client.get(url+'/preview',headers=admin_headers)
        assert a.status_code==200,a.text
        view=a.json()
        assert view['eligible_for_individual_review'] and view['suggested_category_code']=='timing_chain_guide'
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==0
        req={'expected_name':p.name,'expected_revision':view['rule_revision'],
             'evidence_token':view['evidence_token'],'category_code':'timing_chain_guide',
             'confirm_individual_manual':True,'confirm_unpublished_draft':True,
             'reason':'Проверено оператором по оригинальному описанию поставщика'}
        assert (await client.post(url+'/assign-primary',json=req)).status_code in (401,403)
        assert (await client.post(url+'/assign-primary',headers=admin_headers,
                                  json={**req,'confirm_individual_manual':False})).status_code==422
        assert (await client.post(url+'/assign-primary',headers=admin_headers,
                                  json={**req,'category_code':'timing_chain_tensioner'})).status_code==409
        result=await client.post(url+'/assign-primary',headers=admin_headers,json=req)
        assert result.status_code==200,result.text
        assert result.json()['published'] is False
        await db.refresh(p)
        assert p.status=='draft' and p.seo_noindex and p.name=='Mitsubishi MN183892'
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==1
        assert await db.scalar(select(func.count()).select_from(ProductChangeLog))==1
        assert (await client.post(url+'/assign-primary',headers=admin_headers,json=req)).status_code==409
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_legacy_recovery_rejects_stale_offers_and_operator_title(client,admin_headers):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db)
        url=f'/api/v1/admin/draft-materialization/quality-guard/legacy/{p.id}'
        view=(await client.get(url+'/preview',headers=admin_headers)).json()
        req={'expected_name':p.name,'expected_revision':view['rule_revision'],
             'evidence_token':view['evidence_token'],'category_code':'timing_chain_guide',
             'confirm_individual_manual':True,'confirm_unpublished_draft':True,
             'reason':'Проверено оператором по оригинальному описанию поставщика'}
        fo=await db.scalar(select(FeedOffer));fo.description='Цепь ГРМ'
        await db.commit()
        res=await client.post(url+'/assign-primary',headers=admin_headers,json=req)
        assert res.status_code==409
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==0
        fo.description='Успокоитель цепи ГРМ левый';p.name='Operator manually edited'
        await db.commit()
        res=await client.post(url+'/assign-primary',headers=admin_headers,json=req)
        assert res.status_code==409
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_legacy_blocks_non_auto_unknown_unpublished_and_head_conflict(client,admin_headers):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db,'Жидкость для розжига 0.5л')
        url=f'/api/v1/admin/draft-materialization/quality-guard/legacy/{p.id}'
        view=(await client.get(url+'/preview',headers=admin_headers)).json()
        assert 'non_automotive_item' in view['reasons']
        assert view['suggested_category_code'] is None
        fo=await db.scalar(select(FeedOffer));fo.description='Стойка | перед прав/лев'
        await db.commit()
        view=(await client.get(url+'/preview',headers=admin_headers)).json()
        assert 'unrecognized_physical_part' in view['reasons']
        p.status='published';p.seo_noindex=False
        await db.commit()
        view=(await client.get(url+'/preview',headers=admin_headers)).json()
        assert 'not_unpublished_noindex_draft' in view['reasons']
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_final_materialize_reconciles_current_semantics_even_if_saved_fingerprint_equal(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('MITSUBISHI','MN183892','Успокоитель цепи ГРМ','sku')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        assert (await preflight_chunk(db,limit=100))['eligible']==1
        # Monkeypatch only the independent final reconciliation; fingerprint
        # and saved source fields are untouched. Fail closed with no Product.
        import app.modules.draft_materialization.service as svc
        original=svc.reconcile
        def wrong(*a,**kw):
            from dataclasses import replace
            v=original(*a,**kw)
            return replace(v,title='Unexpected changed category')
        svc.reconcile=wrong
        try:
            result=await materialize_chunk(db,limit=100)
        finally:svc.reconcile=original
        assert result['created']==0 and result['reviewed']==1
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_review_queue_does_not_write_and_rejects_invalid_state(client,admin_headers):
    r=await client.get('/api/v1/admin/draft-materialization/quality-guard/review-queue',headers=admin_headers)
    assert r.status_code==200 and r.json()['read_only'] is True
    x=await client.get('/api/v1/admin/draft-materialization/quality-guard/review-queue?status=published',headers=admin_headers)
    assert x.status_code==422
