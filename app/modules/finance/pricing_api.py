"""F2 admin-only shadow pricing rules and deterministic calculation previews."""
from datetime import date
from decimal import Decimal
from typing import Literal
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.finance.models import LegalEntity, TaxProfile, TaxAudit
from app.modules.finance.pricing_models import PricingRule, FeeSchedule
from app.modules.finance.pricing_engine import PricePreview, RoundingTier, calculate, validate_rounding_tiers
from app.modules.marketplace.models import Merchant
from app.modules.catalog.models import Product, Brand
from app.modules.catalog_ext.models import Category
from app.modules.suppliers.models import Supplier

router=APIRouter(prefix='/api/v1/backoffice/finance/pricing', tags=['finance-f2'],dependencies=[Depends(require_admin)])

class RuleInput(BaseModel):
    name: str = Field(min_length=2,max_length=160)
    scope_kind: Literal['GLOBAL','MERCHANT','SUPPLIER','CATEGORY','BRAND','PRODUCT'] = 'GLOBAL'
    scope_id: uuid.UUID | None = None
    priority: int = Field(default=100,ge=0,le=100000)
    markup_percent: Decimal = Field(default=Decimal('0'),ge=0,le=500,max_digits=8,decimal_places=2)
    fixed_markup: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    floor_price: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    min_contribution: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    rounding_mode: Literal['EXACT_RUB','SMART_UP'] = 'EXACT_RUB'
    rounding_max_uplift_pct: Decimal | None = Field(default=None,ge=0,le=10,max_digits=5,decimal_places=2)
    rounding_tiers: list[RoundingTier] | None = None
    valid_from: date
    valid_until: date | None = None

    @model_validator(mode='after')
    def check(self):
        if (self.scope_kind=='GLOBAL') != (self.scope_id is None):
            raise ValueError('GLOBAL requires no UUID; scoped rule requires UUID')
        if self.valid_until and self.valid_until < self.valid_from:raise ValueError('Invalid period')
        if self.valid_from.year<2026:raise ValueError('F2 supports periods from 2026 onwards')
        if self.rounding_tiers is not None:validate_rounding_tiers(self.rounding_tiers)
        return self

class FeeInput(BaseModel):
    name: str = Field(min_length=2,max_length=160)
    fee_kind: Literal['SBP','MARKETPLACE','LOGISTICS','OTHER']
    merchant_id: uuid.UUID | None = None
    percent: Decimal = Field(default=Decimal('0'),ge=0,le=100,max_digits=7,decimal_places=3)
    fixed_amount: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    cap_amount: Decimal | None = Field(default=None,ge=0,max_digits=20,decimal_places=2)
    valid_from: date
    valid_until: date | None = None
    evidence_reference: str = Field(min_length=5,max_length=400)

    @model_validator(mode='after')
    def check(self):
        if self.valid_until and self.valid_until < self.valid_from:raise ValueError('Invalid period')
        if self.valid_from.year<2026:raise ValueError('F2 supports periods from 2026 onwards')
        if self.fee_kind=='SBP' and self.percent>Decimal('10'):
            raise ValueError('SBP schedule must not exceed 10% without a new reviewed pricing policy')
        return self

def rule_out(x:PricingRule):
    return {'id':str(x.id),'name':x.name,'scope_kind':x.scope_kind,'scope_id':str(x.scope_id) if x.scope_id else None,'state':x.state,'priority':x.priority,'markup_percent':str(x.markup_percent),'fixed_markup':str(x.fixed_markup),'floor_price':str(x.floor_price),'min_contribution':str(x.min_contribution),'rounding_mode':x.rounding_mode,'rounding_max_uplift_pct':str(x.rounding_max_uplift_pct) if x.rounding_max_uplift_pct is not None else None,'rounding_tiers':x.rounding_tiers,'valid_from':str(x.valid_from),'valid_until':str(x.valid_until) if x.valid_until else None}

def fee_out(x:FeeSchedule):
    return {'id':str(x.id),'name':x.name,'fee_kind':x.fee_kind,'merchant_id':str(x.merchant_id) if x.merchant_id else None,'state':x.state,'percent':str(x.percent),'fixed_amount':str(x.fixed_amount),'cap_amount':str(x.cap_amount) if x.cap_amount is not None else None,'valid_from':str(x.valid_from),'valid_until':str(x.valid_until) if x.valid_until else None,'evidence_reference':x.evidence_reference}

@router.get('/overview')
async def overview(db:AsyncSession=Depends(get_session)):
    rules=(await db.scalars(select(PricingRule).order_by(PricingRule.created_at.desc()).limit(200))).all()
    fees=(await db.scalars(select(FeeSchedule).order_by(FeeSchedule.created_at.desc()).limit(200))).all()
    return {'stage':'F2','live_pricing_integration_enabled':False,'shared_pricing_arithmetic_enabled':True,'payment_and_procurement_enabled':False,
            'pricing_rules':[rule_out(x) for x in rules],'fee_schedules':[fee_out(x) for x in fees],
            'warning':'Shadow calculation only; existing PricePolicy remains in effect, no real offer or order modified.'}

async def check_scope(db,kind,uid):
    if kind=='GLOBAL':return
    model={'MERCHANT':Merchant,'SUPPLIER':Supplier,'CATEGORY':Category,'BRAND':Brand,'PRODUCT':Product}[kind]
    if not await db.get(model,uid):
        raise HTTPException(404,f'{kind} ID not found')

