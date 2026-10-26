"""Supplier operations: bounded diagnostics and validated settings, never credentials."""
from uuid import UUID
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.core.config import get_settings
from app.modules.accounts.models import utc
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.service import find_published
from app.modules.offer_engine.models import CacheState, SupplierLog, SupplierRuntime
from app.modules.offer_engine.service import CODES, runtime, enqueue_product, aware, credentials_ready

router=APIRouter(prefix='/api/v1/backoffice/offer-engine',tags=['offer-operations'],dependencies=[Depends(require_admin)])

class SupplierPolicyPatch(BaseModel):
    enabled: bool|None = None
    positive_ttl_seconds: int|None = Field(None,ge=30,le=3600)
    empty_ttl_seconds: int|None = Field(None,ge=30,le=900)
    timeout_seconds: int|None = Field(None,ge=5,le=120)
    min_interval_seconds: int|None = Field(None,ge=0,le=120)


def validate_code(code):
    if code not in CODES:
        raise HTTPException(404,'Unknown supplier')


def serialize(cfg):
    return {'code':cfg.code,'enabled':cfg.enabled,
            'positive_ttl_seconds':cfg.positive_ttl_seconds,
            'empty_ttl_seconds':cfg.empty_ttl_seconds,
            'timeout_seconds':cfg.timeout_seconds,
            'min_interval_seconds':cfg.min_interval_seconds,
            'next_request_at':cfg.next_request_at.isoformat(),
            'last_changed_at':cfg.last_changed_at.isoformat(),
            'consecutive_failures':cfg.consecutive_failures,
            'circuit_open_until':cfg.circuit_open_until.isoformat() if cfg.circuit_open_until else None,
            'credentials_configured':credentials_ready(cfg.code),
            'supplier_api_global_enabled':get_settings().supplier_api_enabled,
            'supplier_requests_per_hour_cap':get_settings().supplier_requests_per_hour,
            'live_search_possible':bool(cfg.enabled and credentials_ready(cfg.code) and get_settings().supplier_api_enabled),
            'procurement_enabled':False, # v0.3 does not permit live checkout.
            }

@router.get('/suppliers')
async def supplier_status(db:AsyncSession=Depends(get_session)):
    now=utc()
    out=[]
    for code in CODES:
        cfg=await runtime(db,code)
        statuses=(await db.execute(select(CacheState.status,func.count()).where(
            CacheState.supplier_code==code).group_by(CacheState.status))).all()
        recent=(await db.execute(select(SupplierLog.outcome,func.count()).where(
            SupplierLog.code==code,SupplierLog.created_at>=now-timedelta(days=1))
            .group_by(SupplierLog.outcome))).all()
        last=await db.scalar(select(SupplierLog).where(SupplierLog.code==code)
                             .order_by(SupplierLog.created_at.desc()).limit(1))
        out.append({**serialize(cfg),
                    'cache_statuses':dict(statuses),
                    'requests_24h':dict(recent),
                    'last_request':({'at':last.created_at.isoformat(),'outcome':last.outcome,
                                     'duration_ms':last.duration_ms,'error_type':last.error_type}
                                    if last else None)})
    await db.commit()
    return out

@router.get('/suppliers/{code}/configuration')
async def get_configuration(code:str,db:AsyncSession=Depends(get_session)):
    validate_code(code)
    cfg=await runtime(db,code)
    await db.commit()
    return serialize(cfg)

@router.patch('/suppliers/{code}/configuration')
async def configure_supplier(code:str,patch:SupplierPolicyPatch,db:AsyncSession=Depends(get_session)):
    validate_code(code)
    cfg=await runtime(db,code)
    for field,value in patch.model_dump(exclude_none=True).items():
        setattr(cfg,field,value)
    cfg.last_changed_at=utc()
    await db.commit()
    return serialize(cfg)

@router.get('/suppliers/{code}/cache')
async def inspect_cache(code:str,limit:int=Query(50,ge=1,le=200),
                        status:Literal['ready','empty','error','refreshing','pending','disabled','review']|None=None,
                        db:AsyncSession=Depends(get_session)):
    validate_code(code)
    query=select(CacheState).where(CacheState.supplier_code==code)
    if status:
        query=query.where(CacheState.status==status)
    rows=(await db.scalars(query.order_by(CacheState.last_requested_at.desc().nullslast(),CacheState.lookup_key).limit(limit))).all()
    now=utc()
    return [{'product_id':str(x.product_id) if x.product_id else None,
             'lookup_key':x.lookup_key,'status':x.status,
             'expired':aware(x.expires_at)<=now,'expires_at':x.expires_at.isoformat(),
             'result_count':x.result_count,'error_type':x.error_type,
             'failure_count':x.failure_count,
             'demand_count':x.demand_count,
             'last_demand_at':x.last_demand_at.isoformat() if x.last_demand_at else None,
             'last_requested_at':x.last_requested_at.isoformat() if x.last_requested_at else None,
             'last_success_at':x.last_success_at.isoformat() if x.last_success_at else None,
             'last_duration_ms':x.last_duration_ms} for x in rows]

@router.get('/suppliers/{code}/logs')
async def inspect_logs(code:str,limit:int=Query(50,ge=1,le=200),db:AsyncSession=Depends(get_session)):
    validate_code(code)
    rows=(await db.scalars(select(SupplierLog).where(SupplierLog.code==code)
                          .order_by(SupplierLog.created_at.desc()).limit(limit))).all()
    return [{'time':r.created_at.isoformat(),'action':r.action,'outcome':r.outcome,
             'duration_ms':r.duration_ms,'error_type':r.error_type,
             'lookup_key':r.lookup_key,'result_count':r.result_count} for r in rows]

@router.post('/products/{product_id}/refresh',status_code=202)
async def request_product_refresh(product_id:UUID,
                                  code:Literal['rossko','adeo','partkom','forum-auto']|None=None,
                                  db:AsyncSession=Depends(get_session)):
    product=await db.get(Product,product_id)
    if not product or product.status!='published':raise HTTPException(404,'Published product not found')
    brand=await db.get(Brand,product.brand_id)
    return {'refresh':await enqueue_product(db,product,brand,codes=(code,) if code else None,force=True)}

@router.get('/products')
async def lookup_products(q:str=Query(min_length=2,max_length=110),db:AsyncSession=Depends(get_session)):
    products,count=await find_published(db,q,limit=20,offset=0)
    return {'count':count,'items':[{'id':str(p.id),'name':p.name,'article':p.article,'slug':p.slug} for p in products]}
