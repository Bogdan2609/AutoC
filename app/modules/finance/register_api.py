"""F5 SHADOW-only tax register preview; authenticated and strictly synthetic.

Never reads live orders, bank integrations or F4 payment ledger; no uploads to FNS/1C.
A CSV export is a working draft, NOT a statutory invoice, KUDiR, VAT book or declaration.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.finance.register_engine import ACTOR_KINDS, DOC_KINDS, EXPORT_FORMATS, calculate_vat, export_lines, digest_bytes
from app.modules.finance.register_models import ShadowTaxBook,ShadowTaxDocument,ShadowTaxRow

router=APIRouter(prefix='/api/v1/backoffice/finance/tax-registers-sim',tags=['finance-f5-shadow'],dependencies=[Depends(require_admin)])
BOOK_RE=r'^SIM:F5:BOOK:[A-Za-z0-9][A-Za-z0-9:._/-]*$'
DOC_RE=r'^SIM:F5:DOC:[A-Za-z0-9][A-Za-z0-9:._/-]*$'
ROW_RE=r'^SIM:F5:ROW:[A-Za-z0-9][A-Za-z0-9:._/-]*$'

class BookCreate(BaseModel):
    book_ref:str=Field(min_length=15,max_length=160,pattern=BOOK_RE)
    actor_role:Literal['PLATFORM','SELLER']
    period_start:date
    period_end:date
    @model_validator(mode='after')
    def dates(self):
        if self.period_start>self.period_end or self.period_start.year!=2026 or self.period_end.year!=2026:
            raise ValueError('F5 preview restricted to 2026; future legal formats require versioned review')
        return self

class DocumentCreate(BaseModel):
    document_ref:str=Field(min_length=12,max_length=160,pattern=DOC_RE)
    document_date:date
    kind:Literal['SALE','PURCHASE','AGENT_REPORT','ADVANCE','REFUND','EXPENSE']
    memo:str=Field(default='SIMULATION ONLY',min_length=1,max_length=300)
    @model_validator(mode='after')
    def dates(self):
        if self.document_date.year!=2026:raise ValueError('Document date must be in reviewed year 2026')
        return self

class RowCreate(BaseModel):
    request_key:str=Field(min_length=12,max_length=160,pattern=ROW_RE)
    document_id:uuid.UUID
    event_kind:Literal['OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE']
    operation_date:date
    gross_amount:Decimal=Field(gt=0,max_digits=20,decimal_places=2)
    vat_scheme:Literal['UNRESOLVED','STANDARD','USN_5','USN_7','EXEMPT']='UNRESOLVED'
    tax_class:Literal['STANDARD_GOODS','REDUCED_10','ZERO','EXEMPT']='STANDARD_GOODS'
    correction_of_id:uuid.UUID|None=None
    @model_validator(mode='after')
    def values(self):
        if not self.gross_amount.is_finite():raise ValueError('Finite monetary value required')
        if self.event_kind=='REFUND' and not self.correction_of_id:raise ValueError('Refund requires reference to original event')
        if self.event_kind!='REFUND' and self.correction_of_id:raise ValueError('Only refunds can reference original event')
        if self.vat_scheme=='UNRESOLVED' and self.tax_class!='STANDARD_GOODS':
            raise ValueError('Cannot claim a preferential tax class with unresolved VAT')
        return self


def book_out(b):
    return {'id':str(b.id),'book_ref':b.book_ref,'actor_role':b.actor_role,
            'period_start':b.period_start.isoformat(),'period_end':b.period_end.isoformat(),
            'tax_profile_status':'UNRESOLVED','shadow_only':True,'filing_ready':False}

def doc_out(d):return {'id':str(d.id),'document_ref':d.document_ref,'kind':d.kind,'document_date':d.document_date.isoformat(),'memo':d.memo,'shadow_only':True}

def row_out(r):
    return {'id':str(r.id),'book_id':str(r.book_id),'document_id':str(r.document_id),
            'request_key':r.request_key,'event_kind':r.event_kind,'operation_date':r.operation_date.isoformat(),
            'gross_amount':f'{r.gross_amount:.2f}','vat_scheme':r.vat_scheme,'tax_class':r.tax_class,
            'net_preview':f'{r.net_amount:.2f}' if r.net_amount is not None else None,
            'vat_preview':f'{r.vat_amount:.2f}' if r.vat_amount is not None else None,
            'correction_of_id':str(r.correction_of_id) if r.correction_of_id else None,
            'shadow_only':True,'filing_ready':False}

async def commit(db,what):
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,f'Conflicting {what} already exists') from None

@router.get('/overview')
async def overview(db:AsyncSession=Depends(get_session)):
    books=(await db.scalars(select(ShadowTaxBook).order_by(ShadowTaxBook.created_at.desc()).limit(100))).all()
    docs=(await db.scalars(select(ShadowTaxDocument).order_by(ShadowTaxDocument.created_at.desc()).limit(100))).all()
    n=(await db.scalar(select(func.count()).select_from(ShadowTaxRow))) or 0
    return {'status':'SHADOW_ONLY','filing_enabled':False,'tax_obligations_posted':False,'bank_reads':False,
            'official_exports_enabled':False,'books':[book_out(x) for x in books],
            'documents':[doc_out(x) for x in docs],'counts':{'books':len(books),'documents':len(docs),'rows':n},
            'formats':sorted(EXPORT_FORMATS),'warnings':['Preview CSV is NOT an official book, declaration or tax obligation.',
             'No verified taxpayer or legal tax regime configured.','Do not use simulated bank or receipt plans as tax evidence.']}

@router.post('/books',status_code=201)
async def add_book(data:BookCreate,db:AsyncSession=Depends(get_session)):
    prior=await db.scalar(select(ShadowTaxBook).where(ShadowTaxBook.book_ref==data.book_ref))
    if prior:
        if prior.actor_role!=data.actor_role or prior.period_start!=data.period_start or prior.period_end!=data.period_end:
            raise HTTPException(409,'Book reference reused with conflicting fields')
        return book_out(prior)
    item=ShadowTaxBook(**data.model_dump(),currency='RUB',tax_profile_status='UNRESOLVED')
    db.add(item);await commit(db,'tax book');return book_out(item)

@router.post('/documents',status_code=201)
async def add_document(data:DocumentCreate,db:AsyncSession=Depends(get_session)):
    prior=await db.scalar(select(ShadowTaxDocument).where(ShadowTaxDocument.document_ref==data.document_ref))
    if prior:
        if (prior.document_date,prior.kind,prior.memo)!=(data.document_date,data.kind,data.memo):
            raise HTTPException(409,'Document reference reused with conflicting fields')
        return doc_out(prior)
    item=ShadowTaxDocument(**data.model_dump());db.add(item);await commit(db,'source document');return doc_out(item)

@router.post('/books/{book_id}/rows',status_code=201)
async def add_row(book_id:uuid.UUID,data:RowCreate,db:AsyncSession=Depends(get_session)):
    book=await db.get(ShadowTaxBook,book_id)
    if book is None:raise HTTPException(404,'Shadow tax book not found')
    prior=await db.scalar(select(ShadowTaxRow).where(ShadowTaxRow.request_key==data.request_key))
    if prior:
        fields=('book_id','document_id','event_kind','operation_date','gross_amount','vat_scheme','tax_class','correction_of_id')
        source=data.model_dump()|{'book_id':book_id}
        if any(getattr(prior,f)!=source[f] for f in fields):raise HTTPException(409,'Row key reused with conflicting payload')
        return row_out(prior)
    doc=await db.get(ShadowTaxDocument,data.document_id)
    if doc is None:raise HTTPException(404,'Synthetic document not found')
    if not(book.period_start<=data.operation_date<=book.period_end):raise HTTPException(422,'Date outside register period')
    if data.event_kind not in ACTOR_KINDS[book.actor_role]:raise HTTPException(422,'Operation belongs to different legal role')
    if doc.kind not in DOC_KINDS[data.event_kind]:raise HTTPException(422,'Source document type does not support operation')
    if data.event_kind=='REFUND':
        original=await db.scalar(select(ShadowTaxRow).where(ShadowTaxRow.id==data.correction_of_id).with_for_update())
        if original is None or original.book_id!=book.id or original.event_kind in ('REFUND','PURCHASE','EXPENSE'):
            raise HTTPException(422,'Refund must reference original sale or advance in same book')
        if original.vat_scheme!=data.vat_scheme or original.tax_class!=data.tax_class:
            raise HTTPException(422,'Refund VAT mode must match original preview')
        rest=await db.scalar(select(func.coalesce(func.sum(ShadowTaxRow.gross_amount),0)).where(ShadowTaxRow.correction_of_id==original.id))
        if Decimal(rest)+data.gross_amount>original.gross_amount:raise HTTPException(409,'Refund exceeds uncorrected original')
    try:
        net,vat=calculate_vat(data.event_kind,data.gross_amount,data.vat_scheme,data.tax_class,data.operation_date,doc.document_ref)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    row=ShadowTaxRow(book_id=book.id,**data.model_dump(),net_amount=net,vat_amount=vat)
    db.add(row);await commit(db,'register row');return row_out(row)

@router.get('/books/{book_id}/rows')
async def rows(book_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    book=await db.get(ShadowTaxBook,book_id)
    if book is None:raise HTTPException(404,'Book not found')
    entries=(await db.scalars(select(ShadowTaxRow).where(ShadowTaxRow.book_id==book_id).order_by(ShadowTaxRow.operation_date,ShadowTaxRow.created_at).limit(5000))).all()
    return {'book':book_out(book),'rows':[row_out(x) for x in entries],'count':len(entries),'shadow_only':True,'filing_ready':False}

@router.get('/books/{book_id}/export/{format_name}')
async def csv_export(book_id:uuid.UUID,format_name:str,db:AsyncSession=Depends(get_session)):
    if format_name not in EXPORT_FORMATS:raise HTTPException(404,'Preview format not found')
    book=await db.get(ShadowTaxBook,book_id)
    if book is None:raise HTTPException(404,'Book not found')
    row_count=await db.scalar(select(func.count()).select_from(ShadowTaxRow).where(ShadowTaxRow.book_id==book_id))
    if row_count>5000:
        raise HTTPException(413,'Export exceeds 5000 rows; use a bounded date range or background export')
    entries=(await db.scalars(select(ShadowTaxRow).where(ShadowTaxRow.book_id==book_id)
           .order_by(ShadowTaxRow.operation_date,ShadowTaxRow.created_at).limit(5000))).all()
    docs=(await db.scalars(select(ShadowTaxDocument).where(ShadowTaxDocument.id.in_([x.document_id for x in entries])))).all() if entries else []
    content,count=export_lines(book,docs,entries,format_name)
    return Response(content,media_type='text/csv; charset=utf-8',headers={
        'Content-Disposition':f'attachment; filename="SIM_F5_{format_name}_{book.period_start.year}.csv"',
        'X-Shadow-Only':'true','X-Filing-Ready':'false','X-Row-Count':str(count),
        'X-Content-SHA256':digest_bytes(content),'Cache-Control':'no-store',
    })
