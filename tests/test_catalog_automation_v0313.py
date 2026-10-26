"""Automation never publishes, changes operator titles or guesses source meaning."""
import uuid
import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_ext.models import ProductCategory
from app.modules.catalog_bulk.service import scan_batch
from app.modules.multi_enrichment.service import bootstrap,analyze_chunk
from app.modules.multi_enrichment.taxonomy_db import snapshot, TaxonomyRevision
from app.modules.draft_materialization.automation import (
    _automatic_legacy_candidate, candidate_page, execute_auto_pipeline, legacy_page)
from app.modules.draft_materialization.models import DraftRun, DraftPreflight
from app.modules.draft_materialization.service import execute_run
from app.modules.commerce.models import Job
from tests.test_quality_finalization_v0312 import seed_legacy
from tests.test_catalog_bulk_v027 import seed_feed

async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen, await anext(gen)


def parameters(rev,*,mode='drafts',stage='candidates',limit=100,legacy_limit=0):
    return {'rule_revision':rev, 'mode':mode, 'limit':limit,
            'stage':stage,'cursor':None,'legacy_cursor':None,
            'legacy_limit':legacy_limit,'legacy_scanned':0,
            'legacy_recovered':0,'legacy_reviewed':0,'legacy_eligible':0}

@pytest.mark.asyncio
async def test_automation_api_auth_and_confirmation(client,admin_headers):
    base='/api/v1/admin/draft-materialization/automation'
    assert (await client.post(base+'/runs',json={'mode':'drafts','limit':100})).status_code in (401,403)
    assert (await client.post(base+'/runs',headers=admin_headers,json={'mode':'drafts','limit':100})).status_code==422
    assert (await client.get(base+'/statistics',headers=admin_headers)).status_code==200
    assert (await client.post(base+'/runs',headers=admin_headers,json={'mode':'preview','limit':100})).status_code==409

