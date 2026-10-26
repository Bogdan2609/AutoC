"""SBP webhooks are untrusted hints: authoritative GET /v3/payments/{id} required."""
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.commerce.models import Order,OrderLine,Procurement,Job
from app.modules.payments.models import Payment,Refund
from app.modules.payments.selection import selected_payment_stmt
from app.modules.payments.money_contract import payable_total
from app.core.security import require_admin
from app.modules.payments.gateway import gateway,GatewayError, money as rub_money
router=APIRouter(prefix='/api/v1/payments',tags=['sbp-payments'])

def _matches_money(remote_value, expected):
 try:
  return Decimal(rub_money(remote_value)) == expected
 except GatewayError:
  return False

async def verify_remote(db,payment):
 if not payment.provider_id:raise HTTPException(409,'Payment ID not recorded')
 try:remote=await gateway().status(payment.provider_id)
 except GatewayError as exc:raise HTTPException(503,'Cannot verify provider payment') from exc
 if (remote.get('id')!=payment.provider_id or
     not isinstance(remote.get('metadata'),dict) or
     remote['metadata'].get('order_id')!=str(payment.order_id)):
  raise HTTPException(409,'Payment identity mismatch')
 amount=remote.get('amount')
 if not isinstance(amount,dict):raise HTTPException(409,'Malformed provider payment amount')
 if amount.get('currency')!='RUB' or not _matches_money(amount.get('value'),payment.amount):raise HTTPException(409,'Payment amount mismatch')
 # Lock the common order BEFORE the individual payment. Concurrent webhooks
 # for different provider payments of the same order must serialize too.
 # Otherwise both transactions can observe 'no other succeeded payment'.
 order=await db.scalar(select(Order).where(Order.id==payment.order_id)
                       .with_for_update().execution_options(populate_existing=True))
 payment=await db.scalar(select(Payment).where(Payment.id==payment.id)
                         .with_for_update().execution_options(populate_existing=True))
 if not payment:raise HTTPException(409,'Payment record missing')
 if (payment.provider_id != remote.get('id') or payment.order_id != (order.id if order else None) or
     payment.amount != Decimal(rub_money(amount.get('value')))):
  raise HTTPException(409,'Payment record changed during verification')
 if not order or order.total!=payment.amount:raise HTTPException(409,'Order/payment mismatch')
 # Operator requests contain non-final indicative totals. Never turn them into
 # payable orders, even if a legacy or manually inserted payment exists.
 if (not order.total_is_final or (order.request_details or {}).get('quote_only') or
     (order.request_details or {}).get('workflow')=='v017'):
  raise HTTPException(409,'Provisional operator request cannot be settled as a payment')
 if (order.request_details or {}).get('workflow')!='sbp_checkout':
  raise HTTPException(409,'Order is not a verified SBP checkout workflow')
 if order.status not in ('awaiting_sbp','payment_pending','paid_procurement_pending',
                         'payment_canceled','fulfillment_in_progress','fulfillment_review',
                         'fulfillment_exception','fulfilled'):
  raise HTTPException(409,'Order status is not eligible for payment reconciliation')
 if remote.get('status')=='succeeded' and remote.get('paid') is True:
  method=remote.get('payment_method')
  if not isinstance(method,dict) or method.get('type')!='sbp':
   raise HTTPException(409,'Payment is not SBP')
  lines=(await db.scalars(select(OrderLine).where(OrderLine.order_id==order.id))).all()
  if not lines or order.total<=0 or any(x.quantity<=0 for x in lines):
   raise HTTPException(409,'No payable order lines')
  # The verified bank total must match the immutable payable line snapshot.
  if payable_total((x.unit_retail, x.quantity) for x in lines) != order.total:
   raise HTTPException(409,'Payable order line total mismatch')
  other=await db.scalar(select(Payment).where(Payment.order_id==order.id,
      Payment.id!=payment.id,Payment.status=='succeeded'))
  if other:raise HTTPException(409,'Multiple successful payments require manual reconciliation')
  if payment.status!='succeeded':
   payment.status='succeeded';order.status='paid_procurement_pending'
   # One shared fulfilment materialization for all paid order lines.
   from app.modules.commerce.sale_lifecycle import record_paid_fulfillment
   await record_paid_fulfillment(db,order,lines)
   await db.commit()
 elif remote.get('status')=='canceled' and payment.status not in ('succeeded','canceled'):
  payment.status='canceled'
  # Canceling an alternative payment must not erase a valid paid order.
  existing_paid=await db.scalar(select(Payment.id).where(
      Payment.order_id==order.id,Payment.id!=payment.id,Payment.status=='succeeded'))
  if not existing_paid:
   order.status='payment_canceled'
  await db.commit()
 return {'order_id':str(order.id),'payment_status':payment.status,'order_status':order.status}
