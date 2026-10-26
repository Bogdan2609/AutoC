"""Admin-only previews and explicit, queued catalog enrichment operations."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from app.modules.supplier_feeds.models import FeedBatch
from .models import EnrichmentProposal as Proposal, EnrichmentRun as Run, EnrichmentDecisionAudit as Audit, utcnow
from .service import bootstrap_taxonomy,proposal_view,run_view
from .rules import ENGINE_VERSION,TAXONOMY,suggest

router=APIRouter(prefix='/api/v1/admin/catalog-enrichment',tags=['catalog-enrichment'],
                 dependencies=[Depends(require_admin)])


class RunRequest(BaseModel):
    batch_id:UUID
    limit:int=Field(default=500,ge=1,le=20000)


@router.post('/taxonomy/bootstrap')
async def init_categories(db:AsyncSession=Depends(get_session)):
    """Opt-in creation of controlled taxonomy. No products change."""
    try:return await bootstrap_taxonomy(db)
    except ValueError as exc:
        await db.rollback();raise HTTPException(409,str(exc)) from exc


@router.get('/taxonomy')
async def taxonomy():
    return {'rule_version':ENGINE_VERSION,
            'categories':[{'code':k,'category':v[0],'title_stem':v[1]} for k,v in TAXONOMY.items()],
            'fitment_inference':False,'auto_publish':False}


@router.get('/preview')
async def preview(brand:str=Query(min_length=1,max_length=160),
                  article:str=Query(min_length=1,max_length=110),
                  description:str=Query(min_length=1,max_length=300)):
    """Pure preview; does not write any catalog row."""
    result=suggest(brand,article,description)
    return {'title':result.title,'category_code':result.category_code,
            'category_name':result.category_name,'attributes':result.attributes,
            'quality':result.score,'flags':result.flags,'rule_version':ENGINE_VERSION,
            'publish':False}


async def enqueue(db,kind,body):
    batch=await db.get(FeedBatch,body.batch_id)
    if not batch or batch.status not in ('staged','published'):
        raise HTTPException(409,'Source batch must be staged or published')
    run=Run(kind=kind,batch_id=body.batch_id,status='queued',
            parameters={'limit':body.limit},affected=0,reviewed=0)
    try:
        db.add(run);await db.flush()
        db.add(Job(dedup_key=f'enrich:{run.id}',kind='catalog_enrichment',
                   payload={'run_id':str(run.id)},status='pending',due_at=utcnow(),attempts=0))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Another enrichment run is active for this batch') from exc
    return run_view(run)


@router.post('/runs/propose',status_code=202)
async def queue_propose(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await enqueue(db,'propose',body)


@router.post('/runs/approve-safe',status_code=202)
async def queue_approve(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await enqueue(db,'approve_safe',body)


@router.post('/runs/apply',status_code=202)
async def queue_apply(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await enqueue(db,'apply',body)


@router.get('/runs')
async def list_runs(limit:int=Query(default=30,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))).all()
    return {'items':[run_view(r) for r in rows]}


@router.get('/runs/{run_id}')
async def run_detail(run_id:UUID,db:AsyncSession=Depends(get_session)):
    run=await db.get(Run,run_id)
    if not run:raise HTTPException(404,'Unknown enrichment run')
    return run_view(run)


@router.get('/statistics')
async def statistics(batch_id:UUID|None=None,db:AsyncSession=Depends(get_session)):
    q=select(Proposal.status,func.count(Proposal.id))
    if batch_id:q=q.where(Proposal.batch_id==batch_id)
    data=(await db.execute(q.group_by(Proposal.status))).all()
    return {'statuses':{state:int(count) for state,count in data},
            'batch_id':str(batch_id) if batch_id else None}


@router.get('/proposals')
async def proposals(batch_id:UUID|None=None,
                    status:Literal['proposed','review','approved','applied','rejected']|None=None,
                    limit:int=Query(default=30,ge=1,le=200),
                    offset:int=Query(default=0,ge=0,le=1000000),
                    db:AsyncSession=Depends(get_session)):
    q=select(Proposal)
    if batch_id:q=q.where(Proposal.batch_id==batch_id)
    if status:q=q.where(Proposal.status==status)
    rows=(await db.scalars(q.order_by(Proposal.created_at,Proposal.id)
                           .offset(offset).limit(limit))).all()
    return {'items':[proposal_view(p) for p in rows]}


@router.get('/proposals/{proposal_id}')
async def proposal_detail(proposal_id:UUID,db:AsyncSession=Depends(get_session)):
    p=await db.get(Proposal,proposal_id)
    if not p:raise HTTPException(404,'Proposal not found')
    return proposal_view(p)


class Decision(BaseModel):
    decision:Literal['approved','review','rejected']
    note:str=Field(default='',max_length=500)
    confirm_ambiguous:bool=False


@router.post('/proposals/{proposal_id}/decision')
async def decide(proposal_id:UUID,body:Decision,db:AsyncSession=Depends(get_session)):
    p=await db.scalar(select(Proposal).where(Proposal.id==proposal_id).with_for_update())
    if not p:raise HTTPException(404,'Proposal not found')
    if p.status=='applied':raise HTTPException(409,'Applied proposal is immutable')
    if body.decision=='approved':
        if p.rule_version!=ENGINE_VERSION:raise HTTPException(409,'Regenerate proposal with current rules first')
        if not p.category_id:raise HTTPException(409,'Configure category first')
        blocking=set(p.flags or [])-{'conflicting_sources','unparsed_qualifiers'}
        if blocking:raise HTTPException(409,'Blocking flags require source correction')
        if p.flags and (not body.confirm_ambiguous or len(body.note.strip())<12):
            raise HTTPException(409,'Ambiguity requires explicit confirmation and explanatory note')
        if p.status=='rejected':raise HTTPException(409,'Rejected proposal must be rescanned after source correction')
    old=p.status
    p.status=body.decision;p.review_note=body.note
    if body.decision=='approved' and p.flags:
        p.evidence={**p.evidence,'manual_confirmed':True,
                    'confirmed_flags':p.flags,'confirmation_note':body.note}
    db.add(Audit(proposal_id=p.id,old_status=old,new_status=body.decision,
                 note=body.note,origin='admin_api'))
    await db.commit()
    return proposal_view(p)


@router.get('/proposals/{proposal_id}/decisions')
async def audit(proposal_id:UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(Proposal,proposal_id):raise HTTPException(404,'Unknown proposal')
    rows=(await db.scalars(select(Audit).where(Audit.proposal_id==proposal_id)
                           .order_by(Audit.created_at.desc()).limit(100))).all()
    return {'items':[{'old_status':x.old_status,'new_status':x.new_status,
                      'note':x.note,'origin':x.origin,'created_at':x.created_at.isoformat()}
                     for x in rows]}
