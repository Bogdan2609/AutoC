"""Bounded, resumable candidate-first enrichment; no product publication or supplier writes."""
from __future__ import annotations
import uuid
from uuid import UUID
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from app.modules.catalog.models import Product, ProductChangeLog
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate, CatalogCandidateBatch, CatalogDecisionAudit
from app.modules.catalog_bulk.service import suspicious
from app.modules.catalog_enrichment.models import EnrichmentProposal as LegacyProposal
from app.modules.catalog_enrichment.service import bootstrap_taxonomy, ATTRS
from app.modules.catalog_ext.models import Category, ProductCategory, AttributeDefinition, ProductAttribute
from app.modules.supplier_feeds.models import FeedBatch, FeedOffer
from .classifier import RULE_VERSION, TYPE_MAP, reconcile
from .taxonomy_db import bootstrap_rulebook, snapshot, TaxonomyRevision
from .models import MultiSourceProposal as Proposal, MultiSourceRun as Run, MultiSourceDecision as Audit, utcnow

CHUNK=200


def view(p):
    return {'id':str(p.id),'candidate_id':str(p.candidate_id),
            'product_id':str(p.product_id) if p.product_id else None,
            'title':p.title,'category_code':p.category_code,'category_id':str(p.category_id) if p.category_id else None,
            'attributes':p.attributes,'quality':p.quality,'conflicts':p.conflicts,
            'status':p.status,'rule_version':p.rule_version,'review_note':p.review_note,
            'evidence':p.evidence}


def run_view(r):
    return {'id':str(r.id),'kind':r.kind,'status':r.status,'affected':r.affected,
            'reviewed':r.reviewed,'cursor':r.parameters.get('cursor'),
            'message':r.message,'created_at':r.created_at.isoformat(),
            'finished_at':r.finished_at.isoformat() if r.finished_at else None}


async def bootstrap(db):
    """Install versioned classification rules and add missing inactive categories."""
    prior=await bootstrap_taxonomy(db)
    result=await bootstrap_rulebook(db,TYPE_MAP)
    result['created_categories_legacy']=prior['created_categories']
    return result


async def _page_sources(db, candidate_ids):
    """Use the actual imported offer line, once per supplier, never per warehouse.

    Indexed equality on (batch_id,brand_norm,article_norm); windowing across bounded candidate IDs.
    """
    ranked=(select(Candidate.id.label('candidate_id'), FeedBatch.supplier_code.label('supplier'),
                   FeedOffer.batch_id, FeedOffer.source_line, FeedOffer.description,
                   FeedOffer.supplier_sku, FeedOffer.minimum, FeedOffer.multiple,
                   CatalogCandidateBatch.description_variants, CatalogCandidateBatch.sku_variants,
                   func.row_number().over(partition_by=(Candidate.id,FeedBatch.supplier_code),
                     order_by=(FeedBatch.created_at.desc(),FeedOffer.source_line,FeedOffer.id)).label('rn'))
            .select_from(Candidate)
            .join(CatalogCandidateBatch,CatalogCandidateBatch.candidate_id==Candidate.id)
            .join(FeedBatch,and_(FeedBatch.id==CatalogCandidateBatch.batch_id,
                                FeedBatch.status.in_(('staged','published'))))
            .join(FeedOffer,and_(FeedOffer.batch_id==FeedBatch.id,
                    FeedOffer.brand_norm==Candidate.brand_norm, FeedOffer.article_norm==Candidate.article_norm))
            .where(Candidate.id.in_(candidate_ids)).subquery())
    q=select(ranked).where(ranked.c.rn==1)
    data=(await db.execute(q)).mappings().all()
    grouped={cid:[] for cid in candidate_ids}
    for rec in data:
        grouped[rec['candidate_id']].append({
            'supplier':rec['supplier'],'batch_id':str(rec['batch_id']),
            'source_line':rec['source_line'],'supplier_sku':rec['supplier_sku'],
            'description':rec['description'][:2000],
            'minimum':rec['minimum'],'multiple':rec['multiple'],
            'description_variants':rec['description_variants'],
            'sku_variants':rec['sku_variants']})
    return grouped


