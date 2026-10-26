"""Operator-only draft materialization. No publication endpoints or side effects."""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from app.modules.multi_enrichment.taxonomy_db import snapshot
from .models import DraftRun as Run, DraftPreflight as Preflight, utcnow
from .service import run_view, preflight_view

router=APIRouter(prefix='/api/v1/admin/draft-materialization',tags=['catalog-draft-materialization'],
                 dependencies=[Depends(require_admin)])


class PreflightRequest(BaseModel):
    limit:int=Field(default=1000,ge=1,le=350000)
    start_after:UUID|None=None


class MaterializeRequest(BaseModel):
    limit:int=Field(default=100,ge=1,le=10000)
    confirm_draft_only:bool=Field(description='Explicit acknowledgement: only unpublished drafts')


async def enqueue(db,kind,limit,after=None):
    book=await snapshot(db)
    if not book.revision:raise HTTPException(409,'Bootstrap taxonomy first')
    params={'limit':limit,'rule_revision':book.revision}
    if after:params['cursor']=str(after)
    run=Run(kind=kind,active_key='global',parameters=params,status='queued')
    try:
        db.add(run);await db.flush()
        db.add(Job(dedup_key=f'draft-v031:{run.id}',kind='draft_materialization',
                   payload={'run_id':str(run.id)},status='pending',due_at=utcnow(),attempts=0))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback();raise HTTPException(409,'Another draft job is queued or processing') from exc
    return run_view(run)


@router.post('/runs/preflight',status_code=202)
async def preflight(body:PreflightRequest,db:AsyncSession=Depends(get_session)):
    return await enqueue(db,'preflight',body.limit,body.start_after)


@router.post('/runs/materialize',status_code=202)
async def materialize(body:MaterializeRequest,db:AsyncSession=Depends(get_session)):
    if not body.confirm_draft_only:
        raise HTTPException(422,'Set confirm_draft_only=true for explicit draft creation')
    return await enqueue(db,'materialize',body.limit)


@router.get('/runs')
async def runs(limit:int=Query(default=30,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))).all()
    return {'items':[run_view(x) for x in rows]}


@router.get('/runs/{run_id}')
async def run_detail(run_id:UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(Run,run_id)
    if not row:raise HTTPException(404,'Run not found')
    return run_view(row)


@router.post('/runs/{run_id}/resume',status_code=202)
async def resume(run_id:UUID,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(Run).where(Run.id==run_id).with_for_update())
    if not row:raise HTTPException(404,'Run not found')
    if row.status!='failed':raise HTTPException(409,'Only failed runs may resume')
    rev=(await snapshot(db)).revision
    if rev!=row.parameters['rule_revision']:
        raise HTTPException(409,'Rules changed; start a new preflight run instead')
    restart=int(row.parameters.get('restarts',0))+1
    if restart>5:raise HTTPException(409,'Retry limit exceeded')
    row.parameters={**row.parameters,'restarts':restart};row.status='queued';row.finished_at=None
    db.add(Job(dedup_key=f'draft-v031:{run_id}:restart:{restart}',kind='draft_materialization',
               payload={'run_id':str(run_id)},status='pending',due_at=utcnow(),attempts=0))
    try:await db.commit()
    except IntegrityError as exc:
        await db.rollback();raise HTTPException(409,'Another draft job is queued or processing') from exc
    return run_view(row)


@router.get('/statistics')
async def statistics(db:AsyncSession=Depends(get_session)):
    counts=(await db.execute(select(Preflight.status,func.count(Preflight.id)).group_by(Preflight.status))).all()
    book=await snapshot(db)
    return {'statuses':{state:int(n) for state,n in counts},
            'rule_revision':book.revision,'publication_policy':'individual_manual_only',
            'auto_publish':False}


@router.get('/preflights')
async def preflights(status:str|None=None,limit:int=Query(default=30,ge=1,le=200),
                     db:AsyncSession=Depends(get_session)):
    if status not in (None,'eligible','review','materialized'):
        raise HTTPException(422,'Unknown preflight status')
    q=select(Preflight)
    if status:q=q.where(Preflight.status==status)
    rows=(await db.scalars(q.order_by(Preflight.candidate_id).limit(limit))).all()
    return {'items':[preflight_view(x) for x in rows]}


@router.get('/preflights/{candidate_id}')
async def preflight_detail(candidate_id:UUID,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(Preflight).where(Preflight.candidate_id==candidate_id))
    if not row:raise HTTPException(404,'No preflight for candidate')
    return preflight_view(row)
