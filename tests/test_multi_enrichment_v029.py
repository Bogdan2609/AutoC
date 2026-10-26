"""v0.29: source evidence, candidate-first analysis, conflict isolation and no publication."""
import uuid
import pytest
from sqlalchemy import select, func
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Product
from app.modules.catalog_bulk.models import CatalogCandidate, CatalogCandidateBatch
from app.modules.catalog_bulk.service import scan_batch,materialize_approved
from app.modules.multi_enrichment.classifier import classify_source,reconcile
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal,MultiSourceRun as Run, MultiSourceDecision as Decision
from app.modules.multi_enrichment.service import bootstrap,analyze_chunk,approve_safe_chunk,prepare_drafts_chunk,apply_chunk,execute_run
from app.modules.supplier_feeds.models import FeedOffer
from tests.test_catalog_bulk_v027 import seed_feed


async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen,await anext(gen)


def source(supplier,description):
    return {'supplier':supplier,'batch_id':str(uuid.uuid4()),'source_line':2,'description':description,
            'description_variants':1,'sku_variants':1}


def test_semantic_agreement_despite_different_text():
    a=source('rossko','Подшипник ступицы, с ABS (96 магнитов) | перед прав/лев |')
    b=source('partkom','Подшипник передней ступицы, ABS (96 магнитов)')
    out=reconcile('BM','AML7102',[a,b])
    assert out.evidence['supplier_count']==2 and out.evidence['description_texts_differ']
    assert 'source_category_conflict' not in out.conflicts
    assert 'source_installation_position_conflict' not in out.conflicts
    assert 'execution_or_kit_review' in out.conflicts
    assert out.attributes['installation_position']=='передний'


def test_real_conflict_in_side_requires_review():
    result=reconcile('555','S1',[source('rossko','Наконечник рулевой тяги | прав |'),
                                 source('partkom','Наконечник рулевой тяги левый')])
    assert 'source_installation_side_conflict' in result.conflicts
    assert 'installation_side' not in result.attributes


def test_missing_claim_does_not_vote_against_explicit_claim():
    result=reconcile('555','S1',[source('rossko','Наконечник рулевой тяги левый'),
                                 source('partkom','Наконечник рулевой тяги')])
    assert 'source_installation_side_conflict' not in result.conflicts
    assert result.attributes['installation_side']=='левый'
    assert result.quality>=85 and not result.conflicts


def test_type_conflict_never_auto_approve():
    p=reconcile('555','S1',[source('rossko','Наконечник рулевой тяги'),source('partkom','Фильтр масляный')])
    assert p.category_code is None and 'source_category_conflict' in p.conflicts


def test_non_automotive_and_unknown_remain_review():
    x=reconcile('3ton','40216',[source('rossko','Жидкость для розжига Country 0.5л')])
    assert 'non_automotive_review' in x.conflicts and x.quality<85
    assert 'unclassified' in x.conflicts
    assert classify_source('Стойка | перед прав/лев |')['category_code'] is None
    assert classify_source('Сошка рулевая')['category_code']=='steering_pitman_arm'
    assert classify_source('Трапеция стеклоочистителя')['category_code']=='wiper_linkage'


def test_fingerprints_stable_and_warehouse_duplicates_do_not_vote():
    s=[source('rossko','Фильтр масляный'),source('partkom','Фильтр масляный двигателя')]
    a=reconcile('GoodWill','OG803',s)
    assert a.fingerprint==reconcile('GoodWill','OG803',list(reversed(s))).fingerprint
    assert 'source_category_conflict' not in a.conflicts
    assert a.evidence['supplier_count']==2


