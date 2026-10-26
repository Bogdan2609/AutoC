"""Admin-only reviewed pricing and automatic imported-catalog reconciliation.

No route can publish a Product, enable a source feed, charge SBP or place an order.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import Job
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from .models import FeedPriceRule, FeedCatalogSync, FeedWarehouseMap, FeedSource, FeedBatch, FeedOffer, utc
from .service import validate_warehouse_map
from .pricing import quote_unit, usable_rule
from .pipeline import queue_sync, view

router=APIRouter(prefix='/api/v1/admin/supplier-feeds',tags=['supplier-feeds-commercial-v0315'],dependencies=[Depends(require_admin)])


class PricePolicyInput(BaseModel):
    supplier_code: Literal['rossko','partkom']
    account_id: UUID
    destination_id: UUID
    warehouse_code: str = Field(min_length=1,max_length=140)
    markup_percent: Decimal = Field(ge=0,le=1000,max_digits=8,decimal_places=2)
    supplier_delivery: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    packaging: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    other_costs: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    tax_reserve: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    sbp_percent: Decimal = Field(ge=0,lt=100,max_digits=7,decimal_places=3)
    sbp_fixed: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    min_contribution: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    evidence_reference: str = Field(min_length=12,max_length=300)

FIELDS=('markup_percent','supplier_delivery','packaging','other_costs','tax_reserve','sbp_percent','sbp_fixed','min_contribution')


def pv(p):
    return {'id':str(p.id),'supplier_code':p.supplier_code,'account_id':str(p.account_id),
       'destination_id':str(p.destination_id),'warehouse_code':p.warehouse_code,
       'version':p.version,'approved':p.approved,'tax_status_verified':p.tax_status_verified,
       **{k:str(getattr(p,k)) for k in FIELDS},'evidence_reference':p.evidence_reference}


@router.put('/pricing/policies')
async def save_policy(body:PricePolicyInput,db:AsyncSession=Depends(get_session)):
    await validate_warehouse_map(db,body.supplier_code,body.warehouse_code,body.account_id,body.destination_id)
    source=await db.get(FeedSource,body.supplier_code)
    if not source:raise HTTPException(404,'Unknown feed source')
    policy=await db.scalar(select(FeedPriceRule).where(
       FeedPriceRule.supplier_code==body.supplier_code,FeedPriceRule.account_id==body.account_id,
       FeedPriceRule.destination_id==body.destination_id,FeedPriceRule.warehouse_code==body.warehouse_code
    ).with_for_update())
    if not policy:
        policy=FeedPriceRule(supplier_code=body.supplier_code,account_id=body.account_id,
           destination_id=body.destination_id,warehouse_code=body.warehouse_code,version=1)
        db.add(policy)
    else:
        policy.version+=1
    for field in FIELDS:setattr(policy,field,getattr(body,field))
    policy.evidence_reference=body.evidence_reference
    policy.tax_status_verified=source.tax_status
    policy.approved=False  # Change of ANY cost must revoke approval.
    policy.updated_at=utc()
    try:await db.commit()
    except IntegrityError as exc:
        await db.rollback();raise HTTPException(409,'Concurrent pricing policy update') from exc
    return pv(policy)


@router.get('/pricing/scopes')
async def available_scopes(db:AsyncSession=Depends(get_session)):
    """Only approved maps can be selected in a customer-price policy."""
    rows=(await db.execute(select(FeedWarehouseMap,SupplierAccount,SupplierDestination)
        .join(SupplierAccount,SupplierAccount.id==FeedWarehouseMap.account_id)
        .join(SupplierDestination,SupplierDestination.id==FeedWarehouseMap.destination_id)
        .where(FeedWarehouseMap.approved.is_(True),SupplierAccount.enabled.is_(True),
               SupplierDestination.enabled.is_(True),SupplierDestination.verified.is_(True))
        .order_by(FeedWarehouseMap.supplier_code,FeedWarehouseMap.warehouse_code).limit(2000))).all()
    return {'items':[{'supplier_code':m.supplier_code,'account_id':str(a.id),
             'destination_id':str(d.id),'warehouse_code':m.warehouse_code} for m,a,d in rows]}


@router.get('/pricing/policies')
async def list_policies(supplier:Literal['rossko','partkom']|None=None,
                         limit:int=Query(default=200,ge=1,le=1000),db:AsyncSession=Depends(get_session)):
    q=select(FeedPriceRule)
    if supplier:q=q.where(FeedPriceRule.supplier_code==supplier)
    data=(await db.scalars(q.order_by(FeedPriceRule.updated_at.desc()).limit(limit))).all()
    return {'items':[pv(x) for x in data],'currency':'RUB','payment':'SBP_ONLY','auto_publish':False}


class ApprovePolicy(BaseModel):
    expected_version: int=Field(ge=1)
    confirm_contract_costs: Literal[True]


@router.post('/pricing/policies/{policy_id}/approve')
async def approve_policy(policy_id:UUID,body:ApprovePolicy,db:AsyncSession=Depends(get_session)):
    p=await db.scalar(select(FeedPriceRule).where(FeedPriceRule.id==policy_id).with_for_update())
    if not p:raise HTTPException(404,'Pricing policy not found')
    if p.version!=body.expected_version:raise HTTPException(409,'Policy changed since review')
    await validate_warehouse_map(db,p.supplier_code,p.warehouse_code,p.account_id,p.destination_id)
    mapping=await db.scalar(select(FeedWarehouseMap.id).where(
        FeedWarehouseMap.supplier_code==p.supplier_code,FeedWarehouseMap.account_id==p.account_id,
        FeedWarehouseMap.destination_id==p.destination_id,
        FeedWarehouseMap.warehouse_code==p.warehouse_code,FeedWarehouseMap.approved.is_(True)))
    source=await db.get(FeedSource,p.supplier_code)
    if (not mapping or not source or not source.commercial_verified or source.price_kind!='purchase'
        or source.currency!='RUB' or source.tax_status not in ('included','excluded')
        or source.tax_status!=p.tax_status_verified):
        raise HTTPException(409,'Verify source commercial context, VAT, account/destination and warehouse mapping first')
    p.approved=True;p.updated_at=utc();await db.commit()
    return pv(p)


@router.get('/pricing/offers/{offer_id}/preview')
async def price_preview(offer_id:UUID,account_id:UUID,destination_id:UUID,db:AsyncSession=Depends(get_session)):
    offer=await db.get(FeedOffer,offer_id)
    if not offer:raise HTTPException(404,'Feed offer not found')
    batch=await db.get(FeedBatch,offer.batch_id)
    source=await db.get(FeedSource,batch.supplier_code)
    policy=await db.scalar(select(FeedPriceRule).where(
        FeedPriceRule.supplier_code==batch.supplier_code,FeedPriceRule.account_id==account_id,
        FeedPriceRule.destination_id==destination_id,FeedPriceRule.warehouse_code==offer.warehouse_code))
    if not usable_rule(policy,source):raise HTTPException(409,'No approved policy for this account/destination/warehouse')
    mapped=await db.scalar(select(FeedWarehouseMap.id).where(
        FeedWarehouseMap.supplier_code==batch.supplier_code,
        FeedWarehouseMap.account_id==account_id,FeedWarehouseMap.destination_id==destination_id,
        FeedWarehouseMap.warehouse_code==offer.warehouse_code,FeedWarehouseMap.approved.is_(True)))
    if not mapped:raise HTTPException(409,'No approved warehouse mapping')
    return {'batch_id':str(batch.id),'batch_status':batch.status,'active_batch':source.active_batch_id==batch.id,
            'item':{'brand':offer.brand,'article':offer.article,'warehouse':offer.warehouse_code},
            'quote':quote_unit(offer.purchase_price,policy),'currency':'RUB',
            'note':'Indicative only. Not allowed for checkout without live supplier validation.'}


@router.get('/catalog-syncs')
async def syncs(limit:int=Query(default=30,ge=1,le=100),db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(FeedCatalogSync).order_by(FeedCatalogSync.created_at.desc()).limit(limit))).all()
    return {'items':[view(r) for r in rows],'auto_publish':False}


@router.get('/imports/{batch_id}/catalog-sync')
async def sync_for_batch(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    sync=await db.scalar(select(FeedCatalogSync).where(FeedCatalogSync.batch_id==batch_id))
    if not sync:raise HTTPException(404,'Catalog sync not created; file must first finish validation')
    return view(sync)


@router.post('/imports/{batch_id}/catalog-sync',status_code=202)
async def ensure_sync(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status not in ('staged','published'):
        raise HTTPException(409,'Only validated staged or active feed batches can be synchronized')
    sync=await queue_sync(db,batch_id)
    await db.commit()
    return view(sync)


@router.post('/imports/{batch_id}/catalog-sync/resume',status_code=202)
async def resume_sync(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    sync=await db.scalar(select(FeedCatalogSync).where(FeedCatalogSync.batch_id==batch_id).with_for_update())
    if not sync or sync.status not in ('failed','paused'):
        raise HTTPException(409,'Only paused/failed feed catalog sync can resume')
    from app.modules.multi_enrichment.taxonomy_db import snapshot
    if (await snapshot(db)).revision != sync.taxonomy_revision:
        raise HTTPException(409,'Taxonomy changed; do not resume until reclassified with updated rules')
    sync.status='queued';sync.message='';sync.step+=1
    db.add(Job(dedup_key=f'feed-catalog-sync:{batch_id}:resume:{sync.step}',kind='feed_catalog_sync',
               payload={'sync_id':str(sync.id),'step':sync.step},status='pending',due_at=utc(),attempts=0))
    await db.commit();return view(sync)
