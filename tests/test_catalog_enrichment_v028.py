"""v0.28 integration and invariants on an isolated temporary DB."""
import uuid
import pytest
from sqlalchemy import func, select
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_bulk.service import scan_batch, approve_safe, materialize_approved
from app.modules.catalog_ext.models import ProductCategory, ProductAttribute, AttributeDefinition
from app.modules.catalog_enrichment.rules import suggest
from app.modules.catalog_enrichment.models import EnrichmentProposal as Proposal, EnrichmentRun as Run
from app.modules.catalog_enrichment.service import (bootstrap_taxonomy,propose_chunk,
    approve_safe_chunk,apply_chunk,execute_run)
from tests.test_catalog_bulk_v027 import seed_feed


async def session_from_fixture():
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    return gen,db


async def materialized_fixture(db, descriptions):
    bid=await seed_feed(db,'rossko',[("4U",f"CODE{i}",description,f"S{i}") for i,description in enumerate(descriptions)])
    await scan_batch(db,bid)
    await approve_safe(db,bid,limit=100)
    await materialize_approved(db,limit=100,batch_id=bid)
    return bid


def test_real_rossko_ru_title_and_structured_position():
    row=suggest('4U','JPA14371','Наконечник рулевой тяги | перед прав|')
    assert row.title=='Наконечник рулевой тяги передний правый 4U JPA14371'
    assert row.category_code=='tie_rod_end'
    assert row.attributes=={'installation_side':'правый','installation_position':'передний'}
    assert not row.flags and row.score>=85
    assert suggest('4U','AUP05501','Сайлентблок рычага подвески').category_code=='arm_bushing'
    assert suggest('4U','CDF02716','Стойка стаб.зад.левая | зад лев |').title=='Стойка стабилизатора задняя левая 4U CDF02716'
    assert 'installation_side' not in suggest('3ton','40216','Объем 0.5л').attributes


def test_non_auto_ambiguous_and_mojibake_requires_review():
    assert 'non_automotive_review' in suggest('3ton','40216','Жидкость для розжига Country 0.5л').flags
    assert 'conflicting_sources' in suggest('A','1','Рычаг подвески', ['Фильтр воздушный']).flags
    assert 'side_conflict' in suggest('B','2','Рычаг подвески лев прав').flags
    assert 'unclassified' in suggest('X','3','Товар непонятный').flags
    assert 'encoding_suspect' in suggest('B','4','Ð¡Ð°Ð¹').flags
    assert suggest('A','1','Рычаг подвески').provenance_hash==suggest('A','1','Рычаг подвески').provenance_hash