@pytest.mark.asyncio
async def test_candidate_first_evidence_and_draft_lifecycle(client,admin_headers):
    gen,db=await getdb()
    try:
        r=await seed_feed(db,'rossko',[('555','AA101','Наконечник рулевой тяги левый','r1')])
        p=await seed_feed(db,'partkom',[('555','AA101','Наконечник рулевой тяги | лев |','p1')])
        await scan_batch(db,r);await scan_batch(db,p)
        assert await db.scalar(select(func.count()).select_from(Product))==0
        await bootstrap(db)
        result=await analyze_chunk(db,None,200)
        assert result['seen']==1 and result['affected']==1 and result['reviewed']==0
        proposal=await db.scalar(select(Proposal))
        assert proposal.status=='proposed' and proposal.category_code=='tie_rod_end'
        assert proposal.evidence['supplier_count']==2
        lines={x['supplier']:(x['batch_id'],x['source_line']) for x in proposal.evidence['sources']}
        assert lines['rossko']==(str(r),1) and lines['partkom']==(str(p),1)
        assert await db.scalar(select(func.count()).select_from(Product))==0
        assert (await analyze_chunk(db,None,200))['affected']==0  # no duplicate writes
        assert await approve_safe_chunk(db,10)=={'seen':1,'affected':1,'reviewed':0}
        prepared=await prepare_drafts_chunk(db,10)
        assert prepared['affected']==1
        product_before=await db.scalar(select(func.count()).select_from(Product))
        assert product_before==0  # prepare != materialize
        materialized=await materialize_approved(db,limit=10,batch_id=r)
        assert materialized['created_drafts']==1
        result=await apply_chunk(db,10)
        assert result['affected']==1 and result['reviewed']==0
        product=await db.scalar(select(Product))
        assert product.status=='draft' and product.seo_noindex
        assert 'Наконечник рулевой тяги левый' in product.name
        assert (await apply_chunk(db,10))['seen']==0
        assert (await db.scalar(select(Proposal))).status=='applied'
        assert len((await db.scalars(select(Decision))).all())==2
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_conflicting_sources_stay_on_review_without_product_creation(client,admin_headers):
    gen,db=await getdb()
    try:
        r=await seed_feed(db,'rossko',[('555','CC101','Рычаг подвески перед прав','r1')])
        p=await seed_feed(db,'partkom',[('555','CC101','Рычаг подвески перед лев','p1')])
        await scan_batch(db,r);await scan_batch(db,p);await bootstrap(db)
        result=await analyze_chunk(db,None,10)
        assert result['reviewed']==1
        proposal=await db.scalar(select(Proposal))
        assert 'source_installation_side_conflict' in proposal.conflicts
        assert (await approve_safe_chunk(db,100))['affected']==0
        assert (await prepare_drafts_chunk(db,100))['affected']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
        response=await client.post(f'/api/v1/admin/multi-enrichment/proposals/{proposal.id}/decision',
                                   headers=admin_headers,json={'decision':'approved','note':'Проверено по описаниям','confirm_ambiguous':True})
        assert response.status_code==409
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_provenance_selects_one_actual_source_line_not_warehouse_count(client):
    gen,db=await getdb()
    try:
        b=await seed_feed(db,'rossko',[
            ('TRIALLI','X1','Фильтр масляный','sk1'),
            ('TRIALLI','X1','Фильтр масляный','sk1'),
            ('TRIALLI','X1','Фильтр масляный','sk1')])
        await scan_batch(db,b);await bootstrap(db)
        await analyze_chunk(db,None,10)
        p=await db.scalar(select(Proposal))
        assert len(p.evidence['sources'])==1
        assert p.evidence['supplier_count']==1
        assert p.evidence['sources'][0]['source_line']==1
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_api_admin_auth_preview_queue_and_job(client,admin_headers):
    gen,db=await getdb()
    try:
        bid=await seed_feed(db,'rossko',[('BM','M1','Диск тормозной','sk')])
        await scan_batch(db,bid)
        endpoint='/api/v1/admin/multi-enrichment'
        assert (await client.get(endpoint+'/taxonomy')).status_code in (401,403)
        assert (await client.post(endpoint+'/taxonomy/bootstrap',headers=admin_headers)).status_code==200
        preview=await client.post(endpoint+'/preview',headers=admin_headers,json={
            'brand':'BM','article':'M1','sources':[source('rossko','Диск тормозной')]})
        assert preview.status_code==200 and preview.json()['category_code']=='brake_disc'
        submit=await client.post(endpoint+'/runs/analyze',headers=admin_headers,json={'limit':10})
        assert submit.status_code==202,submit.text
        rid=uuid.UUID(submit.json()['id'])
        await execute_run(db,rid)
        run=(await client.get(endpoint+f'/runs/{rid}',headers=admin_headers)).json()
        assert run['status']=='completed' and run['affected']==1 and run['cursor']
        assert await db.scalar(select(func.count()).select_from(Product))==0
        stats=(await client.get(endpoint+'/statistics',headers=admin_headers)).json()
        assert stats['statuses']['proposed']==1
        new=await client.post(endpoint+'/runs/approve-safe',headers=admin_headers,json={'limit':10})
        assert new.status_code==202
        await execute_run(db,uuid.UUID(new.json()['id']))
        assert (await db.scalar(select(Proposal))).status=='approved'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_legacy_enriched_product_is_protected(client):
    from app.modules.catalog_bulk.service import approve_safe as old_approve
    from app.modules.catalog_enrichment.service import bootstrap_taxonomy,propose_chunk,approve_safe_chunk as old_approve_chunk,apply_chunk as old_apply_chunk
    gen,db=await getdb()
    try:
        r=await seed_feed(db,'rossko',[('555','SG101','Наконечник рулевой тяги левый','s')])
        await scan_batch(db,r);await old_approve(db,r,10);await materialize_approved(db,limit=10,batch_id=r)
        await bootstrap_taxonomy(db);await propose_chunk(db,r,None,10);await old_approve_chunk(db,r,10)
        assert (await old_apply_chunk(db,r,10))['applied']==1
        product=await db.scalar(select(Product));original=product.name
        await bootstrap(db);await analyze_chunk(db,None,10)
        p=await db.scalar(select(Proposal))
        assert p.status=='review' and 'legacy_enrichment_preserved' in p.conflicts
        assert (await apply_chunk(db,10))['affected']==0
        assert (await db.get(Product,product.id)).name==original
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_stale_source_blocks_candidate_approval(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('GOODWILL','OG803','Фильтр масляный','sku')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,10)
        offer=await db.scalar(select(FeedOffer))
        offer.description='Фильтр воздушный'  # supplier corrected the file evidence
        await db.commit()
        decision=await approve_safe_chunk(db,10)
        assert decision['affected']==0 and decision['reviewed']==1
        p=await db.scalar(select(Proposal))
        assert p.status=='review' and p.review_note.startswith('Supplier evidence changed')
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_source_change_after_approval_cannot_prepare_draft(client):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('GOODWILL','OG803','Фильтр масляный','sku')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,10)
        assert (await approve_safe_chunk(db,10))['affected']==1
        offer=await db.scalar(select(FeedOffer));offer.description='Фильтр воздушный';await db.commit()
        prepare=await prepare_drafts_chunk(db,10)
        assert prepare['affected']==0 and prepare['reviewed']==1
        p=await db.scalar(select(Proposal));candidate=await db.scalar(select(CatalogCandidate))
        assert p.status=='review' and candidate.status=='new'
    finally:await gen.aclose()