async def analyze_chunk(db, after:UUID|None=None, limit:int=CHUNK, candidate_ids=None):
    """Exactly one stable cursor step. Store frozen evidence and per-attribute claims."""
    q=select(Candidate).where(Candidate.status!='blocked')
    if candidate_ids is not None:q=q.where(Candidate.id.in_(candidate_ids))
    if after:q=q.where(Candidate.id>after)
    candidates=(await db.scalars(q.order_by(Candidate.id).limit(limit))).all()
    if not candidates:return {'seen':0,'affected':0,'reviewed':0,'cursor':after}
    ids=[c.id for c in candidates]
    evidence=await _page_sources(db,ids)
    rulebook=await snapshot(db)
    if not rulebook.revision:
        raise ValueError('Bootstrap classification rules before analysis')
    existing=(await db.scalars(select(Proposal).where(Proposal.candidate_id.in_(ids)))).all()
    by_candidate={p.candidate_id:p for p in existing}
    operator_reviews=set()
    # Protect operator action even if an ORM identity-map still has a stale status.
    # It must never be erased by a newer automatic rule version.
    to_check=[p.id for p in existing]
    if to_check:
        operator_reviews=set((await db.scalars(select(Audit.proposal_id).where(Audit.proposal_id.in_(to_check),Audit.origin=='admin_api'))).all())
    legacy=(await db.scalars(select(LegacyProposal.product_id).where(
        LegacyProposal.product_id.in_([c.product_id for c in candidates if c.product_id]),
        LegacyProposal.status=='applied'))).all() if any(c.product_id for c in candidates) else []
    previously_applied=set(legacy)
    category_rows=(await db.scalars(select(Category))).all()
    by_name={c.name.casefold():c for c in category_rows}
    touched=reviewed=0
    for c in candidates:
        old=by_candidate.get(c.id)
        if old and (old.status in ('applied','approved','rejected') or old.id in operator_reviews):
            continue  # Never discard a manual decision or committed application.
        result=reconcile(c.sample_brand,c.sample_article,evidence.get(c.id,[]),rulebook)
        conflicts=set(result.conflicts)
        category=by_name.get((result.category_name or '').casefold())
        if result.category_code and not category:conflicts.add('category_unconfigured')
        if c.product_id in previously_applied:conflicts.add('legacy_enrichment_preserved')
        if c.status=='review':conflicts.add('identity_requires_manual_review')
        if c.product_id:
            product=await db.get(Product,c.product_id)
            if not product or product.status!='draft':conflicts.add('product_not_draft')
            elif product.name.casefold().strip()!=f'{c.sample_brand} {c.sample_article}'.casefold().strip():
                conflicts.add('manual_title_preserved')
            elif not product.seo_noindex:conflicts.add('draft_noindex_required')
        status='review' if conflicts else 'proposed'
        if old and old.rule_version==RULE_VERSION and old.fingerprint==result.fingerprint and old.status==status and sorted(old.conflicts)==sorted(conflicts) and old.category_id==(category.id if category else None):
            continue
        p=old or Proposal(candidate_id=c.id,product_id=c.product_id, title=result.title,quality=0,
                          fingerprint=result.fingerprint,rule_version=RULE_VERSION)
        if not old:db.add(p)
        p.product_id=c.product_id;p.title=result.title;p.category_code=result.category_code
        p.category_id=category.id if category else None;p.attributes=result.attributes
        p.evidence=result.evidence;p.conflicts=sorted(conflicts)
        p.quality=max(0,result.quality-8*(len(conflicts)-len(result.conflicts)))
        p.fingerprint=result.fingerprint;p.rule_version=RULE_VERSION
        p.status=status;p.review_note='';p.updated_at=utcnow()
        touched+=1;reviewed+=int(status=='review')
    await db.commit()
    return {'seen':len(candidates),'affected':touched,'reviewed':reviewed,'cursor':candidates[-1].id}


async def _source_fingerprints(db, candidate_ids):
    """Re-read current staged/published supplier evidence before approving/writing."""
    if not candidate_ids:return {}
    sources=await _page_sources(db,candidate_ids)
    rulebook=await snapshot(db)
    candidates=(await db.scalars(select(Candidate).where(Candidate.id.in_(candidate_ids)))).all()
    return {c.id:reconcile(c.sample_brand,c.sample_article,sources.get(c.id,[]),rulebook).fingerprint
            for c in candidates}


