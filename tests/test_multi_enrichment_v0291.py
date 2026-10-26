"""v0.29.1: regression cases taken from user-supplied ROSSKO/PART-KOM samples."""
import uuid
import pytest
from sqlalchemy import select, func
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Product
from app.modules.catalog_bulk.service import scan_batch, materialize_approved
from app.modules.catalog_bulk.models import CatalogCandidate
from app.modules.catalog_enrichment.service import bootstrap_taxonomy, propose_chunk, approve_safe_chunk as old_approve_chunk, apply_chunk as old_apply_chunk
from app.modules.catalog_bulk.service import approve_safe as old_approve
from app.modules.multi_enrichment.classifier import classify_source, reconcile, RULE_VERSION
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal, MultiSourceDecision as Decision
from app.modules.multi_enrichment.service import bootstrap, analyze_chunk, approve_safe_chunk, apply_chunk
from tests.test_catalog_bulk_v027 import seed_feed


def source(supplier, description):
    return {'supplier':supplier,'description':description,'batch_id':str(uuid.uuid4()),
            'source_line':1,'description_variants':1,'sku_variants':1}


@pytest.mark.parametrize(('description','code'),[
    ('Подушка двигателя передняя лев. Chevrolet Espero/Nexia/Lanos 90250437','engine_mount'),
    ('Датчик кисл. для а/м Chevrolet Aveo (11-) 1.6i после кат.','oxygen_sensor'),
    ('Колодки торм. перед.','brake_pads'),
    ('Пружина подвески CHEVROLET LACETTI перед.','suspension_spring'),
    ('Суппорт торм. пер. лев. со скоб. Toyota','brake_caliper'),
    ('Втулка переднего стабилизатора D25','stabilizer_bushing'),
    ('Стойка амортизационная | перед лев |','strut'),
    ('Картридж турбокомпрессора','turbocharger_cartridge'),
    ('Лампа светодиодная LED W5W T10 12V','automotive_lamp'),
    ('Прокладка коллектора впускного к-кт Mitsubishi','intake_manifold_gasket'),
    ('Сайлентблок рамы','suspension_frame_bushing'),
    ('Сайлентблок рычага подвески','arm_bushing'),
    ('Сайлентблок подрамника','subframe_bushing'),
    ('Тяга рулевая','tie_rod'),
    ('Фильтр воздушный','air_filter'),
    ('Комплект сцепления','clutch_kit'),
])
def test_review_examples_and_unchanged_categories(description,code):
    r=classify_source(description)
    assert r['category_code']==code,(description,r)
    assert 'multiple_item_types' not in r['flags'],(description,r)


def test_bare_pads_classified_but_cannot_auto_approve():
    r=classify_source('Колодки передние')
    assert r['category_code']=='brake_pads'
    assert 'generic_pad_type_review' in r['flags']
    assert reconcile('FINWHALE','V1104',[source('partkom','Колодки передние')]).quality<85


def test_fastenings_do_not_override_primary_type():
    x=classify_source('Стойка стабилизатора с гайками перед прав')
    assert x['category_code']=='stabilizer_link'
    assert 'execution_or_kit_review' in x['flags']
    assert classify_source('Гайка')['category_code']=='fastener_nut'
    assert 'generic_fastener_review' in classify_source('Гайка')['flags']


def test_packaging_reconciled_with_supplier_provenance():
    a=source('rossko','Лампа светодиодная LED W5W 2шт')
    b=source('partkom','Лампа светодиодная LED W5W 4шт')
    out=reconcile('XENITE','1009690',[a,b])
    assert 'source_pack_units_conflict' in out.conflicts
    assert out.evidence['qualifier_claims']['pack_units']=={'partkom':4,'rossko':2}
    assert out.quality<85
    good=reconcile('XENITE','1009690',[a,source('partkom','Лампа светодиодная LED W5W')])
    assert 'source_pack_units_conflict' not in good.conflicts # missing != opposite
    assert good.evidence['qualifier_claims']['pack_units']=={'rossko':2}


def test_true_conflicting_silent_blocks_not_auto_approved():
    out=reconcile('JIKIU','YF22038',[source('rossko','Сайлентблок рычага подвески'),
                                      source('partkom','Сайлентблок рамы')])
    assert 'source_category_conflict' in out.conflicts
    assert out.category_code is None


