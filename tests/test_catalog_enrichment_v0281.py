"""v0.28.1 rule regression, additive taxonomy upgrade, and no re-application."""
import uuid
import pytest
from sqlalchemy import func, select
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product
from app.modules.catalog_bulk.models import CatalogCandidate
from app.modules.catalog_ext.models import AttributeDefinition, ProductAttribute, ProductCategory
from app.modules.catalog_enrichment.models import EnrichmentProposal as Proposal, EnrichmentDecisionAudit as Audit
from app.modules.catalog_enrichment.rules import ENGINE_VERSION, suggest
from app.modules.catalog_enrichment.service import bootstrap_taxonomy, propose_chunk, approve_safe_chunk, apply_chunk
from tests.test_catalog_enrichment_v028 import materialized_fixture, session_from_fixture

@pytest.mark.parametrize('name,code,stem', [
    ('Опора шаровая','ball_joint','Опора шаровая'),
    ('Шаровая опора','ball_joint','Опора шаровая'),
    ('Рычаг маятниковый','idler_arm','Рычаг маятниковый'),
    ('Втулка стабилизатора','stabilizer_bushing','Втулка стабилизатора'),
    ('Опора амортизатора','strut_mount','Опора амортизатора'),
    ('Ступица колеса','wheel_hub','Ступица колеса'),
    ('Опора двигателя','engine_mount','Опора двигателя'),
    ('Ремень ГРМ','timing_belt','Ремень ГРМ'),
    ('Термостат','thermostat','Термостат'),
    ('ШРУС','cv_joint','ШРУС'),
    ('Суппорт тормозной','brake_caliper','Суппорт тормозной'),
])
def test_new_taxonomy_classes_are_exact(name, code, stem):
    res=suggest('4U','ART001',name)
    assert res.category_code==code
    assert res.title==f'{stem} 4U ART001'
    assert res.flags==()


def test_dual_side_explicit_not_conflict():
    x=suggest('555','SLN010','Стойка стабилизатора | перед прав/лев |')
    assert x.category_code=='stabilizer_link'
    assert x.attributes=={'installation_side':'обе стороны','installation_position':'передний'}
    assert x.title=='Стойка стабилизатора передняя левая/правая 555 SLN010'
    assert not x.flags
    y=suggest('555','SE6233','Наконечник рулевой тяги | лев/прав |')
    assert y.title=='Наконечник рулевой тяги левый/правый 555 SE6233'
    assert 'side_conflict' not in y.flags
    ambiguous=suggest('555','SE6233','Наконечник рулевой тяги | лев справа прав |')
    assert 'side_conflict' in ambiguous.flags
    assert 'installation_side' not in ambiguous.attributes
    mixed=suggest('555','SE6233','Наконечник рулевой тяги прав/лев и отдельно прав')
    assert 'side_conflict' in mixed.flags


def test_exact_trailing_brand_not_duplicated_or_erased():
    x=suggest('555','SES193R','Наконечник рулевой тяги 555')
    assert x.title=='Наконечник рулевой тяги 555 SES193R'
    assert not x.flags
    assert suggest('4U','A1','Рычаг подвески 4U').title=='Рычаг подвески 4U A1'
    assert 'unparsed_qualifiers' in suggest('555','A2','Наконечник рулевой тяги 5550').flags
    assert '2555' in suggest('555','A3','Наконечник рулевой тяги 2555').title


@pytest.mark.asyncio
async def test_existing_side_enum_extended_only_on_bootstrap(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Стойка стабилизатора перед прав/лев'])
        await bootstrap_taxonomy(db)
        attr=await db.scalar(select(AttributeDefinition).where(AttributeDefinition.code=='installation_side'))
        attr.enum_values=['левый','правый']  # Simulate v0.28's installed enum values
        await db.commit()
        await propose_chunk(db,bid,None,10)
        assert (await db.scalar(select(Proposal))).status=='proposed'
        assert 'обе стороны' not in (await db.get(AttributeDefinition,attr.id)).enum_values
        result=await bootstrap_taxonomy(db)
        await db.refresh(attr)
        assert attr.enum_values==['левый','правый','обе стороны']
        assert (await bootstrap_taxonomy(db))['created_categories']==[]
        assert attr.enum_values==['левый','правый','обе стороны']
        assert result['auto_publish'] is False
        assert await approve_safe_chunk(db,bid,10)==1
        assert (await apply_chunk(db,bid,10))['applied']==1
        prod=await db.scalar(select(Product))
        assert prod.status=='draft' and prod.seo_noindex
        props=(await db.scalars(select(ProductAttribute))).all()
        assert any(p.value_enum=='обе стороны' for p in props)
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_rescan_preserves_applied_changes_and_manual_reviews(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Наконечник рулевой тяги | прав |',
                                            'Стойка стабилизатора | перед прав/лев |',
                                            'Рычаг маятниковый'])
        await bootstrap_taxonomy(db)
        await propose_chunk(db,bid,None,100)
        props=(await db.scalars(select(Proposal).order_by(Proposal.id))).all()
        applied_proposal=next(p for p in props if p.category_code=='tie_rod_end')
        reviewed_proposal=next(p for p in props if p.category_code=='stabilizer_link')
        applied_proposal.status='approved'
        reviewed_proposal.status='review'
        db.add(Audit(proposal_id=reviewed_proposal.id,old_status='proposed',new_status='review',origin='admin_api',note='Check by editor'))
        await db.commit()
        assert (await apply_chunk(db,bid,10))['applied']==1
        await db.refresh(applied_proposal)
        saved_title=applied_proposal.title
        applied_proposal.rule_version='0.28.1'  # Installed row from prior version
        reviewed_proposal.rule_version='0.28.1'
        await db.commit()
        assert (await propose_chunk(db,bid,None,100))['seen']==3
        await db.refresh(applied_proposal)
        await db.refresh(reviewed_proposal)
        assert applied_proposal.status=='applied' and applied_proposal.title==saved_title
        assert reviewed_proposal.status=='review' and reviewed_proposal.rule_version=='0.28.1'
        product=await db.get(Product,applied_proposal.product_id)
        assert product.name==saved_title
        assert product.status=='draft' and product.seo_noindex
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_old_approved_rule_cannot_apply_without_reapproval(client):
    gen,db=await session_from_fixture()
    try:
        bid=await materialized_fixture(db,['Наконечник рулевой тяги | прав |'])
        await bootstrap_taxonomy(db)
        await propose_chunk(db,bid,None,10)
        p=await db.scalar(select(Proposal))
        p.rule_version='0.28.1'
        p.status='approved'
        await db.commit()
        result=await apply_chunk(db,bid,10)
        assert result['applied']==0 and result['reviewed']==1
        product=await db.get(Product,p.product_id)
        assert product.name=='4U CODE0'
        assert product.status=='draft'
    finally:
        await gen.aclose()


@pytest.mark.parametrize('raw,category', [
    ('Наконечник рул. тяги','tie_rod_end'),
    ('Тяга рул.','tie_rod'),
    ('Опора шар.','ball_joint'),
    ('Рычаг маятн.','idler_arm'),
])
def test_supplier_common_abbreviations(raw,category):
    result=suggest('555','CODE9',raw)
    assert result.category_code==category
    assert not result.flags
    assert result.title.endswith('555 CODE9')