async def _reject_stale(db,p,actual):
    if actual==p.fingerprint:return False
    previous=p.status
    p.status='review';p.review_note='Supplier evidence changed since proposal approval';p.updated_at=utcnow()
    db.add(Audit(proposal_id=p.id,old_status=previous,new_status='review',
                 origin='system_guard',note='source_evidence_changed'))
    return True


async def approve_safe_chunk(db, limit:int=CHUNK):
    q=(select(Proposal).where(Proposal.status=='proposed',Proposal.rule_version==RULE_VERSION,
              Proposal.category_id.is_not(None),Proposal.quality>=85)
       .order_by(Proposal.candidate_id).limit(limit).with_for_update(skip_locked=True))
    rows=(await db.scalars(q)).all()
    versions=await _source_fingerprints(db,[p.candidate_id for p in rows])
    approved=stale=0
    for p in rows:
        if await _reject_stale(db,p,versions.get(p.candidate_id)):
            stale+=1;continue
        if p.conflicts:continue
        p.status='approved';p.updated_at=utcnow()
        db.add(Audit(proposal_id=p.id,old_status='proposed',new_status='approved',
                     origin='auto_rule',note=f'Candidate metadata only; {RULE_VERSION}'))
        approved+=1
    await db.commit()
    return {'seen':len(rows),'affected':approved,'reviewed':stale}


async def prepare_drafts_chunk(db,limit:int=CHUNK):
    """Explicit action: mark APPROVED CANDIDATES eligible for the old safe draft creator.

    NEVER calls materialize_approved automatically, NEVER writes Product.status.
    """
    q=(select(Proposal,Candidate).join(Candidate,Candidate.id==Proposal.candidate_id)
       .where(Proposal.status=='approved',Proposal.rule_version==RULE_VERSION,
              Candidate.status=='new')
       .order_by(Proposal.candidate_id).limit(limit).with_for_update(skip_locked=True))
    rows=(await db.execute(q)).all()
    versions=await _source_fingerprints(db,[p.candidate_id for p,c in rows])
    count=reviewed=0
    for p,c in rows:
        if await _reject_stale(db,p,versions.get(c.id)):
            reviewed+=1;continue
        reason=suspicious(c)
        if reason or p.conflicts or p.quality<85:
            old=p.status;p.status='review';p.review_note=reason or 'Candidate policy changed'
            db.add(Audit(proposal_id=p.id,old_status=old,new_status='review',origin='system_guard',note=p.review_note))
            reviewed+=1
            continue
        c.status='approved';c.review_note='';c.updated_at=utcnow()
        db.add(CatalogDecisionAudit(candidate_id=c.id,old_status='new',new_status='approved',
                                     note=f'Explicit v0.29 prepare; no publication',origin='multi_source'))
        count+=1
    await db.commit()
    return {'seen':len(rows),'affected':count,'reviewed':reviewed}


