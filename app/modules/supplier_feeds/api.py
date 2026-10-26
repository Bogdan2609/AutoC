"""Operator-triggered supplier feed intake. Email/SFTP polling is deliberately absent."""
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import enforce_budget, peer_ip
from app.core.security import require_admin
from app.modules.catalog.models import Brand
from app.modules.catalog.normalize import normalize_brand
from app.modules.commerce.models import Job
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from .models import FeedBatch, FeedBrandAlias, FeedOffer, FeedSource, FeedWarehouseMap, utc
from .parsers import MAX_FILE
from .service import STORAGE, publish_batch, validate_warehouse_map

router = APIRouter(prefix='/api/v1/admin/supplier-feeds',tags=['supplier-feeds'],
                   dependencies=[Depends(require_admin)])


def summary(batch):
    return dict(id=str(batch.id),supplier=batch.supplier_code,status=batch.status,
                original_name=batch.original_name,sha256=batch.sha256,
                rows_total=batch.rows_total,rows_valid=batch.rows_valid,
                rows_rejected=batch.rows_rejected,rows_duplicates=batch.rows_duplicates,
                rows_matched=batch.rows_matched,errors_sample=batch.errors_sample,
                generated_at=batch.generated_at.isoformat() if batch.generated_at else None,
                created_at=batch.created_at.isoformat() if batch.created_at else None,
                published_at=batch.published_at.isoformat() if batch.published_at else None)


@router.post('/{supplier}/uploads',status_code=201)
async def upload_feed(supplier: Literal['rossko','partkom'],request: Request,
                      file: UploadFile=File(...),db: AsyncSession=Depends(get_session)):
    await enforce_budget('supplier_feed_upload',peer_ip(request),limit=6,period=3600)
    name=Path(file.filename or '').name[:200]
    ext=Path(name).suffix.lower()
    if (supplier=='rossko' and ext not in ('.xlsx','.zip')) or (supplier=='partkom' and ext!='.csv'):
        raise HTTPException(422,'Unexpected supplier feed extension')
    source=await db.get(FeedSource,supplier)
    if source is None:
        source=FeedSource(code=supplier,max_age_hours=30,commercial_verified=False,price_kind='unknown',currency='RUB',tax_status='unknown')
        db.add(source)
        await db.flush()
    STORAGE.mkdir(parents=True,exist_ok=True,mode=0o700)
    stored_name=f'{uuid.uuid4().hex}{ext}'
    target=STORAGE/stored_name
    sha=hashlib.sha256();size=0
    try:
        with target.open('xb') as dest:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_FILE:
                    raise HTTPException(413,'Feed file too large')
                sha.update(chunk);dest.write(chunk)
        if size==0:
            raise HTTPException(422,'Empty upload')
        previous=await db.scalar(select(FeedBatch).where(
            FeedBatch.supplier_code==supplier,FeedBatch.sha256==sha.hexdigest()))
        if previous:
            target.unlink(missing_ok=True)
            return {'already_uploaded':True,**summary(previous)}
        batch=FeedBatch(supplier_code=supplier,original_name=name,stored_name=stored_name,
                        sha256=sha.hexdigest(),status='uploaded',rows_total=0,rows_valid=0,
                        rows_rejected=0,rows_duplicates=0,rows_matched=0,errors_sample='')
        db.add(batch)
        await db.commit()
        return {'already_uploaded':False,**summary(batch)}
    except IntegrityError:
        target.unlink(missing_ok=True)
        await db.rollback()
        raise HTTPException(409,'Concurrent or duplicate supplier feed upload') from None
    except Exception:
        target.unlink(missing_ok=True)
        await db.rollback()
        raise
    finally:
        await file.close()


@router.get('/imports')
async def import_list(supplier: Literal['rossko','partkom'] | None=None,
                      limit:int=Query(default=30,ge=1,le=100), db:AsyncSession=Depends(get_session)):
    q=select(FeedBatch)
    if supplier:q=q.where(FeedBatch.supplier_code==supplier)
    batches=(await db.scalars(q.order_by(FeedBatch.created_at.desc()).limit(limit))).all()
    return {'items':[summary(b) for b in batches]}


