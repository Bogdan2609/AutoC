"""v0.31.1 admin-only semantic diagnosis and individually authorized draft repair.

No batch Product changes, publication, supplier purchase, auto-adoption, or
modifications to legacy/unmanaged cards. A repair requires an exact state token.
"""
from __future__ import annotations
from collections import Counter
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate
from app.modules.catalog_ext.models import Category, ProductCategory
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal
from app.modules.multi_enrichment.service import _page_sources
from app.modules.multi_enrichment.classifier import reconcile, RULE_VERSION
from app.modules.multi_enrichment.taxonomy_db import snapshot, TaxonomyRevision
from app.modules.multi_enrichment.semantic_guard import source_checks
from .models import DraftPreflight as Preflight, DraftAudit as Audit, utcnow

router=APIRouter(prefix='/api/v1/admin/draft-materialization/quality-guard',
                 tags=['catalog-semantic-quality'],dependencies=[Depends(require_admin)])


def describe(p,product,preflight):
    source=p.evidence.get('sources') or []
    check=source_checks(p.category_code,[s.get('description','') for s in source])
    return {'product_id':str(product.id),'candidate_id':str(preflight.candidate_id),
            'proposal_id':str(p.id),'name':product.name,'category_code':p.category_code,
            'status':product.status,'seo_noindex':product.seo_noindex,
            'semantic':check,'review_required':bool(check['issues']),
            'source_descriptions':[{k:s.get(k) for k in ('supplier','description','batch_id','source_line')} for s in source]}


@router.get('/drafts/suspects')
async def draft_suspects(limit:int=Query(default=1000,ge=1,le=20000),
                         db:AsyncSession=Depends(get_session)):
    # Read only. Never report unreviewed/previously existing drafts as safe.
    rows=(await db.execute(select(Preflight,Proposal,Product)
        .join(Proposal,Proposal.id==Preflight.proposal_id)
        .join(Product,Product.id==Preflight.product_id)
        .where(Preflight.status=='materialized', Product.status=='draft')
        .order_by(Preflight.candidate_id).limit(limit))).all()
    findings=[describe(p,product,pf) for pf,p,product in rows]
    suspects=[x for x in findings if x['review_required']]
    return {'scanned':len(findings),'suspects':len(suspects),'items':suspects,
            'notice':'Sample is limited. Unknown noun heads are not proof of correctness.',
            'auto_publish':False}


@router.get('/drafts/missing-primary')
async def missing_primary(limit:int=Query(default=100,ge=1,le=500),
                          db:AsyncSession=Depends(get_session)):
    ids=(await db.scalars(select(Product.id).where(Product.status=='draft',
        ~select(ProductCategory.id).where(ProductCategory.product_id==Product.id,
            ProductCategory.is_primary==True).exists()).order_by(Product.id).limit(limit))).all()
    return {'count':len(ids),'product_ids':[str(x) for x in ids],
            'notice':'No auto-category assignment from brand/article or generic descriptions.'}


class RepairRequest(BaseModel):
    expected_name:str=Field(min_length=3,max_length=300)
    expected_category_code:str=Field(min_length=3,max_length=80)
    expected_revision:int=Field(ge=1)
    confirm_draft_only:bool


def provenance(sources):
    keys=('supplier','batch_id','source_line','description','supplier_sku',
          'minimum','multiple','description_variants','sku_variants')
    return sorted(tuple(str(s.get(key)) for key in keys) for s in sources)


