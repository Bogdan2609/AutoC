"""v0.31.3 administrative one-click draft-only automation API."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from app.modules.multi_enrichment.taxonomy_db import snapshot
from .models import DraftPreflight, DraftRun, utcnow
from .service import run_view

router = APIRouter(prefix='/api/v1/admin/draft-materialization/automation',
                   tags=['catalog-draft-automation-v0313'],
                   dependencies=[Depends(require_admin)])


class LaunchRequest(BaseModel):
    mode: str = Field(default='preview',pattern='^(preview|drafts)$')
    limit: int = Field(default=1000,ge=1,le=350000)
    legacy_limit: int = Field(default=200,ge=0,le=2000)
    start_after: UUID | None = None
    confirm_draft_only: bool = False


def view(run):
    data = run_view(run)
    data['publication_policy'] = 'individual_manual_only'
    data['auto_publish'] = False
    return data


@router.post('/runs',status_code=202)
async def launch(body:LaunchRequest, db:AsyncSession=Depends(get_session)):
    if body.mode=='drafts' and not body.confirm_draft_only:
        raise HTTPException(422,'Confirm unpublished draft-only automation')
    book=await snapshot(db)
    if book.revision<=0:
        raise HTTPException(409,'Bootstrap taxonomy first')
    run=DraftRun(kind='auto_pipeline',active_key='global',status='queued',
        parameters={'mode':body.mode,'limit':body.limit,'legacy_limit':body.legacy_limit,
            'stage':'legacy' if body.legacy_limit else 'candidates',
            'cursor':str(body.start_after) if body.start_after else None,
            'legacy_cursor':None,'legacy_scanned':0,'legacy_recovered':0,
            'legacy_reviewed':0,'legacy_eligible':0,'rule_revision':book.revision,
            'next_job':0,'generation':0})
    try:
        db.add(run); await db.flush()
        db.add(Job(dedup_key=f'catalog-auto-v0313:{run.id}:start',
            kind='draft_materialization',payload={'run_id':str(run.id),'auto_step':0,'auto_generation':0},
            status='pending',due_at=utcnow(),attempts=0))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Another draft/preflight/automation job is running') from exc
    return view(run)


@router.get('/runs')
async def runs(limit:int=Query(default=20,ge=1,le=100),
               db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(DraftRun).where(DraftRun.kind=='auto_pipeline')
                           .order_by(DraftRun.created_at.desc()).limit(limit))).all()
    return {'items':[view(row) for row in rows],'auto_publish':False}


@router.get('/runs/{run_id}')
async def detail(run_id:UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(DraftRun,run_id)
    if not row or row.kind!='auto_pipeline':raise HTTPException(404,'Automation run not found')
    return view(row)


@router.post('/runs/{run_id}/resume',status_code=202)
async def resume(run_id:UUID,db:AsyncSession=Depends(get_session)):
    run=await db.scalar(select(DraftRun).where(DraftRun.id==run_id).with_for_update())
    if not run or run.kind!='auto_pipeline':raise HTTPException(404,'Automation run not found')
    if run.status!='failed':raise HTTPException(409,'Only failed automation runs may resume')
    if (await snapshot(db)).revision!=run.parameters.get('rule_revision'):
        raise HTTPException(409,'Taxonomy revision changed; run new preview before new execution')
    count=int(run.parameters.get('restarts',0))+1
    if count>5:raise HTTPException(409,'Restart limit exceeded')
    generation=int(run.parameters.get('generation',0))+1
    run.parameters={**run.parameters,'restarts':count,'generation':generation};run.status='queued';run.finished_at=None
    db.add(Job(dedup_key=f'catalog-auto-v0313:{run.id}:resume:{count}',
        kind='draft_materialization',payload={'run_id':str(run.id),
            'auto_step':int(run.parameters.get('next_job',0)),'auto_generation':generation},
        status='pending',due_at=utcnow(),attempts=0))
    try:await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Another automation job is running') from exc
    return view(run)


@router.get('/statistics')
async def statistics(db:AsyncSession=Depends(get_session)):
    current=await snapshot(db)
    values=(await db.execute(select(DraftPreflight.status,func.count())
                             .group_by(DraftPreflight.status))).all()
    return {'preflight_statuses':{k:int(v) for k,v in values},
            'rule_revision':current.revision,'auto_publish':False,
            'publication_policy':'individual_manual_only'}