@router.post('/yookassa/webhook')
async def webhook(body:dict,db:AsyncSession=Depends(get_session)):
 if body.get('type') != 'notification' or body.get('event') not in ('payment.succeeded','payment.canceled','payment.waiting_for_capture'):
  raise HTTPException(400,'Unsupported YooKassa notification')
 obj=body.get('object')
 pid=obj.get('id') if isinstance(obj,dict) else None
 if not isinstance(pid,str) or len(pid)>150:return {'received':True}
 payment=await db.scalar(select(Payment).where(Payment.provider_id==pid))
 if not payment:return {'received':True}
 # Coalescing is advisory; never acknowledge an in-flight duplicate as settled.
 # Bank GET + PostgreSQL row locks are authoritative.
 from app.modules.offer_engine.cache import redis_client
 from app.core.config import get_settings
 from app.core.redis_lease import RedisLease
 import hashlib
 redis=await redis_client()
 lease=None
 if redis is not None:
  key='apv2:payment:webhook:'+hashlib.sha256(pid.encode()).hexdigest()
  try:
   lease=await RedisLease.acquire(redis,key,ttl=60)
  except Exception as exc:
   raise HTTPException(503,'Webhook coalescing temporarily unavailable') from exc
  if lease is None:
   raise HTTPException(503,'Payment notification already being verified; retry')
 elif get_settings().app_env!='test':
  raise HTTPException(503,'Webhook coalescing unavailable')
 try:
  await verify_remote(db,payment)
 finally:
  if lease is not None:
   await lease.release()
 return {'received':True}
@router.post('/orders/{order_id}/refresh')
async def refresh(order_id:UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
 order=await db.get(Order,order_id)
 if not order or order.user_id!=user.id:raise HTTPException(404,'Order not found')
 pay=await db.scalar(selected_payment_stmt(order.id))
 if not pay:raise HTTPException(409,'Payment not created')
 return await verify_remote(db,pay)


@router.get('/admin/refunds/needs-reconciliation', dependencies=[Depends(require_admin)])
async def refund_queue(db:AsyncSession=Depends(get_session)):
    """Read-only queue: never automatically repeat an uncertain supplier/payment write."""
    rows=(await db.scalars(select(Refund).where(
        Refund.status.in_(('uncertain','provider_pending','canceled')))
        .order_by(Refund.id).limit(100))).all()
    keys = [f'refund_reconcile:{r.id}' for r in rows]
    jobs = (await db.scalars(select(Job).where(Job.dedup_key.in_(keys)))).all() if keys else []
    state = {j.dedup_key: j.status for j in jobs}
    return [{'refund_id':str(r.id),'status':r.status,
             'provider_reference_present':bool(r.provider_id),
             'reconciliation_job_status':state.get(f'refund_reconcile:{r.id}'),
             'requires_manual_review':(
                 r.status=='canceled' or not r.provider_id or
                 state.get(f'refund_reconcile:{r.id}')=='manual_review')
            } for r in rows]


@router.post('/admin/refunds/{refund_id}/reconcile', dependencies=[Depends(require_admin)])
async def reconcile_refund(refund_id: UUID, db: AsyncSession = Depends(get_session)):
    """Authoritative read-only check; never repeats the external refund POST."""
    from app.modules.payments.refund_reconciliation import verify_refund_status
    status = await verify_refund_status(db, refund_id, bank=gateway())
    await db.commit()
    return {'refund_id': str(refund_id), 'status': status}


@router.get('/admin/payments/needs-reconciliation', dependencies=[Depends(require_admin)])
async def unsettled_payments(db: AsyncSession = Depends(get_session)):
    """Owner-only, redacted operational view, including missing provider IDs.

    A payment whose provider ID was lost during an uncertain create cannot be
    looked up safely through GET and MUST NOT be recreated with a new key.
    """
    from app.modules.commerce.models import Job
    rows = (await db.scalars(select(Payment).where(
        Payment.status.in_(('creating', 'pending', 'waiting_for_capture',
                            'reconciliation_required')))
        .order_by(Payment.created_at, Payment.id).limit(100))).all()
    keys = [f'payment_reconcile:{r.id}' for r in rows]
    jobs = (await db.scalars(select(Job).where(Job.dedup_key.in_(keys)))).all() if keys else []
    state = {j.dedup_key: j.status for j in jobs}
    return [{
        'payment_id': str(p.id), 'order_id': str(p.order_id), 'status': p.status,
        'provider_reference_present': bool(p.provider_id),
        'reconciliation_job_status': state.get(f'payment_reconcile:{p.id}'),
        'manual_reconciliation_required': (
            not p.provider_id or state.get(f'payment_reconcile:{p.id}') == 'manual_review'),
    } for p in rows]