@router.post('/rules',status_code=201)
async def create_rule(data:RuleInput,db:AsyncSession=Depends(get_session)):
    await check_scope(db,data.scope_kind,data.scope_id)
    payload=data.model_dump(exclude={'rounding_tiers'})
    payload['rounding_tiers']=[x.model_dump(mode='json') for x in data.rounding_tiers] if data.rounding_tiers else None
    row=PricingRule(**payload,state='draft');db.add(row)
    db.add(TaxAudit(entity_id=None,action='f2_pricing_rule_created',payload={'name':data.name,'scope':data.scope_kind}))
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Duplicate pricing rule name and starting date') from None
    return rule_out(row)

@router.post('/fees',status_code=201)
async def create_fee(data:FeeInput,db:AsyncSession=Depends(get_session)):
    if data.merchant_id and not await db.get(Merchant,data.merchant_id):raise HTTPException(404,'Merchant not found')
    row=FeeSchedule(**data.model_dump(),state='draft');db.add(row)
    db.add(TaxAudit(entity_id=None,action='f2_fee_schedule_created',payload={'name':data.name,'kind':data.fee_kind}))
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'Duplicate fee schedule name and starting date') from None
    return fee_out(row)

@router.post('/rules/{rule_id}/retire')
async def retire_rule(rule_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(PricingRule,rule_id)
    if not row:raise HTTPException(404,'Rule not found')
    if row.state=='retired':raise HTTPException(409,'Rule already retired')
    row.state='retired';db.add(TaxAudit(entity_id=None,action='f2_pricing_rule_retired',payload={'rule_id':str(rule_id)}))
    await db.commit();return rule_out(row)

@router.post('/fees/{fee_id}/retire')
async def retire_fee(fee_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(FeeSchedule,fee_id)
    if not row:raise HTTPException(404,'Fee not found')
    if row.state=='retired':raise HTTPException(409,'Fee already retired')
    row.state='retired';db.add(TaxAudit(entity_id=None,action='f2_fee_schedule_retired',payload={'fee_id':str(fee_id)}))
    await db.commit();return fee_out(row)

@router.post('/preview')
async def pricing_preview(data:PricePreview,db:AsyncSession=Depends(get_session)):
    if data.tax_profile_id:
        try:uid=uuid.UUID(data.tax_profile_id)
        except (ValueError,TypeError):raise HTTPException(422,'Invalid tax profile ID') from None
        profile=await db.get(TaxProfile,uid)
        if (not profile or profile.status not in ('active','retired') or profile.valid_from>data.operation_date or
            (profile.valid_until and profile.valid_until<data.operation_date) or profile.vat_scheme!=data.vat_scheme):
            raise HTTPException(409,'Tax profile not valid for operation and VAT mode')
        entity=await db.get(LegalEntity,profile.entity_id)
        if not entity or entity.verification_status!='verified':raise HTTPException(409,'Unverified seller tax profile')
    async def chosen(model,raw,kind=None):
        try:uid=uuid.UUID(raw)
        except (ValueError,TypeError):raise HTTPException(422,'Invalid pricing rule / fee UUID') from None
        row=await db.get(model,uid)
        if not row or row.state=='retired' or not (row.valid_from<=data.operation_date and (row.valid_until is None or data.operation_date<=row.valid_until)):
            raise HTTPException(409,'Selected pricing/fee rule is unavailable on the operation date')
        if kind is not None and row.fee_kind!=kind:
            raise HTTPException(409,'Selected fee schedule has wrong kind')
        return row
    patch={}
    if data.pricing_rule_id:
        if data.sale_model!='OWN':raise HTTPException(409,'Markup policy applies to own sales only')
        r=await chosen(PricingRule,data.pricing_rule_id)
        field={'MERCHANT':'merchant_id','SUPPLIER':'supplier_id','CATEGORY':'category_id','BRAND':'brand_id','PRODUCT':'product_id'}.get(r.scope_kind)
        if field and str(r.scope_id)!=str(getattr(data,field)):
            raise HTTPException(409,'Selected rule scope does not match preview object')
        patch.update(markup_percent=r.markup_percent,fixed_markup=r.fixed_markup,floor_price=r.floor_price,min_contribution=r.min_contribution,rounding_mode=r.rounding_mode,rounding_max_uplift_pct=r.rounding_max_uplift_pct,rounding_tiers=r.rounding_tiers)
    if data.sbp_fee_schedule_id:
        f=await chosen(FeeSchedule,data.sbp_fee_schedule_id,'SBP')
        if f.merchant_id and str(f.merchant_id)!=str(data.merchant_id):raise HTTPException(409,'Merchant bank fee mismatch')
        patch.update(sbp_percent=f.percent,sbp_fixed=f.fixed_amount,sbp_cap=f.cap_amount)
    if data.marketplace_fee_schedule_id:
        if data.sale_model!='AGENCY':raise HTTPException(409,'Marketplace fee requires agency sale')
        f=await chosen(FeeSchedule,data.marketplace_fee_schedule_id,'MARKETPLACE')
        if f.merchant_id and str(f.merchant_id)!=str(data.merchant_id):raise HTTPException(409,'Merchant fee mismatch')
        patch.update(marketplace_percent=f.percent,marketplace_fixed=f.fixed_amount,marketplace_cap=f.cap_amount)
    result=calculate(PricePreview.model_validate({**data.model_dump(mode='json'),**patch}))
    result['applied_rule_ids']={key:str(getattr(data,key)) for key in ('pricing_rule_id','sbp_fee_schedule_id','marketplace_fee_schedule_id') if getattr(data,key)}
    return result