@router.post('/drafts/{product_id}/repair')
async def repair_draft(product_id:UUID,body:RepairRequest,db:AsyncSession=Depends(get_session)):
    if not body.confirm_draft_only:raise HTTPException(422,'Draft-only confirmation required')
    try:
        # Lock revision first, serializing with operator edits to taxonomy rules.
        rev=await db.scalar(select(TaxonomyRevision).where(TaxonomyRevision.id==1).with_for_update())
        if not rev or rev.revision!=body.expected_revision:raise HTTPException(409,'Rules revision changed')
        product=await db.scalar(select(Product).where(Product.id==product_id).with_for_update())
        if not product or product.status!='draft' or not product.seo_noindex:
            raise HTTPException(409,'Only indexed-disabled existing drafts may be repaired')
        pf=await db.scalar(select(Preflight).where(Preflight.product_id==product_id,
                          Preflight.status=='materialized').with_for_update())
        if not pf:raise HTTPException(409,'Legacy or manually maintained product is not auto-repairable')
        p=await db.scalar(select(Proposal).where(Proposal.id==pf.proposal_id).with_for_update())
        c=await db.scalar(select(Candidate).where(Candidate.id==pf.candidate_id).with_for_update())
        if not p or not c or p.status!='applied' or p.product_id!=product_id or c.product_id!=product_id:
            raise HTTPException(409,'Catalog identity/proposal is not an applied draft')
        if product.name!=body.expected_name or p.title!=body.expected_name or p.category_code!=body.expected_category_code:
            raise HTTPException(409,'Name/category changed since preview; do not overwrite operator edits')
        if not await db.scalar(select(ProductChangeLog.id).where(ProductChangeLog.product_id==product_id,
                          ProductChangeLog.action=='draft_materialize_v031')):
            raise HTTPException(409,'Draft creation audit missing')
        if await db.scalar(select(ProductChangeLog.id).where(ProductChangeLog.product_id==product_id,
                          ProductChangeLog.action!='draft_materialize_v031')):
            raise HTTPException(409,'Product has subsequent changes; require manual review')
        primary=(await db.scalars(select(ProductCategory).where(ProductCategory.product_id==product_id,
                         ProductCategory.is_primary==True).with_for_update())).all()
        if len(primary)!=1 or primary[0].category_id!=p.category_id:
            raise HTTPException(409,'Primary category differs; manual review required')
        book=await snapshot(db)
        old=p.evidence.get('sources') or []
        fresh=(await _page_sources(db,[c.id])).get(c.id,[])
        if not old or provenance(old)!=provenance(fresh):
            raise HTTPException(409,'Supplier evidence changed since original materialization')
        result=reconcile(c.sample_brand,c.sample_article,fresh,book)
        if result.conflicts or not result.category_code or result.category_code==p.category_code:
            raise HTTPException(409,'Not an unambiguous changed category; check review queue')
        semantic=source_checks(result.category_code,[s['description'] for s in fresh])
        if semantic['issues'] or semantic['coverage']!=len(fresh):
            raise HTTPException(409,'Supplier head evidence insufficient for unattended category change')
        if result.attributes!=p.attributes:
            raise HTTPException(409,'Physical attributes would change; manual review required')
        category=await db.scalar(select(Category).where(Category.name==result.category_name))
        if not category or result.quality<85:
            raise HTTPException(409,'Verified replacement category/quality unavailable')
        before={'title':p.title,'category_code':p.category_code,'category_id':str(p.category_id),
                'rule_revision':p.evidence.get('taxonomy_revision')}
        product.name=result.title
        primary[0].category_id=category.id
        p.title=result.title;p.category_code=result.category_code;p.category_id=category.id
        p.fingerprint=result.fingerprint;p.evidence=result.evidence;p.conflicts=[];p.quality=result.quality
        p.rule_version=RULE_VERSION;p.updated_at=utcnow()
        pf.fingerprint=result.fingerprint;pf.rule_revision=book.revision;pf.rule_version=RULE_VERSION
        pf.updated_at=utcnow()
        db.add(ProductChangeLog(product_id=product_id,action='semantic_draft_repair_v0311',
            changes={'before':before,'after':{'title':result.title,'category_code':result.category_code,
                     'rule_revision':book.revision},'source_provenance':provenance(fresh),
                     'publication':'unchanged_draft'}))
        db.add(Audit(candidate_id=c.id,proposal_id=p.id,event='semantic_draft_repair_v0311',
                     details={'product_id':str(product_id),'before':before,
                              'after_category':result.category_code,'rule_revision':book.revision}))
        await db.commit()
        return {'product_id':str(product_id),'old_category':before['category_code'],
                'new_category':result.category_code,'old_title':before['title'],
                'new_title':result.title,'status':'draft','seo_noindex':True,'published':False}
    except Exception:
        await db.rollback()
        raise