@pytest.mark.asyncio
async def test_legacy_category_automation_idempotent_and_unpublished(client):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db,description='Успокоитель цепи ГРМ левый')
        rev=(await snapshot(db)).revision
        first=await _automatic_legacy_candidate(db,p.id,rev,apply=False,run_id=uuid.uuid4())
        assert first['eligible'] and not first['changed']
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==0
        result=await _automatic_legacy_candidate(db,p.id,rev,apply=True,run_id=uuid.uuid4())
        assert result['changed']
        await db.refresh(p)
        assert p.name=='Mitsubishi MN183892' and p.status=='draft' and p.seo_noindex
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==1
        assert await db.scalar(select(func.count()).select_from(ProductChangeLog))==1
        assert not (await _automatic_legacy_candidate(db,p.id,rev,apply=True,run_id=uuid.uuid4()))['changed']
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_legacy_automation_denies_ambiguous_nonauto_and_manual_edits(client):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db,description='Жидкость для розжига 0.5л')
        rev=(await snapshot(db)).revision
        result=await _automatic_legacy_candidate(db,p.id,rev,apply=True,run_id=uuid.uuid4())
        assert not result['changed']
        from app.modules.supplier_feeds.models import FeedOffer
        o=await db.scalar(select(FeedOffer));o.description='Успокоитель цепи ГРМ левый'
        db.add(ProductChangeLog(product_id=p.id,action='workspace_save',changes={'title':'human'}))
        await db.commit()
        result=await _automatic_legacy_candidate(db,p.id,rev,apply=True,run_id=uuid.uuid4())
        assert not result['changed'] and 'operator_or_repair_history_preserved' in result['reasons']
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==0
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_automatic_pipeline_drafts_and_resumption_safe(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('GOODWILL','OG803','Фильтр масляный двигателя','sku1')]);await scan_batch(db,batch)
        await bootstrap(db);await analyze_chunk(db,None,100)
        rev=(await snapshot(db)).revision
        run=DraftRun(kind='auto_pipeline',parameters=parameters(rev,limit=100),status='queued')
        db.add(run);await db.commit()
        await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='completed' and run.created==1 and run.scanned==1
        p=await db.scalar(select(Product))
        assert p.status=='draft' and p.seo_noindex
        assert (await db.scalar(select(DraftPreflight))).status=='materialized'
        assert await db.scalar(select(func.count()).select_from(Product))==1
        await execute_run(db,run.id)
        assert await db.scalar(select(func.count()).select_from(Product))==1
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_preview_pipeline_never_creates_product(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('GOODWILL','OG803','Фильтр масляный двигателя','sku1')]);await scan_batch(db,batch)
        await bootstrap(db);await analyze_chunk(db,None,100)
        rev=(await snapshot(db)).revision
        run=DraftRun(kind='auto_pipeline',parameters=parameters(rev,mode='preview',limit=100),status='queued')
        db.add(run);await db.commit()
        await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='completed' and run.eligible==1 and run.created==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_rule_revision_change_fails_closed(client):
    gen,db=await getdb()
    try:
        from app.modules.multi_enrichment.service import bootstrap
        await bootstrap(db)
        rev=(await snapshot(db)).revision
        run=DraftRun(kind='auto_pipeline',parameters=parameters(rev,limit=100),status='queued')
        db.add(run);await db.commit()
        r=await db.scalar(select(TaxonomyRevision));r.revision+=1;await db.commit()
        with pytest.raises(RuntimeError):await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='failed'
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_legacy_page_is_optin_and_scans_only_legacy(client):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db,description='Успокоитель цепи ГРМ левый')
        rev=(await snapshot(db)).revision
        run=DraftRun(kind='auto_pipeline',parameters=parameters(rev,stage='legacy',legacy_limit=20),status='processing')
        db.add(run);await db.commit()
        await execute_auto_pipeline(db,run)
        await db.refresh(run)
        assert run.status=='completed' and run.parameters['legacy_recovered']==1
        assert await db.scalar(select(func.count()).select_from(Product))==1
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==1
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_disabled_taxonomy_pattern_cannot_assign_legacy(client):
    gen,db=await getdb()
    try:
        p=await seed_legacy(db,description='Успокоитель цепи ГРМ левый')
        from app.modules.multi_enrichment.taxonomy_db import TaxonomyPattern
        patterns=(await db.scalars(select(TaxonomyPattern).where(
            TaxonomyPattern.type_code=='timing_chain_guide'))).all()
        assert patterns
        for row in patterns:row.active=False
        await db.commit()
        rev=(await snapshot(db)).revision
        r=await _automatic_legacy_candidate(db,p.id,rev,apply=True,run_id=uuid.uuid4())
        assert not r['changed'] and 'active_rule_does_not_confirm_source' in r['reasons']
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==0
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_job_handoff_uses_persisted_cursor_and_no_duplicates(client,monkeypatch):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[
            ('GOODWILL','OG803','Фильтр масляный двигателя','sku1'),
            ('GOODWILL','OG804','Фильтр масляный двигателя','sku2')]);await scan_batch(db,batch)
        await bootstrap(db);await analyze_chunk(db,None,100)
        rev=(await snapshot(db)).revision
        run=DraftRun(kind='auto_pipeline',parameters=parameters(rev,limit=100),status='queued')
        db.add(run);await db.commit()
        import app.modules.draft_materialization.automation as auto
        monkeypatch.setattr(auto,'CHUNK',1)
        monkeypatch.setattr(auto,'PAGES_PER_JOB',1)
        await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='processing' and run.scanned==1 and run.created==1
        assert await db.scalar(select(func.count()).select_from(Job).where(
            Job.kind=='draft_materialization'))==1
        await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='processing' and run.scanned==2 and run.created==2
        await execute_run(db,run.id)
        await db.refresh(run)
        assert run.status=='completed' and run.created==2
        assert await db.scalar(select(func.count()).select_from(Product))==2
        await execute_run(db,run.id)
        assert await db.scalar(select(func.count()).select_from(Product))==2
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_authenticated_launch_queues_background_job_and_rejects_parallel_run(client,admin_headers):
    gen,db=await getdb()
    try:
        await bootstrap(db)
        endpoint='/api/v1/admin/draft-materialization/automation/runs'
        first=await client.post(endpoint,headers=admin_headers,json={
            'mode':'drafts','limit':100,'legacy_limit':20,'confirm_draft_only':True})
        assert first.status_code==202,first.text
        assert first.json()['kind']=='auto_pipeline' and first.json()['auto_publish'] is False
        run_id=first.json()['id']
        assert await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='draft_materialization'))==1
        duplicate=await client.post(endpoint,headers=admin_headers,json={
            'mode':'preview','limit':20,'legacy_limit':0})
        assert duplicate.status_code==409,duplicate.text
        status=await client.get(endpoint+'/'+run_id,headers=admin_headers)
        assert status.status_code==200 and status.json()['status']=='queued'
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_stale_delivery_does_not_reprocess_completed_page(client,monkeypatch):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[
            ('GOODWILL','OG803','Фильтр масляный двигателя','sku1'),
            ('GOODWILL','OG804','Фильтр масляный двигателя','sku2')]);await scan_batch(db,batch)
        await bootstrap(db);await analyze_chunk(db,None,100)
        rev=(await snapshot(db)).revision
        settings=parameters(rev,limit=100)
        settings.update(next_job=0,generation=0)
        run=DraftRun(kind='auto_pipeline',parameters=settings,status='queued')
        db.add(run);await db.commit()
        import app.modules.draft_materialization.automation as auto
        monkeypatch.setattr(auto,'CHUNK',1);monkeypatch.setattr(auto,'PAGES_PER_JOB',1)
        await execute_run(db,run.id,auto_step=0,auto_generation=0)
        await db.refresh(run)
        assert run.scanned==1 and run.created==1 and run.parameters['next_job']==1
        await execute_run(db,run.id,auto_step=0,auto_generation=0)  # duplicate
        await db.refresh(run)
        assert run.scanned==1 and run.created==1 and run.parameters['next_job']==1
        await execute_run(db,run.id,auto_step=1,auto_generation=0)
        await db.refresh(run)
        assert run.scanned==2 and run.created==2 and run.parameters['next_job']==2
        await execute_run(db,run.id,auto_step=2,auto_generation=0)
        assert await db.scalar(select(func.count()).select_from(Product))==2
    finally:await gen.aclose()