async def apply_chunk(db, limit:int=CHUNK):
    """Populate only untouched drafts already created by CatalogBulk; never publish."""
    q=(select(Proposal,Candidate).join(Candidate,Candidate.id==Proposal.candidate_id)
       .where(Proposal.status=='approved',Candidate.status=='materialized')
       .order_by(Proposal.candidate_id).limit(limit).with_for_update(skip_locked=True))
    rows=(await db.execute(q)).all()
    if not rows:return {'seen':0,'affected':0,'reviewed':0}
    definitions={x.code:x for x in (await db.scalars(select(AttributeDefinition).where(
        AttributeDefinition.code.in_(ATTRS.keys())))).all()}
    versions=await _source_fingerprints(db,[p.candidate_id for p,c in rows])
    applied=reviewed=0
    for p,c in rows:
        if await _reject_stale(db,p,versions.get(c.id)):
            reviewed+=1;await db.commit();continue
        product=await db.scalar(select(Product).where(Product.id==c.product_id).with_for_update()) if c.product_id else None
        issue=None
        if not product or product.status!='draft' or not product.seo_noindex:issue='Product must be an unpublished noindexed draft'
        elif p.rule_version!=RULE_VERSION or p.conflicts or p.quality<85 or not p.category_id:
            issue='Rule/approval conditions changed'
        elif product.name.casefold().strip()!=f'{c.sample_brand} {c.sample_article}'.casefold().strip():
            issue='Existing editorial/enriched title protected'
        elif await db.scalar(select(LegacyProposal.id).where(
            LegacyProposal.product_id==product.id, LegacyProposal.status=='applied')):
            issue='Legacy enrichment preserved'
        else:
            current=await db.scalar(select(ProductCategory).where(
                ProductCategory.product_id==product.id,ProductCategory.is_primary==True))
            if current and current.category_id!=p.category_id:issue='Existing primary category protected'
            for code,val in p.attributes.items():
                attr=definitions.get(code)
                if not attr or attr.data_type!='enum' or val not in (attr.enum_values or []):
                    issue=f'Invalid attribute definition: {code}';break
                existing=await db.scalar(select(ProductAttribute).where(
                    ProductAttribute.product_id==product.id,ProductAttribute.attribute_id==attr.id))
                if existing and existing.value_enum!=val:issue=f'Existing operator attribute protected: {code}';break
        if issue:
            p.status='review';p.review_note=issue;p.updated_at=utcnow()
            db.add(Audit(proposal_id=p.id,old_status='approved',new_status='review',origin='system_guard',note=issue))
            reviewed+=1;await db.commit();continue
        product.name=p.title
        changes={'name':p.title,'source_fingerprint':p.fingerprint,'rule_version':RULE_VERSION}
        if not product.description:
            primary=(p.evidence.get('sources') or [{}])[0].get('description')
            if primary:product.description=primary;changes['description_source']='source_evidence'
        if not await db.scalar(select(ProductCategory.id).where(
            ProductCategory.product_id==product.id,ProductCategory.category_id==p.category_id)):
            db.add(ProductCategory(product_id=product.id,category_id=p.category_id,is_primary=True))
        for code,val in p.attributes.items():
            attr=definitions[code]
            if not await db.scalar(select(ProductAttribute.id).where(
                ProductAttribute.product_id==product.id,ProductAttribute.attribute_id==attr.id)):
                db.add(ProductAttribute(product_id=product.id,attribute_id=attr.id,value_enum=val))
        db.add(ProductChangeLog(product_id=product.id,action='enrich_v029',changes=changes))
        p.product_id=product.id;p.status='applied';p.applied_at=utcnow();p.updated_at=utcnow()
        db.add(Audit(proposal_id=p.id,old_status='approved',new_status='applied',
                     origin='worker',note='Draft metadata only; never published'))
        await db.commit();applied+=1
    return {'seen':len(rows),'affected':applied,'reviewed':reviewed}


async def execute_run(db, run_id:UUID):
    r=await db.scalar(select(Run).where(Run.id==run_id).with_for_update())
    if not r or r.status not in ('queued','processing'):return
    r.status='processing';await db.commit()
    try:
        limit=int(r.parameters['limit'])
        remaining=max(0,limit-int(r.parameters.get('scanned',0)))
        while remaining>0:
            current=(await snapshot(db)).revision
            if int(r.parameters.get('rule_revision',0))!=current:
                raise RuntimeError('Classification rules changed during this run; restart with new run')
            size=min(CHUNK,remaining)
            if r.kind=='analyze':
                after=UUID(r.parameters['cursor']) if r.parameters.get('cursor') else None
                output=await analyze_chunk(db,after,size)
                if output['cursor']:
                    r.parameters={**r.parameters,'cursor':str(output['cursor'])}
            elif r.kind=='approve_safe':output=await approve_safe_chunk(db,size)
            elif r.kind=='prepare_drafts':output=await prepare_drafts_chunk(db,size)
            elif r.kind=='apply':output=await apply_chunk(db,size)
            else:raise ValueError('Unsupported job kind')
            r.affected+=output['affected'];r.reviewed+=output['reviewed']
            r.parameters={**r.parameters,'scanned':int(r.parameters.get('scanned',0))+output['seen']}
            await db.commit()
            remaining-=output['seen']
            if not output['seen']:break
        r.status='completed';r.finished_at=utcnow();r.message='Metadata only; publication untouched'
        await db.commit()
    except Exception as exc:
        await db.rollback()
        r=await db.get(Run,run_id)
        r.status='failed';r.message=type(exc).__name__;r.finished_at=utcnow()
        await db.commit()
        raise
