"""Batch-scoped, durable new-feed -> catalog classification -> draft pipeline.

Activation of price data is separate and NEVER automatic. Product publishing,
merchant offers, payment and procurement are not present in this module.
"""
from __future__ import annotations
from uuid import UUID
from datetime import timedelta
from sqlalchemy import select, func
from app.modules.commerce.models import Job
from app.modules.catalog_bulk.models import CatalogCandidate as Candidate,CatalogCandidateBatch
from app.modules.catalog_bulk.service import scan_batch
from app.modules.multi_enrichment.service import analyze_chunk
from app.modules.multi_enrichment.taxonomy_db import snapshot
from app.modules.draft_materialization.service import preflight_chunk,materialize_chunk
from app.modules.draft_materialization.models import DraftRun
from app.modules.multi_enrichment.models import MultiSourceRun
from .models import FeedCatalogSync,FeedBatch,utc

PAGE=200
PAGES_PER_JOB=4

async def queue_sync(db,batch_id):
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status not in ('staged','published'):
        raise ValueError('Only validated staged/published feeds can trigger the catalog')
    sync=await db.scalar(select(FeedCatalogSync).where(FeedCatalogSync.batch_id==batch_id))
    if sync:return sync
    sync=FeedCatalogSync(batch_id=batch_id,status='queued',stage='scan',scanned=0,step=0,
                         taxonomy_revision=(await snapshot(db)).revision)
    if sync.taxonomy_revision<=0:
        sync.status='paused';sync.message='taxonomy_uninitialized'
        db.add(sync);return sync
    db.add(sync)
    await db.flush()
    db.add(Job(dedup_key=f'feed-catalog-sync:{batch_id}:0',kind='feed_catalog_sync',
               payload={'sync_id':str(sync.id),'step':0},status='pending',due_at=utc(),attempts=0))
    return sync


async def run_sync(db,sync_id:UUID,step:int):
    sync=await db.scalar(select(FeedCatalogSync).where(FeedCatalogSync.id==sync_id).with_for_update())
    if not sync or sync.status != 'queued' or sync.step!=step:
        return  # Ignore replay of old durable messages.
    try:
        if (await snapshot(db)).revision!=sync.taxonomy_revision:
            sync.status='paused';sync.message='taxonomy_revision_changed';await db.commit();return
        batch=await db.get(FeedBatch,sync.batch_id)
        if not batch or batch.status not in ('staged','published'):
            sync.status='paused';sync.message='supplier_feed_not_staged';await db.commit();return
        # Serialize classification/draft materialization with any manual global job.
        busy=await db.scalar(select(DraftRun.id).where(DraftRun.status.in_(('queued','processing'))).limit(1))
        busy_multi=await db.scalar(select(MultiSourceRun.id).where(MultiSourceRun.status.in_(('queued','processing'))).limit(1))
        other_sync=await db.scalar(select(FeedCatalogSync.id).where(
            FeedCatalogSync.id!=sync.id,FeedCatalogSync.status=='processing').limit(1))
        if busy or busy_multi or other_sync:
            # Stop indefinite self-requeue; a human can resolve the blocked run.
            if sync.step >= 60:
                sync.status='failed'
                sync.message='capacity_wait_retry_budget_exhausted'
                await db.commit()
                return
            sync.status='queued';sync.message='waiting_for_catalog_capacity';sync.step+=1
            db.add(Job(dedup_key=f'feed-catalog-sync:{sync.batch_id}:wait:{sync.step}',
                kind='feed_catalog_sync',payload={'sync_id':str(sync.id),'step':sync.step},
                status='pending',due_at=utc()+timedelta(minutes=2),attempts=0))
            await db.commit();return
        sync.status='processing'
        if sync.stage=='scan':
            await scan_batch(db,sync.batch_id)
            sync=await db.get(FeedCatalogSync,sync_id)
            sync.stage='candidates'
        for _ in range(PAGES_PER_JOB):
            if (await snapshot(db)).revision!=sync.taxonomy_revision:
                sync.status='paused';sync.message='taxonomy_revision_changed';await db.commit();return
            after=UUID(sync.cursor) if sync.cursor else None
            q=(select(Candidate.id).join(CatalogCandidateBatch,CatalogCandidateBatch.candidate_id==Candidate.id)
               .where(CatalogCandidateBatch.batch_id==sync.batch_id))
            if after:q=q.where(Candidate.id>after)
            ids=(await db.scalars(q.order_by(Candidate.id).limit(PAGE))).all()
            if not ids:
                from .service import _match
                sync.linked=await _match(db,sync.batch_id)  # new drafts also receive file offers
                sync.status='completed';sync.stage='complete';sync.finished_at=utc()
                sync.message='Batch-scoped catalog processed: DRAFT only; feed activation unchanged'
                await db.commit();return
            analysis=await analyze_chunk(db,candidate_ids=ids)
            preflight=await preflight_chunk(db,candidate_ids=ids)
            created=await materialize_chunk(db,candidate_ids=ids)
            sync=await db.get(FeedCatalogSync,sync_id)
            sync.scanned+=len(ids);sync.created_drafts+=created['created']
            sync.reviewed+=preflight['reviewed']+created['reviewed']
            sync.updated_proposals+=analysis['affected']
            sync.cursor=str(ids[-1]);await db.commit()
        sync.step+=1
        db.add(Job(dedup_key=f'feed-catalog-sync:{sync.batch_id}:{sync.step}',kind='feed_catalog_sync',
            payload={'sync_id':str(sync.id),'step':sync.step},status='pending',due_at=utc(),attempts=0))
        await db.commit()
    except Exception as exc:
        await db.rollback()
        sync=await db.get(FeedCatalogSync,sync_id)
        if sync:
            sync.status='failed';sync.message=type(exc).__name__
            await db.commit()
        raise


def view(sync):
    return {'id':str(sync.id),'batch_id':str(sync.batch_id),'status':sync.status,
      'stage':sync.stage,'scanned':sync.scanned,'created_drafts':sync.created_drafts,
      'reviewed':sync.reviewed,'updated_proposals':sync.updated_proposals,'linked':sync.linked,
      'taxonomy_revision':sync.taxonomy_revision,'message':sync.message,
      'auto_publish':False,'price_activation_automatic':False}
