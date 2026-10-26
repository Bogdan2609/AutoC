"""Admin-only finance foundation. No payment, checkout or procurement write path."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.finance.models import EntityRole, LegalEntity, TaxAudit, TaxProfile, now
from app.modules.finance.tax_engine import (PreviewInput, THRESHOLDS_2026,
                                            THRESHOLDS_FUTURE, preview)
from app.modules.marketplace.models import Merchant

router = APIRouter(prefix='/api/v1/backoffice/finance', tags=['finance-f1'],
                   dependencies=[Depends(require_admin)])


def valid_inn(inn: str) -> bool:
    if not inn.isdigit():
        return False
    n = [int(x) for x in inn]
    def cd(weights: list[int]) -> int:
        return sum(a*b for a,b in zip(weights,n)) % 11 % 10
    if len(n) == 10:
        return cd([2,4,10,3,5,9,4,6,8]) == n[9]
    if len(n) == 12:
        return (cd([7,2,4,10,3,5,9,4,6,8]) == n[10] and
                cd([3,7,2,4,10,3,5,9,4,6,8]) == n[11])
    return False


class CreateEntity(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    entity_type: Literal['company','sole_proprietor','individual','self_employed']
    merchant_id: uuid.UUID | None = None
    inn: str | None = Field(default=None, pattern=r'^\d{10}(\d{2})?$')
    kpp: str | None = Field(default=None, pattern=r'^\d{9}$')
    roles: list[Literal['platform_owner','seller','procurement_buyer','supplier','payment_recipient']] = Field(default_factory=list, max_length=5)

    @model_validator(mode='after')
    def validate(self):
        if len(set(self.roles)) != len(self.roles):
            raise ValueError('Duplicate roles')
        if self.entity_type != 'company' and self.kpp is not None:
            raise ValueError('KPP is for legal companies only')
        if self.inn and len(self.inn) != (10 if self.entity_type == 'company' else 12):
            raise ValueError('INN length does not match entity type')
        if self.inn and not valid_inn(self.inn):
            raise ValueError('Russian INN checksum failed')
        if self.entity_type in ('individual','self_employed') and ('platform_owner' in self.roles or 'procurement_buyer' in self.roles):
            raise ValueError('Individual scenarios require separate legal review')
        return self


class EntityVerification(BaseModel):
    evidence_reference: str = Field(min_length=5, max_length=250)
    administrator_attestation: Literal[True]


class CreateProfile(BaseModel):
    entity_id: uuid.UUID
    valid_from: date
    valid_until: date | None = None
    regime: Literal['OSNO','USN_INCOME','USN_PROFIT','AUSN_INCOME','AUSN_PROFIT']
    vat_scheme: Literal['STANDARD','USN_5','USN_7','EXEMPT','UNRESOLVED'] = 'UNRESOLVED'
    prior_year_income: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=2)
    current_year_income: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=2)
    external_income_confirmed: bool = False
    evidence_reference: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def validate(self):
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValueError('End date precedes start date')
        if self.regime == 'OSNO' and self.vat_scheme not in ('STANDARD','UNRESOLVED'):
            raise ValueError('OSNO domestic VAT requires STANDARD or UNRESOLVED')
        if self.regime.startswith('AUSN') and self.vat_scheme not in ('EXEMPT','UNRESOLVED'):
            raise ValueError('AUSN domestic sale cannot use standard/special USN VAT')
        if self.valid_from.year < 2026:
            raise ValueError('F1 supports tax rules effective from 2026 only')
        return self


class ProfileActivation(BaseModel):
    confirmed_by: str = Field(min_length=3, max_length=100)
    confirm_tax_regime_and_external_income: Literal[True]
    confirm_vat_choice_and_eligibility: Literal[True]
    confirm_supporting_documents: Literal[True]


class RetireProfile(BaseModel):
    valid_until: date
    confirmed_by: str = Field(min_length=3, max_length=100)


def entity_data(e: LegalEntity):
    return {'id':str(e.id),'merchant_id':str(e.merchant_id) if e.merchant_id else None,
            'name':e.name,'entity_type':e.entity_type,'inn':e.inn,'kpp':e.kpp,
            'verification_status':e.verification_status}


def profile_data(p: TaxProfile):
    return {'id':str(p.id), 'entity_id':str(p.entity_id), 'valid_from':p.valid_from.isoformat(),
            'valid_until':p.valid_until.isoformat() if p.valid_until else None,
            'regime':p.regime,'vat_scheme':p.vat_scheme,'status':p.status,
            'external_income_confirmed':p.external_income_confirmed,
            'prior_year_income':str(p.prior_year_income) if p.prior_year_income is not None else None,
            'current_year_income':str(p.current_year_income) if p.current_year_income is not None else None,
            'evidence_reference':p.evidence_reference,'verified_by':p.verified_by}


@router.get('/overview')
async def overview(db: AsyncSession = Depends(get_session)):
    entities=(await db.scalars(select(LegalEntity).order_by(LegalEntity.name).limit(200))).all()
    profiles=(await db.scalars(select(TaxProfile).order_by(TaxProfile.created_at.desc()).limit(500))).all()
    return {'stage':'F1','production_tax_integration_enabled':False,
            'entities':[entity_data(e) for e in entities],
            'profiles':[profile_data(p) for p in profiles],
            'warning':'No company or tax regime is inferred; F1 does not affect existing checkout.'}


@router.post('/legal-entities', status_code=201)
async def create_entity(data: CreateEntity, db: AsyncSession=Depends(get_session)):
    if data.merchant_id and not await db.get(Merchant, data.merchant_id):
        raise HTTPException(404, 'Merchant does not exist')
    entity=LegalEntity(name=data.name, entity_type=data.entity_type, merchant_id=data.merchant_id,
                       inn=data.inn, kpp=data.kpp)
    db.add(entity)
    await db.flush()
    for role in data.roles:
        db.add(EntityRole(entity_id=entity.id, role=role))
    db.add(TaxAudit(entity_id=entity.id, action='entity_created', payload={'type':data.entity_type, 'roles':data.roles}))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'Duplicate tax identity or merchant linkage') from None
    return entity_data(entity)


@router.post('/legal-entities/{entity_id}/verify')
async def verify_entity(entity_id:uuid.UUID, data:EntityVerification, db:AsyncSession=Depends(get_session)):
    entity=await db.get(LegalEntity,entity_id)
    if not entity:raise HTTPException(404,'Legal entity not found')
    if entity.entity_type not in ('company','sole_proprietor'):
        raise HTTPException(409,'Individual sellers require separate legal validation')
    if not entity.inn:
        raise HTTPException(409,'INN required for legal verification')
    entity.verification_status='verified'
    db.add(TaxAudit(entity_id=entity_id,action='entity_manually_verified',
                    payload={'evidence_reference':data.evidence_reference, 'method':'operator_attestation'}))
    await db.commit()
    return entity_data(entity)


@router.get('/tax-profiles')
async def list_profiles(entity_id:uuid.UUID|None=None,db:AsyncSession=Depends(get_session)):
    q=select(TaxProfile).order_by(TaxProfile.valid_from.desc()).limit(250)
    if entity_id:q=q.where(TaxProfile.entity_id==entity_id)
    return [profile_data(p) for p in (await db.scalars(q)).all()]


@router.post('/tax-profiles', status_code=201)
async def create_profile(data:CreateProfile,db:AsyncSession=Depends(get_session)):
    e=await db.get(LegalEntity,data.entity_id)
    if not e:raise HTTPException(404,'Legal entity not found')
    if e.entity_type not in ('company','sole_proprietor'):
        raise HTTPException(409,'Individual tax schemes not yet supported in F1')
    obj=TaxProfile(**data.model_dump(),status='draft')
    db.add(obj)
    db.add(TaxAudit(entity_id=e.id,action='profile_draft_created',
                    payload={'effective_from':data.valid_from.isoformat(),'regime':data.regime,'vat_scheme':data.vat_scheme}))
    try:await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'Profile start date already exists for this entity') from None
    return profile_data(obj)


@router.post('/tax-profiles/{profile_id}/activate')
async def activate_profile(profile_id:uuid.UUID,data:ProfileActivation,db:AsyncSession=Depends(get_session)):
    p=await db.get(TaxProfile,profile_id)
    if not p:raise HTTPException(404,'Profile not found')
    # Lock entity row, serializing competing profile activations in PostgreSQL.
    e=(await db.scalars(select(LegalEntity).where(LegalEntity.id==p.entity_id).with_for_update())).one()
    if p.status!='draft' or e.verification_status!='verified' or p.vat_scheme=='UNRESOLVED':
        raise HTTPException(409,'Requires a draft, verified legal identity and resolved VAT scheme')
    if not p.external_income_confirmed or not p.evidence_reference:
        raise HTTPException(409,'External income and supporting evidence not confirmed')
    if p.regime.startswith('USN') and (p.prior_year_income is None or p.current_year_income is None):
        raise HTTPException(409,'USN activation requires verified aggregate prior/current income')
    if p.regime=='OSNO' and p.vat_scheme!='STANDARD':
        raise HTTPException(409,'OSNO profile requires standard domestic VAT')
    if p.regime.startswith('USN') and p.vat_scheme=='EXEMPT' and (
        p.prior_year_income is None or p.current_year_income is None):
        raise HTTPException(409,'USN VAT exemption needs aggregate income evidence')
    active=(await db.scalars(select(TaxProfile).where(
        TaxProfile.entity_id==p.entity_id, TaxProfile.status.in_(['active','retired'])))).all()
    end=p.valid_until or date.max
    for old in active:
        old_end=old.valid_until or date.max
        if p.valid_from<=old_end and old.valid_from<=end:
            raise HTTPException(409,'Another active tax profile overlaps the requested date range')
    p.status='active';p.verified_by=data.confirmed_by;p.activated_at=now()
    db.add(TaxAudit(entity_id=p.entity_id,action='profile_activated',payload={
        'profile_id':str(p.id),'confirmed_by':data.confirmed_by,
        'regime':p.regime,'vat_scheme':p.vat_scheme}))
    await db.commit()
    return profile_data(p)


@router.post('/tax-profiles/{profile_id}/retire')
async def retire_profile(profile_id:uuid.UUID,data:RetireProfile,db:AsyncSession=Depends(get_session)):
    p=await db.get(TaxProfile,profile_id)
    if not p:raise HTTPException(404,'Profile not found')
    await db.scalar(select(LegalEntity.id).where(LegalEntity.id==p.entity_id).with_for_update())
    if p.status!='active' or data.valid_until<p.valid_from:
        raise HTTPException(409,'Only active profiles may be retired with a valid end date')
    p.valid_until=data.valid_until;p.status='retired'
    db.add(TaxAudit(entity_id=p.entity_id,action='profile_retired',payload={
        'profile_id':str(p.id),'valid_until':data.valid_until.isoformat(),'confirmed_by':data.confirmed_by}))
    await db.commit()
    return profile_data(p)


@router.get('/tax-rules/2026')
async def statutory_baseline():
    return {'source':'https://www.nalog.gov.ru/new2026/',
            'year':2026,'standard_vat_percent':'22','usn_special_vat_percent':['5','7'],
            'threshold_rub':THRESHOLDS_2026,'future_exemption_rub':THRESHOLDS_FUTURE,
            'scope':'informational baseline; not automatic legal eligibility certification'}


@router.post('/tax/preview')
async def preview_vat(data:PreviewInput,db:AsyncSession=Depends(get_session)):
    # Unbound preview is always an explicit hypothetical; bound preview requires
    # real active verified dates and matching VAT mode before using the profile.
    if data.tax_profile_id:
        try:pid=uuid.UUID(data.tax_profile_id)
        except ValueError:raise HTTPException(422,'Invalid profile UUID') from None
        p=await db.get(TaxProfile,pid)
        if not p or p.status not in ('active','retired') or p.valid_from>data.operation_date or (
                p.valid_until and p.valid_until<data.operation_date):
            raise HTTPException(409,'No active tax profile covers operation date')
        e=await db.get(LegalEntity,p.entity_id)
        if e.verification_status!='verified' or p.vat_scheme!=data.vat_scheme:
            raise HTTPException(409,'Tax profile is not verified or VAT scheme differs')
    return preview(data)


@router.get('/tax/audit')
async def finance_audit(db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(TaxAudit).order_by(TaxAudit.created_at.desc()).limit(100))).all()
    return [{'entity_id':str(r.entity_id) if r.entity_id else None,'action':r.action,
             'payload':r.payload,'created_at':r.created_at.isoformat()} for r in rows]
