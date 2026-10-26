"""v0.31 candidate preflight, audited idempotent draft materialization.

Only metadata, local DB writes. NO Product publication, supplier checkout or webhooks.
Every materialization uses a persisted preflight and revalidates the inputs.
"""
from __future__ import annotations
import uuid
from uuid import UUID
from sqlalchemy import select, func, update
from app.modules.catalog.models import Brand, Product, ProductChangeLog
from app.modules.catalog.normalize import NORMALIZER_VERSION
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate, CatalogCandidateBatch, CatalogDecisionAudit
from app.modules.catalog_bulk.service import suspicious, _resolve_brand, _stable_slug, _dialect
from app.modules.catalog_ext.models import Category, ProductCategory, AttributeDefinition, ProductAttribute
from app.modules.multi_enrichment.models import MultiSourceProposal as Proposal
from app.modules.multi_enrichment.classifier import RULE_VERSION, reconcile
from app.modules.multi_enrichment.service import _source_fingerprints, _page_sources
from app.modules.multi_enrichment.taxonomy_db import snapshot, TaxonomyRevision
from app.modules.supplier_feeds.models import FeedOffer
from .models import DraftPreflight as Preflight, DraftRun as Run, DraftAudit as Audit, utcnow
from app.modules.multi_enrichment.semantic_guard import source_checks

CHUNK = 100


def run_view(r):
    return {'automation': ({'mode':r.parameters.get('mode'), 'stage':r.parameters.get('stage'), 'legacy_scanned':r.parameters.get('legacy_scanned',0),'legacy_recovered':r.parameters.get('legacy_recovered',0), 'legacy_reviewed':r.parameters.get('legacy_reviewed',0)} if r.kind=='auto_pipeline' else None), 'id':str(r.id),'kind':r.kind,'status':r.status,'scanned':r.scanned,
            'eligible':r.eligible,'created_drafts':r.created,'reviewed':r.reviewed,
            'cursor':r.parameters.get('cursor'),'rule_revision':r.parameters.get('rule_revision'),
            'message':r.message,'created_at':r.created_at.isoformat(),
            'finished_at':r.finished_at.isoformat() if r.finished_at else None}


def preflight_view(p):
    return {'id':str(p.id),'candidate_id':str(p.candidate_id),'proposal_id':str(p.proposal_id),
            'status':p.status,'reasons':p.reasons,'rule_version':p.rule_version,
            'rule_revision':p.rule_revision,'product_id':str(p.product_id) if p.product_id else None}


async def _check_candidate(db,c,p,rev,defs,variant_flags,actual):
    reasons=[]
    if p.status!='proposed':reasons.append('proposal_not_proposed')
    if c.status!='new' or c.product_id or p.product_id:reasons.append('candidate_already_linked_or_reviewed')
    if p.rule_version!=RULE_VERSION or p.evidence.get('taxonomy_revision')!=rev:
        reasons.append('stale_taxonomy_revision')
    if not p.category_id or not p.category_code or not await db.get(Category,p.category_id):
        reasons.append('missing_category')
    if p.quality<85:reasons.append('insufficient_quality')
    if p.conflicts:reasons.append('proposal_has_conflicts')
    if actual!=p.fingerprint:reasons.append('stale_source_evidence')
    if len(p.title.strip())<8 or len(p.title)>300:reasons.append('invalid_title')
    if (bad:=suspicious(c)):reasons.append('identity_'+bad)
    if c.id in variant_flags:reasons.append('identity_variants_review')
    sources=p.evidence.get('sources') or []
    semantic=source_checks(p.category_code,[s.get('description','') for s in sources])
    reasons.extend(semantic['issues'])
    if not sources or any(not s.get('supplier') or not s.get('batch_id') or not s.get('description') for s in sources):
        reasons.append('missing_source_provenance')
    if len({s.get('supplier') for s in sources})!=len(sources):reasons.append('duplicate_supplier_evidence')
    for code,value in (p.attributes or {}).items():
        attr=defs.get(code)
        if not attr or attr.data_type!='enum' or value not in (attr.enum_values or []):
            reasons.append('invalid_attribute_'+code)
    return sorted(set(reasons))


