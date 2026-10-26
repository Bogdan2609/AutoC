"""Administrative candidate enrichment endpoints; no public or publication routes."""
from __future__ import annotations
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from .classifier import RULE_VERSION, TYPE_MAP, reconcile
from .taxonomy_db import snapshot, TaxonomyRevision
from .models import MultiSourceProposal as Proposal, MultiSourceRun as Run, MultiSourceDecision as Audit, utcnow
from .service import bootstrap, view, run_view, _source_fingerprints

router=APIRouter(prefix='/api/v1/admin/multi-enrichment',tags=['multi-source-catalog'],
                 dependencies=[Depends(require_admin)])


class RunRequest(BaseModel):
    limit:int=Field(default=1000,ge=1,le=350000)
    start_after:UUID|None=None


class DecisionRequest(BaseModel):
    decision:Literal['approved','review','rejected']
    note:str=Field(default='',max_length=500)
    confirm_ambiguous:bool=False


class SourcePreview(BaseModel):
    brand:str=Field(min_length=1,max_length=160)
    article:str=Field(min_length=1,max_length=110)
    sources:list[dict]=Field(min_length=1,max_length=4)


@router.get('/taxonomy')
async def taxonomy(db:AsyncSession=Depends(get_session)):
    book=await snapshot(db)
    return {'rule_version':RULE_VERSION,'rule_revision':book.revision,
            'types_supported':len(book.types),
            'publication_policy':'individual_manual_only','auto_publish':False}



@router.post('/taxonomy/bootstrap')
async def taxonomy_bootstrap(db:AsyncSession=Depends(get_session)):
    try:return await bootstrap(db)
    except ValueError as exc:
        await db.rollback();raise HTTPException(409,str(exc)) from exc


@router.post('/preview')
async def preview(body:SourcePreview,db:AsyncSession=Depends(get_session)):
    required={'supplier','batch_id','source_line','description'}
    if any(not required.issubset(x) for x in body.sources):
        raise HTTPException(422,'Each source needs supplier, batch_id, source_line, description')
    if len({x['supplier'] for x in body.sources})!=len(body.sources):
        raise HTTPException(422,'One source per independent supplier')
    try:
        clean=[{'supplier':str(s['supplier'])[:32], 'batch_id':str(s['batch_id'])[:36],
               'source_line':int(s['source_line']),'description':str(s['description'])[:2000],
               'description_variants':int(s.get('description_variants',1)),
               'sku_variants':int(s.get('sku_variants',1))} for s in body.sources]
    except (ValueError,TypeError) as exc:
        raise HTTPException(422,'Invalid source value') from exc
    book=await snapshot(db)
    if not book.revision:raise HTTPException(409,'Bootstrap taxonomy first')
    res=reconcile(body.brand,body.article,clean,book)
    return {'title':res.title,'category_code':res.category_code,'attributes':res.attributes,
            'conflicts':res.conflicts,'quality':res.quality,'evidence':res.evidence,
            'rule_version':RULE_VERSION,'rule_revision':book.revision,'auto_publish':False}


async def _enqueue(db,kind,body):
    if kind!='analyze' and body.start_after is not None:
        raise HTTPException(422,'start_after only permitted on analyze')
    book=await snapshot(db)
    if not book.revision:raise HTTPException(409,'Bootstrap taxonomy first')
    settings={'limit':body.limit,'scanned':0,'rule_revision':book.revision}
    if body.start_after:settings['cursor']=str(body.start_after)
    run=Run(kind=kind,status='queued',active_key='global',parameters=settings)
    try:
        db.add(run);await db.flush()
        db.add(Job(dedup_key=f'multi-enrich:{run.id}',kind='multi_enrichment',
                   payload={'run_id':str(run.id)},status='pending',due_at=utcnow(),attempts=0))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Another multi-source operation is active') from exc
    return run_view(run)


