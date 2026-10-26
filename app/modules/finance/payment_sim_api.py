"""F4: authenticated SHADOW SBP lifecycle and reconciliation.

No imports of live checkout, YooKassa gateway, real payments, procurement, tax posting,
F3 posting or any outgoing HTTP client. These endpoints cannot transfer money.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.finance.payment_sim_engine import digest, money, next_payment_status, ensure_refund_budget, reconcile
from app.modules.finance.payment_sim_models import (
    ShadowPayment, ShadowEvent, ShadowRefund, ShadowReceiptPlan, ShadowBankLine, ShadowBankMatch,
)

router=APIRouter(prefix='/api/v1/backoffice/finance/payments-sim',tags=['finance-f4-shadow'],dependencies=[Depends(require_admin)])
KEY=Field(min_length=8,max_length=160,pattern=r'^SIM:[A-Za-z0-9][A-Za-z0-9:._/-]+$')

class PaymentCreate(BaseModel):
    sim_order_ref:str=Field(min_length=12,max_length=160,pattern=r'^SIM:ORDER:[A-Za-z0-9][A-Za-z0-9:._/-]+$')
    idempotency_key:str=KEY
    amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)
    sale_model:Literal['OWN','AGENCY']='OWN'

class PaymentEventCreate(BaseModel):
    event_key:str=KEY
    new_status:Literal['PENDING','SUCCEEDED','CANCELED']
    amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)
    method:Literal['MOCK_SBP']='MOCK_SBP'
    currency:Literal['RUB']='RUB'

class RefundCreate(BaseModel):
    request_key:str=KEY
    amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)

class RefundEventCreate(BaseModel):
    status:Literal['SUCCEEDED','REJECTED']

class ReceiptCreate(BaseModel):
    request_key:str=KEY
    kind:Literal['PREPAYMENT','OFFSET','REFUND']
    amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)
    vat_note:str=Field(default='UNCONFIRMED',max_length=120,pattern=r'^[A-Z0-9_]{3,120}$')

class BankLineCreate(BaseModel):
    external_ref:str=Field(min_length=12,max_length=160,pattern=r'^SIM:BANK:[A-Za-z0-9][A-Za-z0-9:._/-]+$')
    direction:Literal['IN','OUT']
    amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)

class MatchCreate(BaseModel):
    payment_id:uuid.UUID|None=None
    refund_id:uuid.UUID|None=None


def amount(v):return f'{v:.2f}'

def payment_out(p):
    return {'id':str(p.id),'sim_order_ref':p.sim_order_ref,'amount':amount(p.amount),'status':p.status,
            'sale_model':p.sale_model,'provider':'MOCK_SBP','shadow_only':True,'money_moved':False}

def refund_out(r):
    return {'id':str(r.id),'payment_id':str(r.payment_id),'amount':amount(r.amount),
            'status':r.status,'shadow_only':True,'money_moved':False}

def receipt_out(r):
    return {'id':str(r.id),'payment_id':str(r.payment_id),'kind':r.kind,'amount':amount(r.amount),
            'state':'PREVIEW_ONLY','vat_note':r.vat_note,'fiscalized':False,'submitted_to_ofd':False}

def bank_out(r):
    return {'id':str(r.id),'external_ref':r.external_ref,'direction':r.direction,'amount':amount(r.amount),'shadow_only':True}

async def _commit(db):
    try:await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'Duplicate simulated operation or invalid database constraint') from None

async def _payment(db,payment_id,lock=False):
    stmt=select(ShadowPayment).where(ShadowPayment.id==payment_id)
    if lock:stmt=stmt.with_for_update()
    p=await db.scalar(stmt)
    if p is None:raise HTTPException(404,'Simulated payment not found')
    return p

@router.get('/overview')
async def overview(db:AsyncSession=Depends(get_session)):
    counts={}
    for table,key in [(ShadowPayment,'payments'),(ShadowRefund,'refunds'),(ShadowReceiptPlan,'receipt_plans'),(ShadowBankLine,'bank_lines'),(ShadowBankMatch,'matches')]:
        counts[key]=await db.scalar(select(func.count()).select_from(table))
    recent=(await db.scalars(select(ShadowPayment).order_by(ShadowPayment.created_at.desc()).limit(30))).all()
    return {'stage':'F4','mode':'SHADOW','provider':'MOCK_SBP','real_bank_enabled':False,'real_refunds_enabled':False,
            'real_fiscalization_enabled':False,'auto_ledger_postings':False,
            'counts':counts,'payments':[payment_out(x) for x in recent],
            'warning':'No bank requests, fiscal checks, ledger postings, payouts, live orders or supplier purchases.'}

@router.post('/payments',status_code=201)
async def create_payment(data:PaymentCreate,db:AsyncSession=Depends(get_session)):
    payload={'sim_order_ref':data.sim_order_ref,'amount':amount(data.amount),'sale_model':data.sale_model}
    hash_=digest(payload)
    old=await db.scalar(select(ShadowPayment).where(ShadowPayment.idempotency_key==data.idempotency_key))
    if old:
        if old.request_sha256!=hash_:raise HTTPException(409,'Payment idempotency key has a different payload')
        return payment_out(old)
    p=ShadowPayment(**data.model_dump(),request_sha256=hash_,currency='RUB',provider='MOCK_SBP',status='CREATED')
    db.add(p);await _commit(db);return payment_out(p)

@router.post('/payments/{payment_id}/mock-events',status_code=201)
async def apply_mock_event(payment_id:uuid.UUID,data:PaymentEventCreate,db:AsyncSession=Depends(get_session)):
    p=await _payment(db,payment_id,lock=True)
    payload={'new_status':data.new_status,'amount':amount(data.amount),'method':data.method,'currency':data.currency}
    hash_=digest(payload)
    old=await db.scalar(select(ShadowEvent).where(ShadowEvent.payment_id==p.id,ShadowEvent.event_key==data.event_key))
    if old:
        if old.digest!=hash_:raise HTTPException(409,'Event key replay with different payload')
        return {'event_id':str(old.id),**payment_out(p),'duplicate':True}
    if p.amount!=data.amount:raise HTTPException(409,'Simulated provider amount mismatch')
    if not next_payment_status(p.status,data.new_status):raise HTTPException(409,'Invalid or regressing payment status')
    if p.status==data.new_status:raise HTTPException(409,'Already in requested state; replay original event key')
    e=ShadowEvent(payment_id=p.id,event_key=data.event_key,digest=hash_,new_status=data.new_status)
    p.status=data.new_status;db.add(e);await _commit(db)
    return {'event_id':str(e.id),**payment_out(p),'duplicate':False}

@router.get('/payments/{payment_id}')
async def payment_detail(payment_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    p=await _payment(db,payment_id)
    refunds=(await db.scalars(select(ShadowRefund).where(ShadowRefund.payment_id==p.id).order_by(ShadowRefund.created_at))).all()
    receipts=(await db.scalars(select(ShadowReceiptPlan).where(ShadowReceiptPlan.payment_id==p.id).order_by(ShadowReceiptPlan.created_at))).all()
    return {**payment_out(p),'refunds':[refund_out(r) for r in refunds],
            'receipt_plans':[receipt_out(r) for r in receipts],
            'refunded_or_reserved':amount(sum((r.amount for r in refunds if r.status!='REJECTED'),Decimal('0')))}

@router.post('/payments/{payment_id}/refunds',status_code=201)
async def request_mock_refund(payment_id:uuid.UUID,data:RefundCreate,db:AsyncSession=Depends(get_session)):
    p=await _payment(db,payment_id,lock=True)
    existing=await db.scalar(select(ShadowRefund).where(ShadowRefund.request_key==data.request_key))
    if existing:
        if existing.payment_id!=p.id or existing.amount!=data.amount:raise HTTPException(409,'Refund key replay with different payload')
        return refund_out(existing)
    if p.status!='SUCCEEDED':raise HTTPException(409,'Only successful simulated payments can be refunded')
    refunds=(await db.scalars(select(ShadowRefund).where(ShadowRefund.payment_id==p.id))).all()
    try:ensure_refund_budget(p.amount,refunds,data.amount)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    r=ShadowRefund(payment_id=p.id,request_key=data.request_key,amount=data.amount,status='REQUESTED')
    db.add(r);await _commit(db);return refund_out(r)

@router.post('/refunds/{refund_id}/mock-result')
async def mock_refund_result(refund_id:uuid.UUID,data:RefundEventCreate,db:AsyncSession=Depends(get_session)):
    r=await db.scalar(select(ShadowRefund).where(ShadowRefund.id==refund_id).with_for_update())
    if r is None:raise HTTPException(404,'Simulated refund not found')
    if r.status!= 'REQUESTED':
        if r.status==data.status:return refund_out(r)
        raise HTTPException(409,'Refund result is immutable')
    r.status=data.status;await _commit(db);return refund_out(r)

@router.post('/payments/{payment_id}/receipt-plans',status_code=201)
async def plan_receipt(payment_id:uuid.UUID,data:ReceiptCreate,db:AsyncSession=Depends(get_session)):
    p=await _payment(db,payment_id,lock=True)
    old=await db.scalar(select(ShadowReceiptPlan).where(ShadowReceiptPlan.request_key==data.request_key))
    if old:
        if old.payment_id!=p.id or old.kind!=data.kind or old.amount!=data.amount or old.vat_note!=data.vat_note:
            raise HTTPException(409,'Receipt plan key replay with different payload')
        return receipt_out(old)
    if p.status!='SUCCEEDED':raise HTTPException(409,'No receipt plan before successful mock payment')
    other=(await db.scalars(select(ShadowReceiptPlan).where(ShadowReceiptPlan.payment_id==p.id))).all()
    if data.kind=='PREPAYMENT':
        if data.amount!=p.amount:raise HTTPException(409,'Prepayment receipt total must equal payment')
        if any(x.kind=='PREPAYMENT' for x in other):raise HTTPException(409,'Prepayment plan already exists')
    if data.kind=='OFFSET':
        if not any(x.kind=='PREPAYMENT' for x in other):raise HTTPException(409,'Prepayment plan required before offset')
        used=sum((x.amount for x in other if x.kind=='OFFSET'),Decimal('0'))
        if used+data.amount>p.amount:raise HTTPException(409,'Offset exceeds original prepayment')
    if data.kind=='REFUND':
        refunds=(await db.scalars(select(ShadowRefund).where(ShadowRefund.payment_id==p.id,ShadowRefund.status=='SUCCEEDED'))).all()
        limit=sum((x.amount for x in refunds),Decimal('0'))
        used=sum((x.amount for x in other if x.kind=='REFUND'),Decimal('0'))
        if used+data.amount>limit:raise HTTPException(409,'Refund receipt plan exceeds successful refunds')
    r=ShadowReceiptPlan(payment_id=p.id,**data.model_dump(),state='PREVIEW_ONLY')
    db.add(r);await _commit(db);return receipt_out(r)

@router.post('/bank-lines',status_code=201)
async def bank_line(data:BankLineCreate,db:AsyncSession=Depends(get_session)):
    old=await db.scalar(select(ShadowBankLine).where(ShadowBankLine.external_ref==data.external_ref))
    if old:
        if old.amount!=data.amount or old.direction!=data.direction:raise HTTPException(409,'Simulated bank reference reused for different data')
        return bank_out(old)
    row=ShadowBankLine(**data.model_dump());db.add(row);await _commit(db);return bank_out(row)

@router.get('/bank-lines')
async def bank_lines(db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(ShadowBankLine).order_by(ShadowBankLine.created_at.desc()).limit(100))).all()
    return [bank_out(r) for r in rows]

@router.post('/bank-lines/{line_id}/match',status_code=201)
async def match_bank_line(line_id:uuid.UUID,data:MatchCreate,db:AsyncSession=Depends(get_session)):
    if (data.payment_id is None)==(data.refund_id is None):raise HTTPException(422,'Choose exactly one payment or refund')
    row=await db.scalar(select(ShadowBankLine).where(ShadowBankLine.id==line_id).with_for_update())
    if not row:raise HTTPException(404,'Simulated bank line not found')
    prior=await db.scalar(select(ShadowBankMatch).where(ShadowBankMatch.bank_line_id==row.id))
    if prior:
        if prior.payment_id==data.payment_id and prior.refund_id==data.refund_id:
            return {'status':'MATCH','match_id':str(prior.id),'duplicate':True,'shadow_only':True}
        raise HTTPException(409,'Bank line already matched to a different operation')
    if data.payment_id:
        operation=await _payment(db,data.payment_id)
        kind='PAYMENT'
    else:
        operation=await db.get(ShadowRefund,data.refund_id)
        if operation is None:raise HTTPException(404,'Simulated refund not found')
        kind='REFUND'
    if operation.status!='SUCCEEDED':raise HTTPException(409,'Only successful simulation can be matched')
    comparison=reconcile(row.direction,row.amount,operation.amount,kind)
    if comparison!='MATCH':raise HTTPException(409,comparison)
    match=ShadowBankMatch(bank_line_id=row.id,payment_id=data.payment_id,refund_id=data.refund_id)
    db.add(match);await _commit(db)
    return {'status':'MATCH','match_id':str(match.id),'duplicate':False,'shadow_only':True}

@router.get('/reconciliation')
async def reconciliation(db:AsyncSession=Depends(get_session)):
    lines=(await db.scalars(select(ShadowBankLine).order_by(ShadowBankLine.created_at.desc()).limit(200))).all()
    matches=(await db.scalars(select(ShadowBankMatch).where(ShadowBankMatch.bank_line_id.in_([x.id for x in lines])))).all() if lines else []
    by_line={m.bank_line_id:m for m in matches}
    return {'lines':[{**bank_out(line),'status':'MATCHED' if line.id in by_line else 'UNMATCHED',
                      'payment_id':str(by_line[line.id].payment_id) if line.id in by_line and by_line[line.id].payment_id else None,
                      'refund_id':str(by_line[line.id].refund_id) if line.id in by_line and by_line[line.id].refund_id else None}
                     for line in lines], 'shadow_only':True,'real_bank_statement_import_enabled':False}
