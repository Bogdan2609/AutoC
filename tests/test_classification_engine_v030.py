"""v0.30: real supplier phrases, database-controlled rules, revision and no publication."""
from uuid import UUID
import pytest
from sqlalchemy import select, func
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Product
from app.modules.catalog_bulk.service import scan_batch
from app.modules.multi_enrichment.service import bootstrap,analyze_chunk,execute_run
from app.modules.multi_enrichment.models import MultiSourceProposal
from app.modules.multi_enrichment.taxonomy_db import (snapshot,TaxonomyPattern,TaxonomyType,
    TaxonomyRevision,TaxonomyAudit)
from app.modules.multi_enrichment.classifier import classify_source,reconcile
from tests.test_catalog_bulk_v027 import seed_feed

ROOT='/api/v1/admin/multi-enrichment'

async def db_session():
    gen=app.dependency_overrides[get_session]()
    return gen,await anext(gen)


@pytest.mark.asyncio
async def test_bootstrap_idempotent_inactive_categories_and_registry(client):
    gen,db=await db_session()
    try:
        first=await bootstrap(db)
        book=await snapshot(db)
        assert first['rule_revision']==1
        assert len(book.types)>100
        assert len(book.patterns)>110
        assert (await bootstrap(db))['rule_revision']==1
        assert await db.scalar(select(func.count()).select_from(TaxonomyAudit))==1
        assert (await client.get(ROOT+'/taxonomy',headers={'X-Admin-Token':'test-token'})).json()['rule_version']=='0.30.0'
        from app.modules.catalog_ext.models import Category
        children=(await db.scalars(select(Category).where(Category.slug.like('auto-v030-%')))).all()
        assert children and all(not c.active for c in children)
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.parametrize(('description','expected'),[
    ('Датчик положения коленвала (ИНО) AUDI A3 II','crankshaft_position_sensor'),
    ('Датчик положения коленчатого вала Nissan','crankshaft_position_sensor'),
    ('Датчик положения распредвала Opel','camshaft_position_sensor'),
    ('Прокладка крышки клапанов Nissan','valve_cover_gasket'),
    ('Прокладка масляного картера двигателя','engine_oil_pan_gasket'),
    ('Кольца поршневые 1.5mm','piston_rings'),
    ('Вкладыши коренные','main_bearings'),
    ('Натяжитель ремня (ИНО) HONDA','drive_belt_tensioner'),
    ('Пружина ходовой передняя','suspension_spring'),
    ('Ремкомплект суппорта | зад |','brake_caliper_repair_kit'),
    ('Насос гидроусилителя Toyota Avensis','power_steering_pump'),
    ('Цилиндр сцеп. Hyundai Elantra (главн.)','clutch_master_cylinder'),
    ('Гофра глушителя 55x280','exhaust_flex'),
    ('Масло моторное синт. 5W30','engine_oil'),
    ('Стеклоподъемник электр. передний правый','window_regulator'),
    ('Клипса обшивки, молдинга (пластик)','trim_fastener'),
    ('Звездочка коленчатого вала','crankshaft_gear'),
    ('Сайлентблок рычага подвески','arm_bushing'),
    ('Сайлентблок рамы','suspension_frame_bushing'),
])
@pytest.mark.asyncio
async def test_real_text_registry_classification(client,description,expected):
    gen,db=await db_session()
    try:
        await bootstrap(db)
        book=await snapshot(db)
        p=classify_source(description,book,'rossko')
        assert p['category_code']==expected, (description,p['type_candidates'],p['matched_rules'])
        assert 'multiple_item_types' not in p['flags'],p
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_cross_supplier_identity_conflict_not_suppressed(client):
    gen,db=await db_session()
    try:
        await bootstrap(db);book=await snapshot(db)
        sources=[{'supplier':'rossko','description':'Сайлентблок рычага подвески','batch_id':'a','source_line':1},
                 {'supplier':'partkom','description':'Сайлентблок рамы','batch_id':'b','source_line':2}]
        result=reconcile('JIKIU','YF22038',sources,book)
        assert 'source_category_conflict' in result.conflicts
        assert result.category_code is None
        assert result.evidence['taxonomy_revision']==1
        assert result.evidence['supplier_count']==2
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_admin_rule_create_preview_disable_audit_and_optimistic_revision(client,admin_headers):
    gen,db=await db_session()
    try:
        await bootstrap(db)
        body={'key':'operator_valve_position_rule','type_code':'camshaft_position_sensor',
              'positive_regex':r'\bдатчик\s+дпrv\b','supplier':'rossko',
              'requires_review':False,'expected_revision':1,
              'reason':'Верифицировано описание датчика производителя'}
        create=await client.post(ROOT+'/taxonomy/patterns',headers=admin_headers,json=body)
        assert create.status_code==201,create.text
        assert create.json()['rule_revision']==2
        stale=await client.post(ROOT+'/taxonomy/patterns',headers=admin_headers,json={**body,'key':'next_rule'})
        assert stale.status_code==409
        test=await client.post(ROOT+'/taxonomy/test',headers=admin_headers,
            json={'description':'Датчик ДПRV','supplier':'rossko'})
        assert test.status_code==200 and test.json()['result']['category_code']=='camshaft_position_sensor'
        other=await client.post(ROOT+'/taxonomy/test',headers=admin_headers,
            json={'description':'Датчик ДПRV','supplier':'partkom'})
        assert other.json()['result']['category_code'] is None
        disabled=await client.patch(ROOT+'/taxonomy/patterns/operator_valve_position_rule',
            headers=admin_headers,json={'active':False,'expected_revision':2,
                                        'reason':'Проверка отключения правила в БД'})
        assert disabled.status_code==200,disabled.text
        assert disabled.json()['rule_revision']==3
        test=await client.post(ROOT+'/taxonomy/test',headers=admin_headers,
            json={'description':'Датчик ДПRV','supplier':'rossko'})
        assert test.json()['result']['category_code'] is None
        actions=(await db.scalars(select(TaxonomyAudit).order_by(TaxonomyAudit.revision))).all()
        assert [a.action for a in actions]==['bootstrap','pattern_create','pattern_update']
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_old_proposal_stale_after_rulebook_update_and_no_draft_written(client,admin_headers):
    gen,db=await db_session()
    try:
        batch=await seed_feed(db,'rossko',[('BM','AB123','Диск тормозной','sku')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,10)
        p=await db.scalar(select(MultiSourceProposal))
        assert p.status=='proposed' and p.evidence['taxonomy_revision']==1
        change=await client.patch(ROOT+'/taxonomy/patterns/legacy_brake_disc',headers=admin_headers,
            json={'active':False,'expected_revision':1,'reason':'Контрольная проверка инвалидирования анализа'})
        assert change.status_code==200,change.text
        manual=await client.post(f'{ROOT}/proposals/{p.id}/decision',headers=admin_headers,
            json={'decision':'approved','note':'Проверено вручную'})
        assert manual.status_code==409
        assert (await db.get(MultiSourceProposal,p.id)).status=='proposed'
        assert await db.scalar(select(func.count()).select_from(Product))==0
        await analyze_chunk(db,None,10)
        q=await db.get(MultiSourceProposal,p.id)
        assert q.evidence['taxonomy_revision']==2 and q.status=='review'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_job_revision_is_pinned_and_aborts_after_admin_rule_change(client,admin_headers):
    gen,db=await db_session()
    try:
        batch=await seed_feed(db,'partkom',[('A','321','Фильтр масляный','a')])
        await scan_batch(db,batch);await bootstrap(db)
        queued=await client.post(ROOT+'/runs/analyze',headers=admin_headers,json={'limit':10})
        assert queued.status_code==202
        create=await client.post(ROOT+'/taxonomy/patterns',headers=admin_headers,json={
            'key':'some_oil_filter_other_rule','type_code':'oil_filter',
            'positive_regex':r'^маслофильтр\b','requires_review':True,
            'expected_revision':1,'reason':'Тестирование отмены устаревшей ревизии'})
        assert create.status_code==201
        with pytest.raises(RuntimeError,match='Classification rules changed'):
            await execute_run(db,UUID(queued.json()['id']))
        assert await db.scalar(select(func.count()).select_from(MultiSourceProposal))==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_suspicious_rule_is_rejected(client,admin_headers):
    gen,db=await db_session()
    try:
        await bootstrap(db)
        resp=await client.post(ROOT+'/taxonomy/patterns',headers=admin_headers,json={
            'key':'bad_regex','type_code':'oil_filter','positive_regex':r'(a+)+b',
            'expected_revision':1,'reason':'Регулярное выражение небезопасно'})
        assert resp.status_code==422,resp.text
        assert (await snapshot(db)).revision==1
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_operator_group_type_inactive_category_and_manual_rules(client,admin_headers):
    from app.modules.catalog_ext.models import Category
    gen,db=await db_session()
    try:
        await bootstrap(db)
        group=await client.post(ROOT+'/taxonomy/groups',headers=admin_headers,json={
            'code':'custom_parts','name':'Специализированные детали',
            'expected_revision':1,'reason':'Создание отдельной группы для новых деталей'})
        assert group.status_code==201,group.text
        new_type=await client.post(ROOT+'/taxonomy/types',headers=admin_headers,json={
            'code':'operator_verified_item','group_code':'custom_parts',
            'category_name':'Специализированный узел','title_stem':'Узел специальный',
            'expected_revision':2,'reason':'Ввод подтвержденного оператором типа детали'})
        assert new_type.status_code==201,new_type.text
        category=await db.scalar(select(Category).where(Category.slug=='auto-v030-operator_verified_item'))
        parent=await db.get(Category,category.parent_id)
        assert category.active is False and parent.active is False
        assert parent.slug=='auto-v030-group-custom_parts'
        create=await client.post(ROOT+'/taxonomy/patterns',headers=admin_headers,json={
            'key':'special_operator_type','type_code':'operator_verified_item',
            'positive_regex':r'^спецузел\s+комплектация\s+а\b',
            'requires_review':True,'expected_revision':3,
            'reason':'Проверяем отдельный тип и состояние на модерации'})
        assert create.status_code==201,create.text
        preview=await client.post(ROOT+'/taxonomy/test',headers=admin_headers,json={
            'description':'Спецузел комплектация А','supplier':'rossko'})
        assert preview.json()['result']['category_code']=='operator_verified_item'
        assert 'classification_rule_requires_review' in preview.json()['result']['flags']
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()
