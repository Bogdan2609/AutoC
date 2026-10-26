"""F3 admin API: all journal posts are isolated SHADOW simulations.

No bank, payment, checkout, merchant payment or procurement adapters imported.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.finance.models import LegalEntity
from app.modules.finance.ledger_models import LedgerBook, LedgerAccount, LedgerEntry, LedgerLine
from app.modules.finance.ledger_engine import canonical_lines, fingerprint, signed_balance

router = APIRouter(prefix='/api/v1/backoffice/finance/ledger', tags=['finance-f3'], dependencies=[Depends(require_admin)])


class BookInput(BaseModel):
    code: str = Field(pattern=r'^SIM-[A-Z0-9][A-Z0-9_-]{2,39}$', max_length=80)
    name: str = Field(min_length=3, max_length=200)
    legal_entity_id: uuid.UUID | None = None


class AccountInput(BaseModel):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{1,39}$')
    title: str = Field(min_length=2, max_length=200)
    kind: Literal['ASSET','LIABILITY','EQUITY','INCOME','EXPENSE']
    settlement_role: Literal['NONE','SELLER_PAYABLE','SUPPLIER_PAYABLE','RECEIVABLE'] = 'NONE'
    counterparty_entity_id: uuid.UUID | None = None

    @model_validator(mode='after')
    def validate_role(self):
        if self.settlement_role in ('SELLER_PAYABLE','SUPPLIER_PAYABLE') and self.kind!='LIABILITY':
            raise ValueError('Payables require a liability account')
        if self.settlement_role=='RECEIVABLE' and self.kind!='ASSET':
            raise ValueError('Receivables require an asset account')
        return self


class LineInput(BaseModel):
    account_id: uuid.UUID
    side: Literal['DEBIT','CREDIT']
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=2)


class EntryInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9:._/-]+$')
    source_ref: str = Field(min_length=8, max_length=160, pattern=r'^SIM:[A-Za-z0-9][A-Za-z0-9:._/-]+$')
    event_kind: str = Field(min_length=3, max_length=60, pattern=r'^[A-Z][A-Z0-9_]+$')
    memo: str = Field(min_length=3, max_length=300)
    lines: list[LineInput] = Field(min_length=2, max_length=30)


class ReverseInput(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9:._/-]+$')
    reason: str = Field(min_length=5, max_length=200)


class ReconcilePreview(BaseModel):
    entry_id: uuid.UUID
    external_reference: str = Field(min_length=4,max_length=200)
    expected_amount: Decimal = Field(ge=0,max_digits=20,decimal_places=2)


def _book_out(b):
    return {'id':str(b.id),'code':b.code,'name':b.name,'currency':b.currency,'state':b.state,
            'legal_entity_id':str(b.legal_entity_id) if b.legal_entity_id else None}


def _account_out(a):
    return {'id':str(a.id),'book_id':str(a.book_id),'code':a.code,'title':a.title,'kind':a.kind,
            'settlement_role':a.settlement_role,'counterparty_entity_id':str(a.counterparty_entity_id) if a.counterparty_entity_id else None}


def _entry_out(e, lines):
    return {'id':str(e.id),'book_id':str(e.book_id),'idempotency_key':e.idempotency_key,
            'event_kind':e.event_kind,'source_ref':e.source_ref,'memo':e.memo,
            'occurred_at':e.occurred_at.isoformat(),'reverses_entry_id':str(e.reverses_entry_id) if e.reverses_entry_id else None,
            'lines':[{'account_id':str(x.account_id),'side':x.side,'amount':f'{x.amount:.2f}'} for x in lines],
            'shadow_only':True,'money_moved':False}


async def _book(db,book_id):
    b=await db.get(LedgerBook,book_id)
    if b is None:raise HTTPException(404,'Shadow book not found')
    if b.state!='shadow':raise HTTPException(409,'Only shadow books are available in F3')
    return b


async def _entry(db,book_id,entry_id):
    e=await db.get(LedgerEntry,entry_id)
    if e is None or e.book_id!=book_id:raise HTTPException(404,'Journal entry not found')
    return e


async def _lines(db,entry_id):
    return (await db.scalars(select(LedgerLine).where(LedgerLine.entry_id==entry_id).order_by(LedgerLine.id))).all()


@router.get('/overview')
async def overview(db:AsyncSession=Depends(get_session)):
    books=(await db.scalars(select(LedgerBook).order_by(LedgerBook.created_at.desc()).limit(100))).all()
    return {'stage':'F3','shadow_only':True,'bank_integrations_enabled':False,'automatic_ledger_postings':False,
            'payouts_enabled':False,'books':[_book_out(x) for x in books],
            'warning':'F3 is a financial simulation ledger. Its balances do not prove any bank payment or real debt.'}


@router.post('/books',status_code=201)
async def create_book(data:BookInput,db:AsyncSession=Depends(get_session)):
    if data.legal_entity_id and not await db.get(LegalEntity,data.legal_entity_id):
        raise HTTPException(404,'Legal entity not found')
    b=LedgerBook(**data.model_dump(),state='shadow',currency='RUB')
    db.add(b)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Shadow book code already exists') from None
    return _book_out(b)


@router.get('/books/{book_id}/accounts')
async def list_accounts(book_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    await _book(db,book_id)
    items=(await db.scalars(select(LedgerAccount).where(LedgerAccount.book_id==book_id).order_by(LedgerAccount.code))).all()
    return [_account_out(x) for x in items]


@router.post('/books/{book_id}/accounts',status_code=201)
async def create_account(book_id:uuid.UUID,data:AccountInput,db:AsyncSession=Depends(get_session)):
    await _book(db,book_id)
    if data.counterparty_entity_id and not await db.get(LegalEntity,data.counterparty_entity_id):
        raise HTTPException(404,'Counterparty legal entity not found')
    a=LedgerAccount(book_id=book_id,**data.model_dump())
    db.add(a)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Account code already exists') from None
    return _account_out(a)


async def _post(db,book_id,data:EntryInput,reverses_id=None):
    await _book(db,book_id)
    try: lines=canonical_lines(data.lines)
    except (ValueError,ArithmeticError):raise HTTPException(422,'Lines must have equal positive debit and credit totals') from None
    digest=fingerprint(book_id,data.source_ref,data.event_kind,data.memo,data.lines,reverses_id)
    found=(await db.scalars(select(LedgerEntry).where(LedgerEntry.book_id==book_id,LedgerEntry.idempotency_key==data.idempotency_key))).first()
    if found:
        if found.request_sha256!=digest:raise HTTPException(409,'Idempotency key already used for different posting')
        return _entry_out(found,await _lines(db,found.id))
    unique_account_ids={uuid.UUID(x['account_id']) for x in lines}
    accounts=(await db.scalars(select(LedgerAccount).where(LedgerAccount.id.in_(unique_account_ids)))).all()
    if len(accounts)!=len(unique_account_ids) or any(a.book_id!=book_id or not a.active for a in accounts):
        raise HTTPException(409,'All posting accounts must be active in this shadow book')
    e=LedgerEntry(book_id=book_id,idempotency_key=data.idempotency_key,request_sha256=digest,
                  source_ref=data.source_ref,event_kind=data.event_kind,memo=data.memo,
                  occurred_at=datetime.now(timezone.utc),reverses_entry_id=reverses_id)
    db.add(e)
    try:await db.flush()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Duplicate source, idempotency key, or reversal') from None
    for x in lines:
        db.add(LedgerLine(entry_id=e.id,account_id=uuid.UUID(x['account_id']),side=x['side'],amount=Decimal(x['amount'])))
    await db.flush()
    e.sealed=True
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Duplicate source, idempotency key, or reversal') from None
    return _entry_out(e,await _lines(db,e.id))


@router.post('/books/{book_id}/entries',status_code=201)
async def post_entry(book_id:uuid.UUID,data:EntryInput,db:AsyncSession=Depends(get_session)):
    return await _post(db,book_id,data)


@router.get('/books/{book_id}/entries')
async def entries(book_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    await _book(db,book_id)
    items=(await db.scalars(select(LedgerEntry).where(LedgerEntry.book_id==book_id).order_by(LedgerEntry.created_at.desc()).limit(150))).all()
    return [_entry_out(x,await _lines(db,x.id)) for x in items]


@router.post('/books/{book_id}/entries/{entry_id}/reverse',status_code=201)
async def reverse_entry(book_id:uuid.UUID,entry_id:uuid.UUID,data:ReverseInput,db:AsyncSession=Depends(get_session)):
    e=await _entry(db,book_id,entry_id)
    if e.reverses_entry_id:raise HTTPException(409,'A reversal cannot be reversed automatically')
    current=(await db.scalars(select(LedgerEntry).where(LedgerEntry.reverses_entry_id==entry_id))).first()
    if current:
        if current.idempotency_key==data.idempotency_key:return _entry_out(current,await _lines(db,current.id))
        raise HTTPException(409,'Already reversed; a second reversal is forbidden')
    orig=await _lines(db,entry_id)
    payload=EntryInput(idempotency_key=data.idempotency_key,source_ref='SIM:REV:'+str(entry_id),event_kind='REVERSAL',
        memo='Reversal: '+data.reason,
        lines=[LineInput(account_id=x.account_id,side='CREDIT' if x.side=='DEBIT' else 'DEBIT',amount=x.amount) for x in orig])
    return await _post(db,book_id,payload,reverses_id=entry_id)


async def _balances(db,book_id):
    await _book(db,book_id)
    accounts=(await db.scalars(select(LedgerAccount).where(LedgerAccount.book_id==book_id).order_by(LedgerAccount.code))).all()
    totals=await db.execute(select(LedgerLine.account_id,
        func.sum(case((LedgerLine.side=='DEBIT',LedgerLine.amount),else_=Decimal('0'))).label('debit'),
        func.sum(case((LedgerLine.side=='CREDIT',LedgerLine.amount),else_=Decimal('0'))).label('credit'))
        .join(LedgerEntry,LedgerEntry.id==LedgerLine.entry_id)
        .where(LedgerEntry.book_id==book_id).group_by(LedgerLine.account_id))
    sums={row[0]:(row[1] or Decimal('0'),row[2] or Decimal('0')) for row in totals.all()}
    result=[]
    for a in accounts:
        dr,cr=sums.get(a.id,(Decimal('0'),Decimal('0')))
        bal=signed_balance(a.kind,dr,cr)
        result.append({**_account_out(a),'debit':f'{dr:.2f}','credit':f'{cr:.2f}','balance':f'{bal:.2f}'})
    return result


@router.get('/books/{book_id}/trial-balance')
async def trial_balance(book_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    rows=await _balances(db,book_id)
    dr=sum((Decimal(x['debit']) for x in rows),Decimal('0'))
    cr=sum((Decimal(x['credit']) for x in rows),Decimal('0'))
    return {'book_id':str(book_id),'accounts':rows,'total_debit':f'{dr:.2f}',
            'total_credit':f'{cr:.2f}','balanced':dr==cr,'shadow_only':True}


@router.get('/books/{book_id}/settlement-positions')
async def settlement_positions(book_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    rows=await _balances(db,book_id)
    return {'book_id':str(book_id),'shadow_only':True,
            'positions':[x for x in rows if x['settlement_role']!='NONE'],
            'warning':'Simulated positions are not payout instructions or confirmation of debt.'}


@router.post('/books/{book_id}/reconcile-preview')
async def reconcile_preview(book_id:uuid.UUID,data:ReconcilePreview,db:AsyncSession=Depends(get_session)):
    await _entry(db,book_id,data.entry_id)
    lines=await _lines(db,data.entry_id)
    recorded=sum((x.amount for x in lines if x.side=='DEBIT'),Decimal('0'))
    return {'status':'MATCH' if recorded==data.expected_amount else 'MISMATCH',
            'external_reference':data.external_reference,'expected_amount':f'{data.expected_amount:.2f}',
            'recorded_turnover':f'{recorded:.2f}','shadow_only':True,
            'warning':'Manual preview only; bank identity, payment authenticity and settlement are not verified.'}