@pytest.mark.asyncio
async def test_rule_taxonomy_and_draft_apply_does_not_publish(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Наконечник рулевой тяги | перед прав|'])
        assert (await bootstrap_taxonomy(db))['auto_publish'] is False
        assert (await bootstrap_taxonomy(db))['created_categories']==[]
        result=await propose_chunk(db,bid,None,100)
        assert result['seen']==1 and result['proposed']==1 and not result['review']
        proposal=await db.scalar(select(Proposal))
        assert proposal.status=='proposed' and proposal.category_id is not None
        assert await approve_safe_chunk(db,bid,100)==1
        applied=await apply_chunk(db,bid,100)
        assert applied['applied']==1 and applied['reviewed']==0
        product=await db.get(Product,proposal.product_id)
        assert product.name=='Наконечник рулевой тяги передний правый 4U CODE0'
        assert product.status=='draft' and product.seo_noindex
        assert await db.scalar(select(func.count()).select_from(ProductCategory))==1
        assert await db.scalar(select(func.count()).select_from(ProductAttribute))==2
        assert await db.scalar(select(func.count()).select_from(ProductChangeLog))>=1
        assert (await apply_chunk(db,bid,100))['selected']==0
        assert (await propose_chunk(db,bid,None,100))['proposed']==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_conflicting_external_title_is_preserved(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Рычаг подвески | перед лев |'])
        await bootstrap_taxonomy(db)
        product=await db.scalar(select(Product))
        product.name='Название проверено вручную'
        await db.commit()
        result=await propose_chunk(db,bid,None,100)
        p=await db.scalar(select(Proposal))
        assert result['review']==1 and 'manual_title_preserved' in p.flags
        assert await approve_safe_chunk(db,bid,10)==0
        assert (await db.get(Product,product.id)).name=='Название проверено вручную'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_unconfigured_taxonomy_can_be_rescanned_without_losing_data(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Сайлентблок рычага подвески'])
        assert (await propose_chunk(db,bid,None,10))['review']==1
        p=await db.scalar(select(Proposal))
        assert 'category_unconfigured' in p.flags
        await bootstrap_taxonomy(db)
        assert (await propose_chunk(db,bid,None,10))['proposed']==1
        await db.refresh(p)
        assert p.category_id and p.status=='proposed' and p.flags==[]
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_enrichment_api_queue_job_execution_and_auth(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Рычаг подвески | перед лев|'])
        url='/api/v1/admin/catalog-enrichment'
        assert (await client.get(url+'/taxonomy')).status_code in (401,403)
        assert (await client.post(url+'/taxonomy/bootstrap',headers=admin_headers)).status_code==200
        preview=await client.get(url+'/preview',headers=admin_headers,params={
            'brand':'4U','article':'AB123','description':'Рычаг подвески перед лев'})
        assert preview.status_code==200 and 'передний левый' in preview.json()['title']
        r=await client.post(url+'/runs/propose',headers=admin_headers,
                            json={'batch_id':str(bid),'limit':100})
        assert r.status_code==202,r.text
        rid=uuid.UUID(r.json()['id'])
        await execute_run(db,rid)
        assert (await client.get(url+f'/runs/{rid}',headers=admin_headers)).json()['status']=='completed'
        props=(await client.get(url+'/proposals',headers=admin_headers,
                params={'batch_id':str(bid)})).json()['items']
        assert len(props)==1 and props[0]['status']=='proposed'
        approve=await client.post(url+'/runs/approve-safe',headers=admin_headers,
                                   json={'batch_id':str(bid),'limit':100})
        assert approve.status_code==202
        await execute_run(db,uuid.UUID(approve.json()['id']))
        apply=await client.post(url+'/runs/apply',headers=admin_headers,
                                 json={'batch_id':str(bid),'limit':100})
        assert apply.status_code==202
        await execute_run(db,uuid.UUID(apply.json()['id']))
        product=await db.scalar(select(Product))
        assert product.status=='draft' and product.seo_noindex
        assert product.name.startswith('Рычаг подвески передний левый')
        assert (await client.get(url+'/statistics',headers=admin_headers,
            params={'batch_id':str(bid)})).json()['statuses']['applied']==1
        assert (await client.get(url+'/runs',headers=admin_headers)).status_code==200
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_ambiguous_source_requires_manual_confirmation(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Рычаг подвески дополнительно уточнить'])
        await bootstrap_taxonomy(db)
        result=await propose_chunk(db,bid,None,50)
        assert result['review']==1
        p=await db.scalar(select(Proposal))
        url=f'/api/v1/admin/catalog-enrichment/proposals/{p.id}/decision'
        bad=await client.post(url,headers=admin_headers,json={'decision':'approved'})
        assert bad.status_code==409
        accept=await client.post(url,headers=admin_headers,json={
            'decision':'approved','confirm_ambiguous':True,
            'note':'Оператор сверил вариант исполнения с оригинальной карточкой'})
        assert accept.status_code==200,accept.text
        await db.refresh(p)
        assert (await apply_chunk(db,bid,10))['applied']==1
        hist=await client.get(f'/api/v1/admin/catalog-enrichment/proposals/{p.id}/decisions',headers=admin_headers)
        assert hist.status_code==200 and hist.json()['items'][0]['origin']=='worker'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_name_change_after_approval_prevents_application(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Сайлентблок рычага подвески'])
        await bootstrap_taxonomy(db)
        await propose_chunk(db,bid,None,20)
        assert await approve_safe_chunk(db,bid,20)==1
        product=await db.scalar(select(Product))
        product.name='Проверенное вручную описание'
        await db.commit()
        assert (await apply_chunk(db,bid,20))['reviewed']==1
        assert (await db.get(Product,product.id)).name=='Проверенное вручную описание'
        assert (await db.get(Product,product.id)).status=='draft'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_published_product_remains_untouched(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Сайлентблок рычага подвески'])
        product=await db.scalar(select(Product))
        product.status='published';product.seo_noindex=False
        await db.commit()
        await bootstrap_taxonomy(db)
        r=await propose_chunk(db,bid,None,20)
        assert r['proposed']==0
        assert (await db.get(Product,product.id)).status=='published'
        assert (await db.get(Product,product.id)).seo_noindex is False
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_duplicate_active_jobs_for_same_batch_blocked(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Рычаг подвески'])
        url='/api/v1/admin/catalog-enrichment/runs/'
        first=await client.post(url+'propose',headers=admin_headers,json={'batch_id':str(bid)})
        second=await client.post(url+'approve-safe',headers=admin_headers,json={'batch_id':str(bid)})
        assert first.status_code==202 and second.status_code==409
        await execute_run(db,uuid.UUID(first.json()['id']))
        third=await client.post(url+'approve-safe',headers=admin_headers,json={'batch_id':str(bid)})
        assert third.status_code==202
    finally:await gen.aclose()