async def _variants(db,ids):
    if not ids:return set()
    rows=(await db.execute(select(CatalogCandidateBatch.candidate_id).where(
        CatalogCandidateBatch.candidate_id.in_(ids),
        (CatalogCandidateBatch.description_variants>1)|(CatalogCandidateBatch.sku_variants>1)))).all()
    return {x[0] for x in rows}


async def preflight_chunk(db, *, after:UUID|None=None,limit:int=CHUNK,run_id:UUID|None=None,candidate_ids=None):
    """Inspect proposed candidates in stable UUID order; no Product writes.

    Preflight is an immutable-input snapshot for downstream creation. Subsequent
    re-evaluation updates checks unless the record is already materialized.
    """
    q=(select(Proposal,Candidate).join(Candidate,Candidate.id==Proposal.candidate_id)
       .where(Proposal.status=='proposed'))
    if candidate_ids is not None:q=q.where(Candidate.id.in_(candidate_ids))
    if after:q=q.where(Candidate.id>after)
    pairs=(await db.execute(q.order_by(Candidate.id).limit(limit))).all()
    if not pairs:return {'seen':0,'eligible':0,'reviewed':0,'cursor':after}
    ids=[c.id for p,c in pairs]
    previous={x.candidate_id:x for x in (await db.scalars(select(Preflight).where(
        Preflight.candidate_id.in_(ids)))).all()}
    rev=(await snapshot(db)).revision
    fingerprints=await _source_fingerprints(db,ids)
    defs={x.code:x for x in (await db.scalars(select(AttributeDefinition))).all()}
    variant_flags=await _variants(db,ids)
    eligible=reviewed=0
    for p,c in pairs:
        pf=previous.get(c.id)
        if pf and pf.status=='materialized':continue
        reasons=await _check_candidate(db,c,p,rev,defs,variant_flags,fingerprints.get(c.id))
        state='review' if reasons else 'eligible'
        if not pf:
            pf=Preflight(candidate_id=c.id,proposal_id=p.id,rule_version=p.rule_version,
                         fingerprint=p.fingerprint,rule_revision=rev,status=state,reasons=reasons)
            db.add(pf)
        else:
            pf.proposal_id=p.id;pf.rule_version=p.rule_version;pf.rule_revision=rev
            pf.fingerprint=p.fingerprint;pf.status=state;pf.reasons=reasons;pf.updated_at=utcnow()
        eligible+=state=='eligible';reviewed+=state=='review'
        if state=='review':
            db.add(Audit(candidate_id=c.id,proposal_id=p.id,run_id=run_id,event='preflight_review',
                         details={'reasons':reasons,'rule_revision':rev}))
    await db.commit()
    return {'seen':len(pairs),'eligible':eligible,'reviewed':reviewed,'cursor':pairs[-1][1].id}


async def _mark_review(db,pf,p,c,reason,run_id):
    pf.status='review';pf.reasons=[reason];pf.updated_at=utcnow()
    db.add(Audit(candidate_id=c.id,proposal_id=p.id,run_id=run_id,event='materialization_blocked',
                 details={'reason':reason}))
    await db.commit()


