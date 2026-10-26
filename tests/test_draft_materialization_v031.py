"""Safety regression tests: preflight is metadata-only; materialization always draft."""
import uuid
import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_ext.models import ProductCategory, ProductAttribute
from app.modules.catalog_bulk.service import scan_batch
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate, CatalogCandidateBatch
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal
from app.modules.multi_enrichment.service import bootstrap, analyze_chunk
from app.modules.multi_enrichment.taxonomy_db import TaxonomyRevision
from app.modules.supplier_feeds.models import FeedOffer
from app.modules.draft_materialization.models import DraftPreflight as Preflight, DraftRun as Run, DraftAudit
from app.modules.draft_materialization.service import preflight_chunk,materialize_chunk,execute_run
from tests.test_catalog_bulk_v027 import seed_feed


async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen,await anext(gen)


async def setup(db, brand='GOODWILL', article='OG803',desc='Фильтр масляный двигателя'):
    batch=await seed_feed(db,'rossko',[(brand,article,desc,'sku1')]);await scan_batch(db,batch)
    await bootstrap(db);await analyze_chunk(db,None,100)
    return batch


@pytest.mark.asyncio
async def test_preflight_and_create_new_draft_atomically(client,admin_headers):
    gen,db=await getdb()
    try:
        await setup(db)
        before=await db.scalar(select(func.count()).select_from(Product))
        pf=await preflight_chunk(db,limit=10)
        assert pf['eligible']==1 and pf['reviewed']==0
        assert await db.scalar(select(func.count()).select_from(Product))==before==0
        response=await client.post('/api/v1/admin/draft-materialization/runs/materialize',headers=admin_headers,json={'limit':10,'confirm_draft_only':False})
        assert response.status_code==422
        result=await materialize_chunk(db,limit=10)
        assert result['created']==1 and result['reviewed']==0
        product=await db.scalar(select(Product))
        assert product.status=='draft' and product.seo_noindex
        assert product.name.startswith('Фильтр')
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==1
        assert (await db.scalar(select(Proposal))).status=='applied'
        assert (await db.scalar(select(Preflight))).status=='materialized'
        assert await db.scalar(select(func.count()).select_from(ProductChangeLog))==1
        assert await db.scalar(select(func.count()).select_from(DraftAudit))==1
        assert await materialize_chunk(db,limit=10)=={'seen':0,'created':0,'reviewed':0}
        assert await db.scalar(select(func.count()).select_from(Product))==1
        offers=(await db.scalars(select(FeedOffer))).all()
        assert all(x.product_id==product.id for x in offers)
        assert await db.scalar(select(func.count()).select_from(ProductAttribute))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_new_kit_variant_requires_review(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[
            ('GOODWILL','OG803','Фильтр масляный двигателя','sku1')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,100)
        provenance=await db.scalar(select(CatalogCandidateBatch));provenance.sku_variants=2;await db.commit()
        result=await preflight_chunk(db,limit=10)
        assert result['reviewed']==1
        pf=await db.scalar(select(Preflight))
        assert 'identity_variants_review' in pf.reasons
        assert (await materialize_chunk(db,limit=10))['created']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_source_change_after_preflight_blocks_product(client):
    gen,db=await getdb()
    try:
        await setup(db)
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        offer=await db.scalar(select(FeedOffer));offer.description='Фильтр воздушный';await db.commit()
        result=await materialize_chunk(db,limit=10)
        assert result['created']==0 and result['reviewed']==1
        pf=await db.scalar(select(Preflight))
        assert pf.status=='review' and 'stale_source_evidence' in pf.reasons[0]
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_rule_revision_change_blocks_create(client):
    gen,db=await getdb()
    try:
        await setup(db)
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        revision=await db.scalar(select(TaxonomyRevision))
        revision.revision+=1;await db.commit()
        assert (await materialize_chunk(db,limit=10))['created']==0
        # A new preflight is required; previous revision is not eligible for creation.
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_operator_title_and_existing_product_protected(client):
    gen,db=await getdb()
    try:
        await setup(db)
        cand=await db.scalar(select(Candidate))
        # Already handled identities never get a new product during preflight.
        cand.status='review';await db.commit()
        res=await preflight_chunk(db,limit=10)
        assert res['reviewed']==1
        assert 'candidate_already_linked_or_reviewed' in (await db.scalar(select(Preflight))).reasons
        assert (await materialize_chunk(db,limit=10))['created']==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_job_api_auth_run_and_stats(client,admin_headers):
    gen,db=await getdb()
    try:
        await setup(db)
        base='/api/v1/admin/draft-materialization'
        assert (await client.post(base+'/runs/preflight',json={'limit':1})).status_code in (401,403)
        res=await client.post(base+'/runs/preflight',headers=admin_headers,json={'limit':1})
        assert res.status_code==202,res.text
        rid=uuid.UUID(res.json()['id']);await execute_run(db,rid)
        record=(await client.get(base+'/runs/'+str(rid),headers=admin_headers)).json()
        assert record['status']=='completed' and record['eligible']==1
        stats=(await client.get(base+'/statistics',headers=admin_headers)).json()
        assert stats['statuses']['eligible']==1 and stats['auto_publish']==False
        res=await client.post(base+'/runs/materialize',headers=admin_headers,
                              json={'limit':1,'confirm_draft_only':True})
        assert res.status_code==202,res.text
        await execute_run(db,uuid.UUID(res.json()['id']))
        assert (await db.scalar(select(Product))).status=='draft'
        assert (await db.scalar(select(Preflight))).status=='materialized'
        assert (await client.post(base+'/runs/'+str(rid)+'/resume',headers=admin_headers)).status_code==409
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_conflicting_suppliers_cannot_preflight(client):
    gen,db=await getdb()
    try:
        a=await seed_feed(db,'rossko',[('555','SE101','Наконечник рулевой тяги правый','sku1')])
        b=await seed_feed(db,'partkom',[('555','SE101','Наконечник рулевой тяги левый','sku2')])
        await scan_batch(db,a);await scan_batch(db,b);await bootstrap(db);await analyze_chunk(db,None,10)
        assert (await db.scalar(select(Proposal))).status=='review'
        assert (await preflight_chunk(db,limit=10))['seen']==0
        assert (await materialize_chunk(db,limit=10))['created']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_two_supplier_attribution_and_installation_side(client):
    gen,db=await getdb()
    try:
        a=await seed_feed(db,'rossko',[('555','SE203L','Наконечник рулевой тяги левый','r101')])
        b=await seed_feed(db,'partkom',[('555','SE203L','Наконечник рулевой тяги | лев |','p101')])
        await scan_batch(db,a);await scan_batch(db,b);await bootstrap(db);await analyze_chunk(db,None,10)
        prop=await db.scalar(select(Proposal))
        assert prop.evidence['supplier_count']==2 and prop.attributes['installation_side']=='левый'
        assert (await preflight_chunk(db,limit=10))['eligible']==1
        assert (await materialize_chunk(db,limit=10))['created']==1
        product=await db.scalar(select(Product))
        assert product.status=='draft' and product.seo_noindex
        attrs=(await db.scalars(select(ProductAttribute))).all()
        assert len(attrs)==1 and attrs[0].value_enum=='левый'
        offers=(await db.scalars(select(FeedOffer))).all()
        assert len(offers)==2 and all(o.product_id==product.id for o in offers)
    finally:await gen.aclose()