@router.post('/runs/analyze',status_code=202)
async def analyze(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _enqueue(db,'analyze',body)


@router.post('/runs/approve-safe',status_code=202)
async def approve(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _enqueue(db,'approve_safe',body)


@router.post('/runs/prepare-drafts',status_code=202)
async def prepare(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _enqueue(db,'prepare_drafts',body)


@router.post('/runs/apply',status_code=202)
async def apply(body:RunRequest,db:AsyncSession=Depends(get_session)):
    return await _enqueue(db,'apply',body)


@router.get('/runs')
async def runs(limit:int=Query(default=30,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))).all()
    return {'items':[run_view(r) for r in rows]}


@router.get('/runs/{run_id}')
async def run_detail(run_id:UUID,db:AsyncSession=Depends(get_session)):
    r=await db.get(Run,run_id)
    if not r:raise HTTPException(404,'Unknown run')
    return run_view(r)


@router.post('/runs/{run_id}/resume',status_code=202)
async def resume(run_id:UUID,db:AsyncSession=Depends(get_session)):
    r=await db.scalar(select(Run).where(Run.id==run_id).with_for_update())
    if not r:raise HTTPException(404,'Unknown run')
    if r.status!='failed':raise HTTPException(409,'Only failed runs may resume')
    # A new job key is used, since old failed jobs have a unique dedup_key.
    attempts=int(r.parameters.get('restarts',0))+1
    if attempts>5:raise HTTPException(409,'Maximum automatic retry count exceeded')
    r.parameters={**r.parameters,'restarts':attempts};r.status='queued';r.finished_at=None
    db.add(Job(dedup_key=f'multi-enrich:{run_id}:restart:{attempts}',kind='multi_enrichment',
               payload={'run_id':str(run_id)},status='pending',due_at=utcnow(),attempts=0))
    await db.commit()
    return run_view(r)


@router.get('/statistics')
async def statistics(db:AsyncSession=Depends(get_session)):
    results=(await db.execute(select(Proposal.status,func.count(Proposal.id)).group_by(Proposal.status))).all()
    book=await snapshot(db)
    return {'statuses':{state:int(count) for state,count in results},
            'rule_version':RULE_VERSION,'rule_revision':book.revision,'auto_publish':False}


@router.get('/proposals')
async def proposals(status:Literal['proposed','review','approved','applied','rejected']|None=None,
                    limit:int=Query(default=30,ge=1,le=200),offset:int=Query(default=0,ge=0),
                    db:AsyncSession=Depends(get_session)):
    q=select(Proposal)
    if status:q=q.where(Proposal.status==status)
    rows=(await db.scalars(q.order_by(Proposal.candidate_id).offset(offset).limit(limit))).all()
    return {'items':[view(p) for p in rows]}


@router.get('/proposals/{proposal_id}')
async def proposal_detail(proposal_id:UUID,db:AsyncSession=Depends(get_session)):
    p=await db.get(Proposal,proposal_id)
    if not p:raise HTTPException(404,'Proposal not found')
    return view(p)


@router.post('/proposals/{proposal_id}/decision')
async def decide(proposal_id:UUID,body:DecisionRequest,db:AsyncSession=Depends(get_session)):
    p=await db.scalar(select(Proposal).where(Proposal.id==proposal_id).with_for_update())
    if not p:raise HTTPException(404,'Proposal not found')
    if p.status=='applied':raise HTTPException(409,'Applied proposal immutable')
    if p.status=='rejected':raise HTTPException(409,'Rejected proposal immutable; rescan after correction')
    if body.decision=='approved':
        book=await snapshot(db)
        if (p.rule_version!=RULE_VERSION or not p.category_id or
                p.evidence.get('taxonomy_revision')!=book.revision):
            raise HTTPException(409,'Current rules and configured category required')
        fingerprints=await _source_fingerprints(db,[p.candidate_id])
        if fingerprints.get(p.candidate_id)!=p.fingerprint:
            raise HTTPException(409,'Source evidence or rule revision changed; reanalyze first')
        hard=set(p.conflicts or [])-{'source_type_incomplete','execution_or_kit_review'}
        if hard:raise HTTPException(409,'Blocking conflicts must be corrected at the source')
        if p.conflicts and (not body.confirm_ambiguous or len(body.note.strip())<12):
            raise HTTPException(409,'Explicit qualified human decision and reason required')
    old=p.status;p.status=body.decision;p.review_note=body.note;p.updated_at=utcnow()
    db.add(Audit(proposal_id=p.id,old_status=old,new_status=p.status,origin='admin_api',note=body.note))
    await db.commit()
    return view(p)