@router.get('/imports/{batch_id}')
async def import_details(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    batch=await db.get(FeedBatch,batch_id)
    if not batch:raise HTTPException(404,'Unknown feed batch')
    return summary(batch)


@router.post('/imports/{batch_id}/process',status_code=202)
async def enqueue_import(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status not in ('uploaded','failed'):
        raise HTTPException(409,'Only uploaded or failed files can be queued')
    job=Job(dedup_key=f'feed-import:{batch_id}',kind='feed_import',payload={'batch_id':str(batch_id)},
            status='pending',due_at=utc(),attempts=0)
    existing=await db.scalar(select(Job).where(Job.dedup_key==job.dedup_key))
    if existing:
        if existing.status=='running':raise HTTPException(409,'Import already running')
        existing.status='pending';existing.due_at=utc();existing.attempts=0;existing.last_error=None
    else:db.add(job)
    batch.status='queued'
    await db.commit()
    return summary(batch)


class Publish(BaseModel):
    generated_at:datetime


@router.post('/imports/{batch_id}/publish')
async def publish(batch_id:UUID,body:Publish,db:AsyncSession=Depends(get_session)):
    batch=await publish_batch(db,batch_id,body.generated_at)
    return summary(batch)


@router.get('/imports/{batch_id}/offers')
async def sample(batch_id:UUID,offset:int=0,limit:int=50,db:AsyncSession=Depends(get_session)):
    if offset<0 or offset>1000000 or limit<1 or limit>200:
        raise HTTPException(422,'Invalid paging')
    if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'No such batch')
    rows=(await db.scalars(select(FeedOffer).where(FeedOffer.batch_id==batch_id)
                           .order_by(FeedOffer.source_line).offset(offset).limit(limit))).all()
    return {'items':[{'line':o.source_line,'brand':o.brand,'article':o.article,
                      'warehouse':o.warehouse_code,'price':str(o.purchase_price),
                      'stock_raw':o.stock_raw,'stock_min':o.stock_min,
                      'is_lower_bound':o.is_lower_bound,'matched_product_id':str(o.product_id) if o.product_id else None}
                     for o in rows]}


class WarehouseMapping(BaseModel):
    supplier: Literal['rossko','partkom']
    warehouse_code: str=Field(min_length=1,max_length=140)
    account_id: UUID
    destination_id:UUID
    approved:bool=False


@router.post('/warehouses/map',status_code=201)
async def map_warehouse(body:WarehouseMapping,db:AsyncSession=Depends(get_session)):
    await validate_warehouse_map(db,body.supplier,body.warehouse_code,body.account_id,body.destination_id)
    current=await db.scalar(select(FeedWarehouseMap).where(
        FeedWarehouseMap.supplier_code==body.supplier,FeedWarehouseMap.warehouse_code==body.warehouse_code,
        FeedWarehouseMap.account_id==body.account_id,FeedWarehouseMap.destination_id==body.destination_id))
    if current:
        current.approved=body.approved
        entry=current
    else:
        entry=FeedWarehouseMap(supplier_code=body.supplier,warehouse_code=body.warehouse_code,
            account_id=body.account_id,destination_id=body.destination_id,approved=body.approved)
        db.add(entry)
    await db.commit()
    return {'id':str(entry.id),'approved':entry.approved}


class AliasMapping(BaseModel):
    supplier: Literal['rossko','partkom']
    alias:str=Field(min_length=1,max_length=160)
    brand_id:UUID
    approved:bool=False


@router.post('/brands/map',status_code=201)
async def map_brand(body:AliasMapping,db:AsyncSession=Depends(get_session)):
    norm=normalize_brand(body.alias)
    if not await db.get(FeedSource,body.supplier) or not await db.get(Brand,body.brand_id):
        raise HTTPException(404,'Feed or catalog brand is missing')
    current=await db.scalar(select(FeedBrandAlias).where(
        FeedBrandAlias.supplier_code==body.supplier,FeedBrandAlias.brand_norm==norm))
    if current:
        current.brand_id=body.brand_id
        current.approved=body.approved
    else:
        current=FeedBrandAlias(supplier_code=body.supplier,brand_norm=norm,
                               brand_id=body.brand_id,approved=body.approved)
        db.add(current)
    await db.commit()
    return {'id':str(current.id),'approved':current.approved,'note':'New mapping applies on next import; active batches are immutable'}


@router.get('/sources')
async def sources(db:AsyncSession=Depends(get_session)):
    entries=(await db.scalars(select(FeedSource).order_by(FeedSource.code))).all()
    return {'items':[{'supplier':x.code,'active_batch_id':str(x.active_batch_id) if x.active_batch_id else None,
                      'max_age_hours':x.max_age_hours,'commercial_verified':x.commercial_verified,
                      'price_kind':x.price_kind,'currency':x.currency,'tax_status':x.tax_status} for x in entries]}


@router.get('/imports/{batch_id}/warehouses')
async def warehouse_statistics(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'No such batch')
    rows=(await db.execute(select(FeedOffer.warehouse_code,
        func.count(FeedOffer.id).label('records'),
        func.count(FeedOffer.product_id).label('matched'))
        .where(FeedOffer.batch_id==batch_id).group_by(FeedOffer.warehouse_code)
        .order_by(func.count(FeedOffer.id).desc()).limit(500))).all()
    return {'warehouses':[{'code':w,'rows':n,'matched':m} for w,n,m in rows]}


@router.get('/imports/{batch_id}/unmatched')
async def unmatched_brands(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'No such batch')
    rows=(await db.execute(select(FeedOffer.brand_norm,func.count(FeedOffer.id))
        .where(FeedOffer.batch_id==batch_id,FeedOffer.product_id.is_(None))
        .group_by(FeedOffer.brand_norm)
        .order_by(func.count(FeedOffer.id).desc()).limit(100))).all()
    return {'brands':[{'brand':b,'unmatched_rows':n} for b,n in rows]}


@router.get('/imports/{batch_id}/diff')
async def import_diff(batch_id:UUID,compare_to:UUID|None=None,db:AsyncSession=Depends(get_session)):
    """Set-based counts; no loading 500k offers into application memory."""
    from sqlalchemy import and_,case
    from sqlalchemy.orm import aliased
    batch=await db.get(FeedBatch,batch_id)
    if not batch:raise HTTPException(404,'No such batch')
    if compare_to is None:
        previous=await db.scalar(select(FeedBatch).where(
            FeedBatch.supplier_code==batch.supplier_code,FeedBatch.id!=batch.id,
            FeedBatch.status=='published',FeedBatch.created_at<batch.created_at)
            .order_by(FeedBatch.created_at.desc()).limit(1))
        if not previous:
            return {'comparison':'no_previous_published_file','batch_id':str(batch_id)}
    else:
        previous=await db.get(FeedBatch,compare_to)
        if not previous or previous.supplier_code!=batch.supplier_code or previous.id==batch.id:
            raise HTTPException(422,'Previous version must belong to the same supplier')
    new=aliased(FeedOffer);old=aliased(FeedOffer)
    query=select(
        func.count(new.id),
        func.sum(case((old.id.is_(None),1),else_=0)),
        func.sum(case((and_(old.id.is_not(None),new.purchase_price!=old.purchase_price),1),else_=0)),
        func.sum(case((and_(old.id.is_not(None),new.stock_raw!=old.stock_raw),1),else_=0)),
    ).select_from(new).outerjoin(old,and_(old.batch_id==previous.id,old.identity_hash==new.identity_hash))
    total,added,price_changes,stock_changes=(await db.execute(
        query.where(new.batch_id==batch.id))).one()
    removed=await db.scalar(select(func.count(old.id)).select_from(old)
        .outerjoin(new,and_(new.batch_id==batch.id,new.identity_hash==old.identity_hash))
        .where(old.batch_id==previous.id,new.id.is_(None)))
    return {'batch_id':str(batch.id),'compare_to':str(previous.id),
            'rows_new':total or 0,'added':added or 0,'removed':removed or 0,
            'price_changed':price_changes or 0,'stock_changed':stock_changes or 0,
            'note':'Price and stock changes may overlap; counts are not additive'}


def aware_file_time(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post('/sources/{supplier}/rollback')
async def rollback(supplier:Literal['rossko','partkom'],db:AsyncSession=Depends(get_session)):
    """Switch to previous PUBLISHED and still fresh file; does not alter snapshots."""
    from datetime import timedelta
    source=await db.scalar(select(FeedSource).where(FeedSource.code==supplier).with_for_update())
    if not source or not source.active_batch_id:raise HTTPException(409,'No active version')
    current=await db.get(FeedBatch,source.active_batch_id)
    previous=await db.scalar(select(FeedBatch).where(
        FeedBatch.supplier_code==supplier,FeedBatch.status=='published',
        FeedBatch.id!=current.id,FeedBatch.created_at<current.created_at)
        .order_by(FeedBatch.created_at.desc()).limit(1))
    if not previous or not previous.generated_at or utc()-aware_file_time(previous.generated_at)>timedelta(hours=source.max_age_hours):
        raise HTTPException(409,'No previous fresh published version; rollback blocked')
    source.active_batch_id=previous.id
    source.updated_at=utc()
    await db.commit()
    return {'active_batch_id':str(previous.id),'previous_active_id':str(current.id)}


class CommercialScope(BaseModel):
    price_kind:Literal['purchase']
    currency:Literal['RUB']
    tax_status:Literal['included','excluded','unknown']
    verified:bool=False


@router.post('/sources/{supplier}/commercial-scope')
async def commercial_scope(supplier:Literal['rossko','partkom'],body:CommercialScope,
                           db:AsyncSession=Depends(get_session)):
    """Operator explicitly attests that this file price is a PURCHASE price
    applicable to the mapped supplier account(s); no such assumption by code.
    """
    source=await db.get(FeedSource,supplier)
    if not source:raise HTTPException(404,'No feed source')
    source.price_kind=body.price_kind
    source.currency=body.currency
    source.tax_status=body.tax_status
    source.commercial_verified=body.verified
    source.updated_at=utc()
    await db.commit()
    return {'supplier':supplier,'commercial_verified':source.commercial_verified,
            'price_kind':source.price_kind,'currency':source.currency,'tax_status':source.tax_status}


@router.get('/imports/{batch_id}/candidates')
async def candidates(batch_id:UUID,limit:int=Query(default=100,ge=1,le=200),
                     offset:int=Query(default=0,ge=0,le=100000),
                     db:AsyncSession=Depends(get_session)):
    """Manual product-review queue: no catalog auto-publication."""
    if not await db.get(FeedBatch,batch_id):raise HTTPException(404,'No batch')
    q=select(FeedOffer.brand_norm,FeedOffer.article_norm,
             func.min(FeedOffer.description),func.count(FeedOffer.id))\
       .where(FeedOffer.batch_id==batch_id,FeedOffer.product_id.is_(None))\
       .group_by(FeedOffer.brand_norm,FeedOffer.article_norm)\
       .order_by(FeedOffer.brand_norm,FeedOffer.article_norm).offset(offset).limit(limit)
    rows=(await db.execute(q)).all()
    return {'items':[{'brand':b,'article':a,'sample_name':name,'warehouse_offer_count':count}
                     for b,a,name,count in rows],
            'note':'Candidates are not products. Curate them through Master Catalog; retry matching before publication.'}


@router.post('/imports/{batch_id}/rematch')
async def rematch(batch_id:UUID,db:AsyncSession=Depends(get_session)):
    """Only staged files can update catalog links; published commercial data frozen."""
    from .service import _match
    batch=await db.get(FeedBatch,batch_id)
    if not batch or batch.status!='staged':
        raise HTTPException(409,'Only staged versions can be rematched')
    batch.rows_matched=await _match(db,batch_id)
    await db.commit()
    return summary(batch)
