"""Authenticated operator commands for controlled batch catalog ingestion."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from app.modules.supplier_feeds.models import FeedBatch
from .models import CatalogCandidate as Candidate, CatalogCandidateBatch as CandidateBatch, CatalogBulkRun as BulkRun, CatalogDecisionAudit, now
from .service import candidate_summary,run_summary,approve_safe

router=APIRouter(prefix='/api/v1/admin/catalog-bulk',tags=['catalog-bulk'],
                 dependencies=[Depends(require_admin)])


class RunRequest(BaseModel):
    batch_id:UUID
    limit:int=Field(default=100,ge=1,le=10000)


async def _queue(db,kind,batch_id,parameters=None):
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status not in ('staged','published'):
        raise HTTPException(409,'Batch must be staged or published')
    run=BulkRun(kind=kind,status='queued',batch_id=batch_id,
                parameters=parameters or {},affected=0,linked=0,message='')
    db.add(run)
    await db.flush()
    db.add(Job(dedup_key=f'bulk-catalog:{run.id}',kind='catalog_bulk',
               payload={'run_id':str(run.id)},status='pending',due_at=now(),attempts=0))
    await db.commit()
    return run_summary(run)


@router.post('/scan',status_code=202)
async def queue_scan(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _queue(db,'scan',body.batch_id)


class ApproveQueueRequest(BaseModel):
    batch_id:UUID
    limit:int=Field(default=200000,ge=1,le=500000)


@router.post('/approve-safe/queue',status_code=202)
async def queue_safe_approval(body:ApproveQueueRequest,db:AsyncSession=Depends(get_session)):
    return await _queue(db,'approve_safe',body.batch_id,{'limit':body.limit})


@router.post('/materialize',status_code=202)
async def queue_materialize(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _queue(db,'materialize',body.batch_id,{'limit':body.limit})


@router.post('/relink',status_code=202)
async def queue_relink(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _queue(db,'relink',body.batch_id)


@router.get('/runs')
async def runs(limit:int=Query(default=30,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(BulkRun).order_by(BulkRun.created_at.desc()).limit(limit))).all()
    return {'items':[run_summary(r) for r in rows]}


@router.get('/runs/{run_id}')
async def run_detail(run_id:UUID,db:AsyncSession=Depends(get_session)):
    run=await db.get(BulkRun,run_id)
    if not run:raise HTTPException(404,'Unknown bulk run')
    return run_summary(run)


@router.get('/statistics')
async def stats(batch_id:UUID|None=None,db:AsyncSession=Depends(get_session)):
    from .models import CatalogCandidate
    totals=(await db.execute(select(Candidate.status,func.count(Candidate.id)).group_by(Candidate.status))).all()
    response={'statuses':{status:int(count) for status,count in totals}}
    if batch_id:
        if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'No such feed batch')
        response['batch_candidates']=int(await db.scalar(select(func.count()).select_from(CandidateBatch)
            .where(CandidateBatch.batch_id==batch_id)) or 0)
        response['batch_offers']=int(await db.scalar(select(func.sum(CandidateBatch.offers_count))
            .where(CandidateBatch.batch_id==batch_id)) or 0)
    return response


@router.get('/candidates')
async def candidates(status:Literal['new','review','approved','materialized','blocked']|None=None,
                     batch_id:UUID|None=None,brand:str|None=None,
                     limit:int=Query(default=50,ge=1,le=200),
                     offset:int=Query(default=0,ge=0,le=1000000),
                     db:AsyncSession=Depends(get_session)):
    q=select(Candidate)
    if batch_id:
        q=q.join(CandidateBatch,CandidateBatch.candidate_id==Candidate.id).where(
            CandidateBatch.batch_id==batch_id)
    if status:q=q.where(Candidate.status==status)
    if brand:q=q.where(Candidate.brand_norm==brand.upper().strip())
    rows=(await db.scalars(q.order_by(Candidate.brand_norm,Candidate.article_norm)
                           .offset(offset).limit(limit))).all()
    return {'items':[candidate_summary(r) for r in rows]}


class SafeApproval(BaseModel):
    batch_id:UUID
    limit:int=Field(default=500,ge=1,le=3000)


@router.post('/approve-safe')
async def approve_safe_batch(body:SafeApproval,db:AsyncSession=Depends(get_session)):
    return await approve_safe(db,body.batch_id,limit=body.limit)


class ReviewDecision(BaseModel):
    decision:Literal['approved','review','blocked']
    note:str=Field(default='',max_length=500)


@router.post('/candidates/{candidate_id}/decision')
async def decide(candidate_id:UUID,body:ReviewDecision,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(Candidate).where(Candidate.id==candidate_id).with_for_update())
    if not row:raise HTTPException(404,'Candidate not found')
    if row.status=='materialized':raise HTTPException(409,'Materialized identity cannot be reassigned')
    original=row.status
    row.status=body.decision
    row.review_note=('manual_override:'+body.note if body.decision=='approved' else body.note)
    row.updated_at=now()
    db.add(CatalogDecisionAudit(candidate_id=row.id,old_status=original,
         new_status=body.decision,note=body.note,origin='admin_api'))
    await db.commit()
    return candidate_summary(row)


@router.get('/candidates/{candidate_id}/decisions')
async def decision_history(candidate_id:UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(Candidate,candidate_id):raise HTTPException(404,'Unknown candidate')
    logs=(await db.scalars(select(CatalogDecisionAudit).where(
        CatalogDecisionAudit.candidate_id==candidate_id).order_by(
        CatalogDecisionAudit.created_at.desc()).limit(100))).all()
    return {'items':[{'old_status':x.old_status,'new_status':x.new_status,
                       'note':x.note,'origin':x.origin,'created_at':x.created_at.isoformat()}
                      for x in logs]}