def test_equivalent_source_abbreviation_becomes_single_type():
    out=reconcile('Trialli','PF2393',[source('rossko','Колодки тормозные дисковые для автомобилей Lexus NX'),
                                     source('partkom','Колодки торм. Lexus NX диск. зад.')])
    assert out.category_code=='brake_pads'
    assert 'source_type_incomplete' not in out.conflicts
    assert 'source_category_conflict' not in out.conflicts
    assert not out.conflicts


def test_real_description_variation_does_not_auto_dismiss_execution():
    out=reconcile('Stellox','1126259SX',[source('partkom','Прокладка коллектора впускного к-кт Mitsubishi ASX'),
                                          source('rossko','Прокладка коллект.впуск.к-кт Mitsubishi')])
    assert out.category_code=='intake_manifold_gasket'
    assert 'execution_or_kit_review' in out.conflicts
    assert 'source_category_conflict' not in out.conflicts


def test_ambiguous_and_discontinued_still_review():
    p=reconcile('555','SL4745',[source('rossko','Стойка стабилизатора перед/зад прав/лев')])
    assert 'ambiguous_position' in p.conflicts
    p=reconcile('555','SEI2472',[source('rossko','Снят с производства наконечник рулевой тяги')])
    assert 'discontinued' in p.conflicts
    p=reconcile('3ton','40216',[source('rossko','Жидкость для розжига')])
    assert 'non_automotive_review' in p.conflicts


async def getdb():
    gen=app.dependency_overrides[get_session]()
    return gen, await anext(gen)


@pytest.mark.asyncio
async def test_existing_review_recomputed_from_v029_to_v0291_and_idempotent(client,admin_headers):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'partkom',[('KORTEX','KSS015','Пружина подвески перед.','s1')])
        await scan_batch(db,batch);await bootstrap(db)
        await analyze_chunk(db,None,10)
        p=await db.scalar(select(Proposal));assert p.status=='proposed'
        # Mimic existing older, inadequate proposal persisted in the real database.
        p.rule_version='0.29.0';p.status='review';p.category_code=None;p.category_id=None
        p.conflicts=['unclassified'];p.review_note='old rule unclassified'
        await db.commit()
        updated=await analyze_chunk(db,None,10)
        assert updated['affected']==1
        new=await db.get(Proposal,p.id)
        assert new.status=='proposed' and new.rule_version==RULE_VERSION
        assert new.category_code=='suspension_spring' and not new.conflicts
        assert new.review_note==''
        assert (await analyze_chunk(db,None,10))['affected']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_manual_review_decisions_not_overwritten_by_reanalysis(client,admin_headers):
    gen,db=await getdb()
    try:
        batch=await seed_feed(db,'rossko',[('JIKIU','YF22038','Сайлентблок рамы','sku')])
        await scan_batch(db,batch);await bootstrap(db);await analyze_chunk(db,None,10)
        p=await db.scalar(select(Proposal))
        resp=await client.post(f'/api/v1/admin/multi-enrichment/proposals/{p.id}/decision',
            headers=admin_headers,json={'decision':'review','note':'Проверить исполнение у производителя'})
        assert resp.status_code==200
        p=await db.get(Proposal,p.id);p.rule_version='0.29.0';await db.commit()
        assert (await analyze_chunk(db,None,10))['affected']==0
        assert (await db.get(Proposal,p.id)).rule_version=='0.29.0'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_legacy_product_immutable_across_v0291_reanalysis(client):
    gen,db=await getdb()
    try:
        r=await seed_feed(db,'rossko',[('555','SG101','Наконечник рулевой тяги левый','s')])
        await scan_batch(db,r);await old_approve(db,r,10);await materialize_approved(db,limit=10,batch_id=r)
        await bootstrap_taxonomy(db);await propose_chunk(db,r,None,10);await old_approve_chunk(db,r,10)
        assert (await old_apply_chunk(db,r,10))['applied']==1
        product=await db.scalar(select(Product));original=product.name
        await bootstrap(db);await analyze_chunk(db,None,10)
        p=await db.scalar(select(Proposal))
        assert 'legacy_enrichment_preserved' in p.conflicts
        p.rule_version='0.29.0';await db.commit()
        await analyze_chunk(db,None,10)
        assert (await db.get(Proposal,p.id)).rule_version==RULE_VERSION
        assert (await db.get(Product,product.id)).name==original
        assert (await apply_chunk(db,10))['affected']==0
        assert (await db.get(Product,product.id)).status=='draft'
    finally:await gen.aclose()