async def materialize_chunk(db, *,limit:int=CHUNK,run_id:UUID|None=None, candidate_after:UUID|None=None, candidate_to:UUID|None=None,candidate_ids=None):
    """Explicit, bounded DRAFT-ONLY creation; every candidate committed atomically.

    Another worker cannot materialize the same identity concurrently: candidate
    rows are locked and products have a DB unique constraint on brand/article.
    """
    rev=(await snapshot(db)).revision
    q=(select(Preflight).where(Preflight.status=='eligible',
          Preflight.rule_revision==rev, Preflight.rule_version==RULE_VERSION))
    if candidate_ids is not None:q=q.where(Preflight.candidate_id.in_(candidate_ids))
    if candidate_after is not None:q=q.where(Preflight.candidate_id>candidate_after)
    if candidate_to is not None:q=q.where(Preflight.candidate_id<=candidate_to)
    q=q.order_by(Preflight.candidate_id).limit(limit)
    if _dialect(db)=='postgresql':q=q.with_for_update(skip_locked=True)
    pf_ids=[x.id for x in (await db.scalars(q)).all()]
    created=reviewed=seen=0
    for pf_id in pf_ids:
        pf=await db.scalar(select(Preflight).where(Preflight.id==pf_id).with_for_update())
        if not pf or pf.status!='eligible':continue
        seen+=1
        p=await db.scalar(select(Proposal).where(Proposal.id==pf.proposal_id).with_for_update())
        c=await db.scalar(select(Candidate).where(Candidate.id==pf.candidate_id).with_for_update())
        if not p or not c:
            # Referenced records are FK-protected; defensive fail closed.
            raise ValueError('Preflight references missing source')
        # Serialize the final validation/commit with administrative rule edits.
        # Admin PATCH also locks revision row 1 before incrementing the revision.
        revision_lock=await db.scalar(select(TaxonomyRevision).where(
            TaxonomyRevision.id==1).with_for_update())
        if not revision_lock or revision_lock.revision!=rev:
            raise RuntimeError('Rules changed while creating drafts')
        defs={x.code:x for x in (await db.scalars(select(AttributeDefinition))).all()}
        flags=await _variants(db,[c.id]);actual=(await _source_fingerprints(db,[c.id])).get(c.id)
        reasons=await _check_candidate(db,c,p,rev,defs,flags,actual)
        if pf.proposal_id!=p.id or pf.fingerprint!=p.fingerprint or pf.rule_revision!=rev:
            reasons.append('preflight_snapshot_changed')
        # v0.31.2: independent last-mile reclassification from actual current
        # supplier evidence. Saved score/fingerprint alone is not a semantic
        # proof. Fail closed on rule changes, conflicts and title/type drift.
        if not reasons:
            current_book=await snapshot(db)
            if current_book.revision!=rev:
                reasons.append('classification_revision_changed')
            else:
                live=(await _page_sources(db,[c.id])).get(c.id,[])
                verified=reconcile(c.sample_brand,c.sample_article,live,current_book)
                if (verified.conflicts or verified.category_code!=p.category_code
                        or verified.title!=p.title or verified.attributes!=p.attributes
                        or verified.fingerprint!=p.fingerprint):
                    reasons.append('last_mile_semantic_reconciliation_failed')
        if reasons:
            await _mark_review(db,pf,p,c,','.join(sorted(set(reasons))),run_id)
            reviewed+=1;continue
        # Strictly no adoption or modification of existing Product, even a draft.
        brand,problem=await _resolve_brand(db,c)
        if problem:
            await _mark_review(db,pf,p,c,problem,run_id);reviewed+=1;continue
        existing=await db.scalar(select(Product.id).where(
            Product.brand_id==brand.id, Product.article_norm==c.article_norm))
        if existing:
            await _mark_review(db,pf,p,c,'existing_product_requires_manual_link',run_id)
            reviewed+=1;continue
        slug=_stable_slug(brand.slug,c.article_norm,
                          identity=f'{brand.id}:{c.article_norm}',max_length=200)
        product=Product(id=uuid.uuid4(),brand_id=brand.id,article=c.sample_article,
            article_norm=c.article_norm,name=p.title,description=(p.evidence.get('sources') or [{}])[0].get('description'),
            slug=slug,status='draft',seo_noindex=True,normalizer_version=NORMALIZER_VERSION)
        db.add(product);await db.flush()
        db.add(ProductCategory(product_id=product.id,category_id=p.category_id,is_primary=True))
        for code,val in (p.attributes or {}).items():
            db.add(ProductAttribute(product_id=product.id,attribute_id=defs[code].id,value_enum=val))
        c.status='materialized';c.product_id=product.id;c.review_note='';c.updated_at=utcnow()
        p.product_id=product.id;p.status='applied';p.applied_at=utcnow();p.updated_at=utcnow()
        pf.product_id=product.id;pf.status='materialized';pf.materialized_at=utcnow();pf.updated_at=utcnow()
        db.add(ProductChangeLog(product_id=product.id,action='draft_materialize_v031',
            changes={'candidate_id':str(c.id),'proposal_id':str(p.id),
                     'fingerprint':p.fingerprint,'rule_version':p.rule_version,
                     'rule_revision':rev,'publication':'draft_only'}))
        db.add(CatalogDecisionAudit(candidate_id=c.id,old_status='new',new_status='materialized',
            note='v0.31 preflight-controlled draft, no publication',origin='draft_v031'))
        db.add(Audit(candidate_id=c.id,proposal_id=p.id,run_id=run_id,event='draft_created',
            details={'product_id':str(product.id),'rule_revision':rev,'published':False}))
        # Link only unassigned offers, limited to scanned batches of this identity.
        batches=select(CatalogCandidateBatch.batch_id).where(CatalogCandidateBatch.candidate_id==c.id)
        await db.execute(update(FeedOffer).where(FeedOffer.batch_id.in_(batches),
            FeedOffer.brand_norm==c.brand_norm,FeedOffer.article_norm==c.article_norm,
            FeedOffer.product_id.is_(None)).values(product_id=product.id))
        await db.commit()
        created+=1
    return {'seen':seen,'created':created,'reviewed':reviewed}


