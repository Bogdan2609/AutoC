"""Bulk-safe candidate discovery, review, and DRAFT-only materialization.

Never calls suppliers, never publishes SEO pages, never changes payment/order state.
The scan uses indexed, set-based SQL on PostgreSQL and bounded keyset chunks on SQLite.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timezone
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import and_, func, insert, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import NORMALIZER_VERSION, make_slug
from app.modules.catalog_ext.models import BrandAlias
from app.modules.supplier_feeds.models import FeedBatch, FeedBrandAlias, FeedOffer
from .models import CatalogCandidate as Candidate, CatalogCandidateBatch as CandidateBatch, CatalogBulkRun as BulkRun, now


def _dialect(db):
    engine = getattr(db, 'bind', None) or getattr(getattr(db, 'session', None), 'bind', None)
    return engine.dialect.name if engine is not None else 'postgresql'


def _upsert(db):
    return pg_insert if _dialect(db) == 'postgresql' else sqlite_insert


def candidate_summary(c):
    return {'id':str(c.id),'brand':c.sample_brand,'article':c.sample_article,
            'brand_norm':c.brand_norm,'article_norm':c.article_norm,
            'description':c.sample_description,'status':c.status,
            'product_id':str(c.product_id) if c.product_id else None,'review_note':c.review_note}


def run_summary(run):
    return {'id':str(run.id),'kind':run.kind,'status':run.status,
            'batch_id':str(run.batch_id) if run.batch_id else None,
            'affected':run.affected,'linked':run.linked,'message':run.message,
            'created_at':run.created_at.isoformat() if run.created_at else None,
            'finished_at':run.finished_at.isoformat() if run.finished_at else None}


async def scan_batch(db, batch_id: UUID) -> tuple[int, int]:
    """Idempotent registration of candidate identities and batch provenance.

    Never changes a staged/published FeedBatch nor existing catalog products.
    Repeating the scan is harmless and does not reset operator decisions.
    """
    batch=await db.get(FeedBatch,batch_id)
    if batch is None or batch.status not in ('staged','published'):
        raise ValueError('Only staged/published feed batches may be scanned')
    grouping=(FeedOffer.brand_norm,FeedOffer.article_norm)
    if _dialect(db)=='postgresql':
        # gen_random_uuid exists in PostgreSQL 18. No Python row-by-row upsert.
        rows=(select(func.gen_random_uuid(),FeedOffer.brand_norm,FeedOffer.article_norm,
                     func.min(FeedOffer.brand),func.min(FeedOffer.article),
                     func.substr(func.min(FeedOffer.description),1,300),
                     literal('new'), literal(''), literal(now()),literal(now()))
              .where(FeedOffer.batch_id==batch_id).group_by(*grouping))
        stmt=(pg_insert(Candidate).from_select(
            ['id','brand_norm','article_norm','sample_brand','sample_article',
             'sample_description','status','review_note','created_at','updated_at'], rows)
            .on_conflict_do_nothing(index_elements=['brand_norm','article_norm']))
        await db.execute(stmt)
    else:
        # Portable bounded-memory implementation for SQLite tests and development.
        last=None
        while True:
            q=(select(FeedOffer.brand_norm,FeedOffer.article_norm,
                    func.min(FeedOffer.brand),func.min(FeedOffer.article),
                    func.substr(func.min(FeedOffer.description),1,300))
               .where(FeedOffer.batch_id==batch_id).group_by(*grouping)
               .order_by(*grouping).limit(1000))
            if last is not None:q=q.where(tuple_(*grouping)>last)
            rows=(await db.execute(q)).all()
            if not rows:break
            values=[dict(id=uuid.uuid4(),brand_norm=bn,article_norm=an,
                         sample_brand=brand,sample_article=article,
                         sample_description=desc or '',status='new',review_note='',
                         created_at=now(),updated_at=now()) for bn,an,brand,article,desc in rows]
            await db.execute(sqlite_insert(Candidate).on_conflict_do_nothing(
                index_elements=['brand_norm','article_norm']),values)
            last=(rows[-1][0],rows[-1][1])
    # Set-based per-source provenance. A candidate can belong to many daily batches
    # and suppliers without multiplying product cards or losing offer quantities.
    source_select=(select(func.gen_random_uuid() if _dialect(db)=='postgresql' else func.random(),
               Candidate.id,FeedOffer.batch_id,func.count(FeedOffer.id),
               func.count(func.distinct(FeedOffer.description)),
               func.count(func.distinct(func.nullif(FeedOffer.supplier_sku,''))),
               func.substr(func.min(FeedOffer.description),1,300))
        .join(Candidate,and_(Candidate.brand_norm==FeedOffer.brand_norm,
                             Candidate.article_norm==FeedOffer.article_norm))
        .where(FeedOffer.batch_id==batch_id)
        .group_by(Candidate.id,FeedOffer.batch_id))
    if _dialect(db) == 'sqlite':
        # SQLite func.random returns integer and is not suitable for UUID;
        # scan provenance using bounded keyset traversal instead.
        last=None
        while True:
            q=(select(Candidate.id,func.count(FeedOffer.id),
                      func.count(func.distinct(FeedOffer.description)),
                      func.count(func.distinct(func.nullif(FeedOffer.supplier_sku,''))),
               func.substr(func.min(FeedOffer.description),1,300))
               .join(FeedOffer,and_(Candidate.brand_norm==FeedOffer.brand_norm,
                    Candidate.article_norm==FeedOffer.article_norm))
               .where(FeedOffer.batch_id==batch_id).group_by(Candidate.id)
               .order_by(Candidate.id).limit(1000))
            if last is not None:q=q.where(Candidate.id>last)
            groups=(await db.execute(q)).all()
            if not groups:break
            await db.execute(sqlite_insert(CandidateBatch).on_conflict_do_nothing(
                index_elements=['candidate_id','batch_id']),
                [dict(id=uuid.uuid4(),candidate_id=cid,batch_id=batch_id,
                      offers_count=offers,description_variants=descs,sku_variants=skus,
                      sample_description=(desc or '')[:300])
                 for cid,offers,descs,skus,desc in groups])
            last=groups[-1][0]
    else:
        await db.execute(pg_insert(CandidateBatch).from_select(
            ['id','candidate_id','batch_id','offers_count','description_variants','sku_variants','sample_description'],
            source_select).on_conflict_do_nothing(index_elements=['candidate_id','batch_id']))
    total=int(await db.scalar(select(func.count()).select_from(CandidateBatch).where(
        CandidateBatch.batch_id==batch_id)) or 0)
    await db.commit()
    return total, int(await db.scalar(select(func.count()).select_from(Candidate).where(
        Candidate.status=='new')) or 0)


def suspicious(c) -> str | None:
    """Conservative screening for unattended *approval*; operator may override."""
    if (not c.brand_norm or not c.article_norm or
        len(c.brand_norm)>160 or len(c.article_norm)>110):return 'empty_or_long_identity'
    if any(x in c.brand_norm for x in ('Ð','Ñ','\ufffd','�','?')):
        return 'suspected_text_encoding'
    if re.search(r'[\x00-\x1f<>"{}]',c.sample_brand+c.sample_article):
        return 'unsafe_identity_characters'
    if len(c.brand_norm)<2 or len(c.article_norm)<2:
        return 'short_ambiguous_identity'
    if len(c.sample_brand)>160 or len(c.sample_article)>110:
        return 'oversized_identity'
    if c.brand_norm in {'UNKNOWN','NONAME','NO NAME','БЕЗ БРЕНДА'}:
        return 'unknown_manufacturer'
    return None


async def approve_safe(db, batch_id:UUID, limit:int=500) -> dict:
    """Strict opt-in approval. Never approves multiple supplier SKUs or titles."""
    if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'Unknown source batch')
    variant_totals=(select(CandidateBatch.candidate_id,
                 func.max(CandidateBatch.description_variants).label('description_variants'),
                 func.max(CandidateBatch.sku_variants).label('sku_variants'),
                 func.count(func.distinct(CandidateBatch.sample_description)).label('source_descriptions'))
                 .group_by(CandidateBatch.candidate_id).subquery())
    q=(select(Candidate,variant_totals.c.description_variants,variant_totals.c.sku_variants,
              variant_totals.c.source_descriptions)
       .join(CandidateBatch,CandidateBatch.candidate_id==Candidate.id)
       .join(variant_totals,variant_totals.c.candidate_id==Candidate.id)
       .where(CandidateBatch.batch_id==batch_id,Candidate.status=='new')
       .order_by(Candidate.brand_norm,Candidate.article_norm).limit(limit))
    if _dialect(db)=='postgresql':q=q.with_for_update(of=Candidate,skip_locked=True)
    rows=(await db.execute(q)).all()
    approved=review=0
    for candidate,description_variants,sku_variants,source_descriptions in rows:
        reason=suspicious(candidate)
        # Multiple distinct descriptions or supplier identity SKUs are a review
        # signal, not a licence to merge product executions automatically.
        if description_variants>1 or sku_variants>1 or source_descriptions>1:
            reason='identity_variants_need_review'
        if reason:
            candidate.status='review';candidate.review_note=reason;review+=1
        else:
            candidate.status='approved';candidate.review_note='';approved+=1
        candidate.updated_at=now()
    await db.commit()
    return {'selected':len(rows),'approved':approved,'review':review,
            'note':'Approval allows draft creation only, never publication'}


def _stable_slug(prefix: str, value:str, *, identity:str, max_length:int=180) -> str:
    hashpart=hashlib.sha256(identity.encode('utf-8')).hexdigest()[:14]
    prefix=re.sub(r'[^a-z0-9-]+','-',prefix.casefold()).strip('-') or 'brand'
    value=re.sub(r'[^a-z0-9-]+','-',value.casefold()).strip('-') or 'item'
    return (f'{prefix}-{value}'[:max_length-15].rstrip('-')+'-'+hashpart)


async def _resolve_brand(db,c:Candidate):
    # Canonical brand, verified global alias and approved supplier-specific aliases.
    direct=await db.scalar(select(Brand).where(Brand.name_norm==c.brand_norm))
    verified=(await db.scalars(select(BrandAlias.brand_id).where(
        BrandAlias.alias_norm==c.brand_norm,BrandAlias.verified.is_(True)))).all()
    supplier_aliases=(await db.scalars(select(FeedBrandAlias.brand_id).join(
        FeedBatch, FeedBatch.supplier_code==FeedBrandAlias.supplier_code).join(
        CandidateBatch,CandidateBatch.batch_id==FeedBatch.id)
        .where(CandidateBatch.candidate_id==c.id,
               FeedBrandAlias.brand_norm==c.brand_norm,
               FeedBrandAlias.approved.is_(True)))).all()
    ids=set(verified)|set(supplier_aliases)|({direct.id} if direct else set())
    if len(ids)>1:return None,'conflicting_approved_brand_aliases'
    if ids:
        brand=await db.get(Brand,next(iter(ids)))
        return brand,None if brand else 'missing_brand'
    # Brand is created only as a result of an explicit candidate approval.
    slug=_stable_slug(c.brand_norm,'',identity=c.brand_norm,max_length=180)
    brand=Brand(id=uuid.uuid4(),name=c.sample_brand,
                name_norm=c.brand_norm,slug=slug)
    db.add(brand)
    await db.flush()
    return brand,None


async def materialize_approved(db, *, limit:int=100, batch_id:UUID | None=None) -> dict:
    """Create *unpublished*, noindexed Products. Never bypasses publication policy.

    Uses short transactions per candidate to avoid large locks, and ON CONFLICT
    is enforced by unique identities at the DB level. A batch filter scopes work.
    """
    if not 1<=limit<=1000:raise ValueError('Materialization limit out of range')
    q=select(Candidate).where(Candidate.status=='approved')
    if batch_id:
        q=q.join(CandidateBatch,CandidateBatch.candidate_id==Candidate.id).where(CandidateBatch.batch_id==batch_id)
    q=q.order_by(Candidate.brand_norm,Candidate.article_norm).limit(limit)
    if _dialect(db)=='postgresql':q=q.with_for_update(of=Candidate,skip_locked=True)
    candidates=(await db.scalars(q)).all()
    created=linked=review=0
    for candidate in candidates:
        # Re-lock after previous commit: another worker may have decided this candidate.
        candidate=await db.scalar(select(Candidate).where(Candidate.id==candidate.id)
            .with_for_update(skip_locked=True))
        if candidate is None or candidate.status!='approved':continue
        reason=suspicious(candidate)
        if reason and not candidate.review_note.startswith('manual_override:'):
            candidate.status='review';candidate.review_note=reason
            await db.commit();review+=1;continue
        brand,problem=await _resolve_brand(db,candidate)
        if problem:
            candidate.status='review';candidate.review_note=problem
            await db.commit();review+=1;continue
        product=await db.scalar(select(Product).where(
            Product.brand_id==brand.id,Product.article_norm==candidate.article_norm))
        if product is None:
            product=Product(id=uuid.uuid4(),brand_id=brand.id,article=candidate.sample_article,
                article_norm=candidate.article_norm,
                name=f'{brand.name} {candidate.sample_article}'[:300],description=None,
                slug=_stable_slug(brand.slug,candidate.article_norm,
                    identity=f'{brand.id}:{candidate.article_norm}',max_length=200),
                status='draft',seo_noindex=True,normalizer_version=NORMALIZER_VERSION)
            db.add(product)
            await db.flush()
            created+=1
        else:linked+=1
        candidate.product_id=product.id
        candidate.status='materialized'
        candidate.review_note=''
        candidate.updated_at=now()
        # Commit the product, candidate, and offer links atomically: no dangling
        # materialized candidate can survive a crash before the relink.
        # Link ONLY source batches scanned into provenance; don't alter any other
        # product links and NEVER replace a previously verified product mapping.
        target_batches=select(CandidateBatch.batch_id).where(CandidateBatch.candidate_id==candidate.id)
        await db.execute(update(FeedOffer).where(FeedOffer.batch_id.in_(target_batches),
            FeedOffer.brand_norm==candidate.brand_norm,
            FeedOffer.article_norm==candidate.article_norm,
            FeedOffer.product_id.is_(None)).values(product_id=product.id))
        await db.commit()
    return {'selected':len(candidates),'created_drafts':created,
            'linked_existing':linked,'review':review,'published':0}


async def relink_batch(db,batch_id:UUID)->int:
    """Link previously approved identities to an additional (later) feed file."""
    batch=await db.get(FeedBatch,batch_id)
    if batch is None or batch.status not in ('staged','published'):
        raise ValueError('Only staged/published feed batches may be linked')
    source=(select(Candidate.product_id).join(CandidateBatch,CandidateBatch.candidate_id==Candidate.id)
        .where(CandidateBatch.batch_id==batch_id,
               Candidate.brand_norm==FeedOffer.brand_norm,
               Candidate.article_norm==FeedOffer.article_norm,
               Candidate.status=='materialized').limit(1).correlate(FeedOffer).scalar_subquery())
    # Preserve any existing verified links from earlier feed importer.
    await db.execute(update(FeedOffer).where(FeedOffer.batch_id==batch_id,
        FeedOffer.product_id.is_(None),source.is_not(None)).values(product_id=source))
    linked=int(await db.scalar(select(func.count(FeedOffer.id)).where(
        FeedOffer.batch_id==batch_id,FeedOffer.product_id.is_not(None))) or 0)
    await db.commit()
    return linked


async def execute_run(db,run_id:UUID):
    run=await db.scalar(select(BulkRun).where(BulkRun.id==run_id).with_for_update())
    if run is None or run.status not in ('queued','processing'):return
    run.status='processing'
    await db.commit()
    try:
        if run.kind=='scan':
            result=await scan_batch(db,run.batch_id)
            attached=await relink_batch(db,run.batch_id)
            run=await db.get(BulkRun,run_id)
            run.affected=result[0];run.linked=attached
            run.message=f'{result[1]} new candidates remain; existing draft links refreshed'
        elif run.kind=='approve_safe':
            total=int(run.affected);approved=int(run.linked);review=0
            remaining=max(0,int(run.parameters.get('limit',1000))-total)
            while remaining>0:
                result=await approve_safe(db,run.batch_id,min(remaining,1000))
                if not result['selected']:break
                total+=result['selected'];approved+=result['approved'];review+=result['review']
                remaining-=result['selected']
                run=await db.get(BulkRun,run_id)
                run.affected=total;run.linked=approved
                await db.commit()
            run=await db.get(BulkRun,run_id)
            run.affected=total;run.linked=approved
            run.message=f'{review} identities sent for review; no products published'
        elif run.kind=='materialize':
            created=linked=review=0
            total=int(run.affected)
            remaining=max(0,int(run.parameters.get('limit',100))-total)
            while remaining>0:
                result=await materialize_approved(db,limit=min(remaining,250),batch_id=run.batch_id)
                if not result['selected']:break
                total+=result['selected'];remaining-=result['selected']
                created+=result['created_drafts'];linked+=result['linked_existing']
                review+=result['review']
                run=await db.get(BulkRun,run_id)
                run.affected=total;run.linked+=result['created_drafts']+result['linked_existing']
                run.message=f'Created {created} drafts, linked {linked} existing, reviewed {review}'
                await db.commit()
            run=await db.get(BulkRun,run_id)
        elif run.kind=='relink':
            result=await relink_batch(db,run.batch_id)
            run=await db.get(BulkRun,run_id)
            run.affected=result;run.linked=result
        else:raise ValueError('Unknown catalog bulk job')
        run.status='completed';run.finished_at=now()
        await db.commit()
    except Exception as exc:
        await db.rollback()
        run=await db.get(BulkRun,run_id)
        if run:
            run.status='failed';run.message=type(exc).__name__+': '+str(exc)[:180]
            run.finished_at=now();await db.commit()
        raise
