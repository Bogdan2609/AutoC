"""Private JSON operations console; no PII in supplier diagnostics."""
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import HTMLResponse
from pathlib import Path
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.accounts.models import User
from app.modules.commerce.models import Order,OrderLine,Procurement,Replacement,Job
from app.modules.payments.models import Payment,Refund
from app.modules.suppliers.models import Supplier,SupplierItem,OfferSnapshot
from app.modules.offer_engine.models import SupplierLog,CacheState
router=APIRouter(prefix='/api/v1/backoffice/operations',tags=['operations'],dependencies=[Depends(require_admin)])
@router.get('/overview')
async def overview(db:AsyncSession=Depends(get_session)):
 async def count(t):return await db.scalar(select(func.count()).select_from(t))
 return {'users':await count(User),'orders':await count(Order),'payments':await count(Payment),'procurements':await count(Procurement),'replacements':await count(Replacement),'refunds':await count(Refund),'jobs':await count(Job)}
@router.get('/suppliers')
async def suppliers(db:AsyncSession=Depends(get_session)):
 rows=[]
 for code in ('rossko','adeo','partkom','forum-auto'):
  logs=(await db.scalars(select(SupplierLog).where(SupplierLog.code==code).order_by(SupplierLog.created_at.desc()).limit(20))).all()
  states=(await db.scalars(select(CacheState).where(CacheState.supplier_code==code).limit(100))).all()
  rows.append({'code':code,'recent_requests':[{'action':x.action,'outcome':x.outcome,'ms':x.duration_ms,'error_type':x.error_type} for x in logs],
    'cache':[{'lookup_key':s.lookup_key,'status':s.status,'expires_at':s.expires_at.isoformat(),'count':s.result_count,'error_type':s.error_type} for s in states]})
 return rows
@router.get('/orders')
async def orders(db:AsyncSession=Depends(get_session)):
 values=(await db.scalars(select(Order).order_by(Order.created_at.desc()).limit(100))).all()
 return [{'id':str(o.id),'status':o.status,'total':str(o.total),'customer_id':str(o.user_id)} for o in values]
@router.get('/orders/{order_id}')
async def order_details(order_id:UUID,db:AsyncSession=Depends(get_session)):
 o=await db.get(Order,order_id)
 if not o:raise HTTPException(404,'Order not found')
 lines=(await db.scalars(select(OrderLine).where(OrderLine.order_id==o.id))).all();payments=(await db.scalars(select(Payment).where(Payment.order_id==o.id))).all()
 output=[]
 for l in lines:
  purchase=await db.scalar(select(Procurement).where(Procurement.order_line_id==l.id))
  replacements=(await db.scalars(select(Replacement).where(Replacement.order_line_id==l.id))).all()
  output.append({'id':str(l.id),'title':l.title,'supplier':l.supplier_code,'quantity':l.quantity,'retail':str(l.unit_retail),'purchase':str(l.unit_purchase),'status':l.status,
    'procurement':{'status':purchase.status,'external_order_id':purchase.supplier_order_id,'error':purchase.error} if purchase else None,
    'replacements':[{'id':str(r.id),'status':r.status,'alternative_id':str(r.alternative_item_id) if r.alternative_item_id else None} for r in replacements]})
 return {'id':str(o.id),'total':str(o.total),'status':o.status,
  'workflow':(o.request_details or {}).get('workflow'),
  'operator_quote':(o.request_details or {}).get('operator_quote'),
  'total_is_final':o.total_is_final,'contact':o.contact,'phone':o.phone,'shipping_address':o.shipping_address,
  'items':output,'payments':[{'id':str(p.id),'status':p.status,'amount':str(p.amount),'provider_id':p.provider_id} for p in payments]}
@router.post('/lines/{line_id}/propose-replacement')
async def propose(line_id:UUID,alternative_item_id:UUID,reason:str='Supplier cannot fulfill',db:AsyncSession=Depends(get_session)):
 line=await db.get(OrderLine,line_id)
 if not line or line.status not in ('procurement_manual_review','supplier_rejected'):raise HTTPException(409,'Line not eligible')
 current=await db.get(SupplierItem,line.supplier_item_id);other=await db.get(SupplierItem,alternative_item_id)
 if not other or not current or other.id==current.id or other.product_id!=current.product_id or other.match_status!='confirmed':raise HTTPException(409,'Only confirmed identical products are eligible')
 latest=await db.scalar(select(OfferSnapshot).where(OfferSnapshot.supplier_item_id==other.id).order_by(OfferSnapshot.observed_at.desc()))
 if not latest or latest.quantity is None or latest.quantity<line.quantity:raise HTTPException(409,'Insufficient cached stock')
 r=Replacement(order_line_id=line.id,alternative_item_id=other.id,suggested_price=line.unit_retail,reason=reason)
 db.add(r);line.status='replacement_offered';await db.commit()
 return {'id':str(r.id),'status':'awaiting_customer','needs_live_validation':True}
@router.get('/jobs')
async def jobs(db:AsyncSession=Depends(get_session)):
 records=(await db.scalars(select(Job).order_by(Job.due_at.desc()).limit(100))).all()
 return [{'id':str(x.id),'kind':x.kind,'status':x.status,'attempts':x.attempts,'error':x.last_error} for x in records]
@router.get('/suppliers/{code}/offers')
async def supplier_offers(code:str,db:AsyncSession=Depends(get_session)):
 supplier=await db.scalar(select(Supplier).where(Supplier.code==code))
 if not supplier:raise HTTPException(404,'Supplier not found')
 items=(await db.scalars(select(SupplierItem).where(SupplierItem.supplier_id==supplier.id).limit(200))).all()
 from app.modules.offer_engine.models import OfferDetail
 data=[]
 for item in items:
  snap=await db.scalar(select(OfferSnapshot).where(OfferSnapshot.supplier_item_id==item.id).order_by(OfferSnapshot.observed_at.desc(),OfferSnapshot.id.desc()))
  detail=await db.get(OfferDetail,snap.id) if snap else None
  data.append({'item_id':str(item.id),'external_key':item.external_key,'product_id':str(item.product_id) if item.product_id else None,
    'brand':item.raw_brand,'article':item.raw_article,'matching':item.match_status,
    'current_offer':{'snapshot_id':str(snap.id),'purchase':str(snap.purchase_price),'tax':snap.tax_status,
     'quantity':snap.quantity,'days':snap.lead_time_days,'observed_at':snap.observed_at.isoformat(),
     'warehouse':detail.warehouse if detail else None,'min_qty':detail.min_qty if detail else None,
     'pack_multiple':detail.pack_multiple if detail else None,'verified_live':detail.verified_live if detail else False,
     'extra':detail.metadata_public if detail else {}} if snap else None})
 return data
@router.get('/refunds')
async def refunds(db:AsyncSession=Depends(get_session)):
 records=(await db.scalars(select(Refund).limit(100))).all()
 return [{'id':str(r.id),'payment_id':str(r.payment_id),'provider_id':r.provider_id,'amount':str(r.amount),'status':r.status} for r in records]

# HTML contains no credentials: all data APIs remain protected by X-Admin-Token.
ui_router=APIRouter(include_in_schema=False)
@ui_router.get('/operator',response_class=HTMLResponse)
async def operator_page():
 return HTMLResponse(Path('templates/operator.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store','X-Robots-Tag':'noindex, nofollow','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