async def execute_run(db,run_id:UUID,*,auto_step:int|None=None,auto_generation:int|None=None):
    r=await db.scalar(select(Run).where(Run.id==run_id).with_for_update())
    if not r or r.status not in ('queued','processing'):return
    if r.kind=='auto_pipeline' and (
        (auto_step is not None and auto_step != int(r.parameters.get('next_job',0))) or
        (auto_generation is not None and auto_generation != int(r.parameters.get('generation',0)))
    ):
        return  # Duplicate/stale queue delivery; no side effects or counter changes.
    r.status='processing';await db.commit()
    try:
        if r.kind=='auto_pipeline':
            from .automation import execute_auto_pipeline
            await execute_auto_pipeline(db,r)
            return
        remaining=max(0,int(r.parameters['limit'])-r.scanned)
        while remaining>0:
            rev=(await snapshot(db)).revision
            if rev!=int(r.parameters['rule_revision']):
                raise RuntimeError('Classification revision changed; create a new preflight')
            size=min(CHUNK,remaining)
            if r.kind=='preflight':
                after=UUID(r.parameters['cursor']) if r.parameters.get('cursor') else None
                out=await preflight_chunk(db,after=after,limit=size,run_id=r.id)
                if out['cursor']:
                    r.parameters={**r.parameters,'cursor':str(out['cursor'])}
            elif r.kind=='materialize':
                out=await materialize_chunk(db,limit=size,run_id=r.id)
            else:raise ValueError('Unsupported run kind')
            r.scanned+=out['seen'];r.eligible+=out.get('eligible',0)
            r.created+=out.get('created',0);r.reviewed+=out['reviewed']
            await db.commit()
            remaining-=out['seen']
            if not out['seen']:break
        r.status='completed';r.finished_at=utcnow();r.message='Only draft metadata; no publication'
        await db.commit()
    except Exception as exc:
        await db.rollback()
        r=await db.get(Run,run_id)
        r.status='failed';r.message=f'{type(exc).__name__}: {str(exc)[:180]}'
        r.finished_at=utcnow();await db.commit()
        raise
