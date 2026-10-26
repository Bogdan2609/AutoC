"""Postgres durable jobs. Refuse speculative external purchases; never retry ambiguous orders."""
import asyncio
import uuid
import logging
from app.core.config import get_settings
from datetime import timedelta
from time import monotonic
from uuid import UUID
from sqlalchemy import select, func
from app.core.db import get_session_factory
from app.modules.accounts.models import utc
from app.modules.catalog.models import Product
from app.modules.catalog.normalize import normalize_brand,normalize_article
from app.modules.commerce.models import Job,Procurement,OrderLine,Order,Replacement
from app.modules.offer_engine.models import CacheState,OfferDetail,SupplierLog
from app.modules.offer_engine.providers import PROVIDERS,Unavailable
from app.modules.suppliers.models import Supplier,SupplierItem,OfferSnapshot
from app.modules.payments.models import Payment,Refund
from app.modules.payments.gateway import gateway
from app.modules.payments.refund_reconciliation import validate_refund_response

async def search(db, job):
    """Read-only provider search, no secrets or raw HTTP bodies in DB logs."""
    from app.modules.offer_engine.service import aware, runtime
    p = job.payload
    code = p['supplier']
    start = monotonic()
    state = await db.scalar(select(CacheState).where(
        CacheState.supplier_code==code, CacheState.lookup_key==p['key']))
    if not state or state.status != 'refreshing':
        return
    if state.last_requested_at and aware(state.last_requested_at).isoformat() != p.get('requested_at'):
        return  # another refresh superseded this job
    cfg = await runtime(db, code)
    account = None
    destination = None
    if p.get('account_id'):
        # Reject a queued job whose cache key belongs to another account or
        # destination. The payload is durable, but must not grant authority.
        from app.modules.marketplace.account_search import account_key
        if not p.get('destination_id') or p['key'] != account_key(
                p['brand'], p['article'], UUID(p['account_id']), UUID(p['destination_id'])):
            return
    else:
        # A formerly valid global job can survive the registration of a scoped
        # account. Never use legacy credentials for a supplier now owned by
        # independently configured API accounts (even if currently disabled).
        from app.modules.marketplace.models import SupplierAccount
        configured = await db.scalar(select(SupplierAccount.id).join(
            Supplier, SupplierAccount.supplier_id == Supplier.id).where(
            Supplier.code == code).limit(1))
        if configured is not None:
            state.status = 'disabled'
            state.expires_at = utc() + timedelta(minutes=5)
            return
    if p.get('account_id'):
        from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
        from app.modules.marketplace.account_credentials import ready, for_account
        account = await db.get(SupplierAccount, UUID(p['account_id']))
        destination = await db.get(SupplierDestination,UUID(p['destination_id'])) if p.get('destination_id') else None
        supplier_for_account = await db.get(Supplier,account.supplier_id) if account else None
        node = await db.get(FulfillmentNode,destination.node_id) if destination else None
        if (not account or not destination or not supplier_for_account or
            supplier_for_account.code != code or destination.account_id != account.id or
            not node or not node.active or node.merchant_id!=account.merchant_id or
            not account.enabled or not account.read_enabled or not destination.enabled or
            not destination.verified or not ready(code,account,destination)):
            state.status = 'disabled'
            state.expires_at = utc()+timedelta(minutes=5)
            return
    if not cfg.enabled:
        state.status='disabled'
        state.expires_at=utc()+timedelta(minutes=5)
        return
    # Commit DB reads before the outbound HTTP request: no held DB transaction.
    timeout = min(cfg.timeout_seconds,account.timeout_seconds) if account else cfg.timeout_seconds
    provider_config = for_account(code,account,destination) if account else None
    account_id = account.id if account else None
    await db.commit()
    success = False
    records = []
    error_type = None
    try:
        records = await asyncio.wait_for(PROVIDERS[code].search(p['brand'],p['article'],provider_config) if account_id else PROVIDERS[code].search(p['brand'],p['article']),timeout=timeout)
        if len(records)>250:
            raise Unavailable('Supplier response exceeds search limit')
        success = True
    except Exception as error:
        error_type = type(error).__name__[:80]
    duration = int((monotonic()-start)*1000)
    state = await db.scalar(select(CacheState).where(
        CacheState.supplier_code==code, CacheState.lookup_key==p['key']))
    # Do not overwrite a newer result if this request lost its lease.
    if not state or state.status!='refreshing' or (state.last_requested_at and aware(state.last_requested_at).isoformat()!=p.get('requested_at')):
        return
    cfg = await runtime(db,code)
    if account_id is not None:
        # Read current database values *after* the network request. An account
        # or destination can be disabled while the supplier is answering; an
        # in-flight request must not publish fresh saleable snapshots afterward.
        from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
        live_scope = await db.scalar(select(SupplierAccount.id).join(
            SupplierDestination, SupplierDestination.account_id == SupplierAccount.id
        ).join(FulfillmentNode, FulfillmentNode.id == SupplierDestination.node_id).where(
            SupplierAccount.id == account_id,
            SupplierDestination.id == destination.id,
            SupplierAccount.enabled.is_(True),
            SupplierAccount.read_enabled.is_(True),
            SupplierDestination.enabled.is_(True),
            SupplierDestination.verified.is_(True),
            FulfillmentNode.active.is_(True),
            FulfillmentNode.merchant_id == SupplierAccount.merchant_id,
        ))
        if live_scope is None:
            state.status = 'disabled'
            state.expires_at = utc() + timedelta(minutes=5)
            return
    state.last_finished_at=utc()
    state.last_duration_ms=duration
    if success:
        cfg.consecutive_failures=0
        cfg.circuit_open_until=None
        product=await db.get(Product,UUID(p['product_id'])) if p.get('product_id') else None
        if product is None and p.get('discovery'):
            from app.modules.offer_engine.discovery import create_verified_product
            # Create a catalog identity only from validated, exact positive offers.
            admissible = [r for r in records
                          if normalize_brand(r.brand)==normalize_brand(p['brand'])
                          and normalize_article(r.article)==normalize_article(p['article'])
                          and 0 < r.price and 0 < len(r.key) <= 150 and 0 < len(r.token) <= 512
                          and (r.qty is None or r.qty >= 0)
                          and (r.days is None or r.days >= 0)]
            if admissible:
                product = await create_verified_product(db, p['brand'], p['article'])
        if not product:
            if p.get('discovery') and not records:
                state.status = 'empty'
                state.expires_at = utc()+timedelta(seconds=cfg.empty_ttl_seconds)
                state.result_count = 0
                state.last_success_at = utc()
                state.last_completed_job_id = str(job.id)
                state.error_type = None
                state.failure_count = 0
                success = True
            elif p.get('discovery'):
                # Returned only ambiguous/unusable records: no auto-publish.
                state.status = 'review'
                state.expires_at = utc()+timedelta(seconds=cfg.empty_ttl_seconds)
                state.result_count = 0
                state.last_success_at = utc()
                state.last_completed_job_id = str(job.id)
                state.error_type = 'OfferRejectedByValidation'
                success = True
            else:
                success=False
                error_type='ProductRemoved'
        else:
            state.product_id = product.id
            supplier=await db.scalar(select(Supplier).where(Supplier.code==code))
            if not supplier:
                supplier=Supplier(code=code,name=code.upper())
                db.add(supplier)
                await db.flush()
            stored=0
            rejected=0
            seen_keys=set()
            admissible=[]
            for rec in records:
                if (normalize_brand(rec.brand)!=normalize_brand(p['brand']) or
                    normalize_article(rec.article)!=normalize_article(p['article']) or
                    not 0<rec.price or not 0<len(rec.key)<=150 or
                    not 0<len(rec.token)<=512 or
                    (rec.qty is not None and rec.qty<0) or
                    (rec.days is not None and rec.days<0) or rec.key in seen_keys):
                    rejected+=1
                    continue
                seen_keys.add(rec.key)
                admissible.append(rec)
            # Single SELECT for all current supplier identities, instead of one
            # per record. Keep the unique supplier/external_key domain guard.
            existing_items=(await db.scalars(select(SupplierItem).where(
                SupplierItem.supplier_id==supplier.id,
                SupplierItem.external_key.in_([r.key for r in admissible])))).all() if admissible else []
            by_key={item.external_key:item for item in existing_items}
            added=False
            for rec in admissible:
                if rec.key not in by_key:
                    item=SupplierItem(id=uuid.uuid4(),supplier_id=supplier.id,
                        product_id=product.id,external_key=rec.key,
                        raw_brand=rec.brand,raw_article=rec.article,match_status='confirmed')
                    by_key[rec.key]=item
                    db.add(item)
                    added=True
            if added:
                await db.flush()  # one INSERT batch rather than per-SKU flush
            for rec in admissible:
                item=by_key[rec.key]
                if item.product_id!=product.id or item.match_status!='confirmed':
                    rejected+=1
                    continue
                item.last_search_token=str(job.id)
                snap=OfferSnapshot(id=uuid.uuid4(),supplier_item_id=item.id,
                                   purchase_price=rec.price,quantity=rec.qty,
                                   lead_time_days=rec.days,
                                   tax_status='included' if code=='rossko' else 'unknown',
                                   supplier_account_id=account_id,
                                   supplier_destination_id=destination.id if destination else None)
                db.add(snap)
                db.add(OfferDetail(snapshot_id=snap.id,external_token=rec.token,
                                   warehouse=rec.warehouse,min_qty=rec.minimum,
                                   pack_multiple=rec.multiple,verified_live=True,
                                   metadata_public={**rec.public_meta,'_search_job_id':str(job.id)}))
                stored+=1
            if stored:
                await db.flush()  # one persistence batch of snapshots/details
            state.status='ready' if stored else 'empty'
            state.expires_at=utc()+timedelta(seconds=cfg.positive_ttl_seconds if stored else cfg.empty_ttl_seconds)
            state.result_count=stored
            state.last_completed_job_id=str(job.id)
            state.error_type=None
            state.failure_count=0
            state.last_success_at=utc()
            if rejected:
                db.add(SupplierLog(code=code,supplier_account_id=account_id,action='validation',outcome='rejected',
                    duration_ms=0,error_type='MismatchOrInvalidData',
                    lookup_key=p['key'],result_count=rejected))
    if not success:
        cfg.consecutive_failures+=1
        if cfg.consecutive_failures>=3:
            cfg.circuit_open_until=utc()+timedelta(seconds=min(60*(2**min(cfg.consecutive_failures-3,4)),900))
        state.failure_count+=1
        state.status='error'
        state.expires_at=utc()+timedelta(seconds=min(60*(2**min(state.failure_count-1,4)),900))
        state.error_type=error_type or 'SupplierError'
    db.add(SupplierLog(code=code,supplier_account_id=account_id,action='search',outcome='success' if success else 'error',
                       duration_ms=duration,error_type=state.error_type if not success else None,
                       lookup_key=p['key'],result_count=state.result_count if success else None))

async def procurement(db,job):
 proc=await db.scalar(select(Procurement).where(
     Procurement.id==UUID(job.payload['id'])).with_for_update())
 if not proc or proc.status!='pending':return
 line=await db.get(OrderLine,proc.order_line_id)
 order=await db.get(Order,line.order_id) if line else None
 paid=await db.scalar(select(Payment.id).where(
     Payment.order_id==order.id,Payment.status=='succeeded')) if order else None
 if (not order or not line or not paid or not order.total_is_final or
     (order.request_details or {}).get('workflow')!='sbp_checkout'):
  proc.status='manual_review'
  proc.error='Unverified order/payment linkage: supplier purchase forbidden'
  if line:
   line.status='procurement_manual_review'
   from app.modules.commerce.sale_lifecycle import SaleLineExecution, reconcile_order
   execution=await db.scalar(select(SaleLineExecution).where(SaleLineExecution.order_line_id==line.id))
   if execution and execution.status=='procurement_pending':
    execution.status='manual_review'
   if order:await reconcile_order(db,order)
  return
 # In the current build no supplier-specific execution contract is approved.
 # A real supplier order must never be blindly retried after an ambiguous HTTP result.
 proc.status='manual_review'
 proc.error='Supplier purchase contract not yet verified; no purchase sent'
 line.status='procurement_manual_review'
 from app.modules.commerce.sale_lifecycle import SaleLineExecution, reconcile_order
 execution=await db.scalar(select(SaleLineExecution).where(SaleLineExecution.order_line_id==line.id))
 if execution and execution.status=='procurement_pending':
  execution.status='manual_review'
 await reconcile_order(db,order)

async def refund(db,job):
 ref=await db.get(Refund,UUID(job.payload['id']))
 if not ref:return
 # There is no safe blind retry of an ambiguous external refund.
 if ref.status=='uncertain':
  raise ValueError('Refund outcome uncertain: manual provider reconciliation required')
 if ref.status!='pending':return
 if ref.provider_id:
  ref.status='provider_pending'
  return
 pay=await db.get(Payment,ref.payment_id)
 if not pay or pay.status!='succeeded' or not pay.provider_id:
  raise ValueError('Confirmed SBP payment required for refund')
 # Persist the uncertainty marker BEFORE the external side effect.
 ref.status='uncertain';await db.commit()
 remote=await gateway().refund(pay.provider_id,ref.amount,ref.id)
 # A malformed or wrong-currency ACK leaves an 'uncertain' record. Never retry POST.
 status=validate_refund_response(remote,refund_id=None,payment_id=pay.provider_id,amount=ref.amount)
 ref.provider_id=remote['id']
 ref.status='provider_pending' if status=='pending' else status

async def replacement(db,job):
 rep=await db.get(Replacement,UUID(job.payload['id']))
 if rep and rep.status=='accepted_pending_live_validation':
  rep.status='manual_review' # Must re-verify alternative before binding procurement.

async def one(factory=None):
 factory=factory or get_session_factory()
 async with factory() as db:
  job=await db.scalar(select(Job).where(Job.status=='pending',Job.due_at<=utc()).order_by(Job.due_at,Job.id).with_for_update(skip_locked=True))
  if job is None:
   # Crash recovery: do not automatically retry purchase/refund calls with uncertain result.
   stalled=await db.scalar(select(Job).where(Job.status=='running',Job.due_at<utc()).order_by(Job.due_at).with_for_update(skip_locked=True))
   if stalled:
    safe_retry = stalled.kind in ('search','feed_import','feed_catalog_sync','catalog_bulk','catalog_enrichment','multi_enrichment','draft_materialization','payment_reconcile','refund_reconcile')
    stalled.status='pending' if safe_retry and stalled.attempts < (12 if stalled.kind in ('payment_reconcile','refund_reconcile') else 5) else 'manual_review'
    stalled.last_error='worker_timeout_requires_reconciliation' if not safe_retry else (
        'retry_budget_exhausted_manual_review' if stalled.attempts >= (12 if stalled.kind in ('payment_reconcile','refund_reconcile') else 5) else 'retry_backoff_after_timeout')
    if stalled.status == 'pending':
     stalled.due_at=utc()+timedelta(minutes=min(2**max(1,stalled.attempts),60))
    await db.commit()
    return True
   return False
  job.status='running';job.attempts+=1;job.due_at=utc()+timedelta(hours=2 if job.kind in ('feed_import','feed_catalog_sync','catalog_bulk','catalog_enrichment','multi_enrichment','draft_materialization') else 0,minutes=5 if job.kind not in ('feed_import','feed_catalog_sync','catalog_bulk','catalog_enrichment','multi_enrichment','draft_materialization') else 0);job_id=job.id;kind=job.kind;await db.commit()
  try:
   if kind=='search':
    from app.modules.offer_engine.service import aware, runtime
    cfg=await runtime(db,job.payload['supplier'])
    account = None
    if job.payload.get('account_id'):
     from app.modules.marketplace.models import SupplierAccount
     account=await db.get(SupplierAccount,UUID(job.payload['account_id']))
     if not account or not account.enabled or not account.read_enabled:
      job.status='done';await db.commit();return True
    block_until=max(filter(None,[aware(cfg.next_request_at),aware(cfg.circuit_open_until)]),default=utc())
    if cfg.enabled and block_until>utc():
     job.status='pending'
     job.due_at=block_until
     await db.commit()
     return True
    if cfg.enabled:
     from app.modules.offer_engine.discovery import claim_supplier_budget
     if not await claim_supplier_budget(job.payload['supplier'],
           str(account.id) if account else None,account.requests_per_hour if account else None):
      # Hourly cap reached, or Redis unavailable: no supplier HTTP call.
      job.status='pending';job.due_at=utc()+timedelta(minutes=15)
      state=await db.scalar(select(CacheState).where(
       CacheState.supplier_code==job.payload['supplier'],
       CacheState.lookup_key==job.payload['key']))
      if state and state.status=='refreshing':
       state.expires_at=job.due_at+timedelta(minutes=2)
      await db.commit()
      return True
     cfg.next_request_at=utc()+timedelta(seconds=cfg.min_interval_seconds)
     await db.commit()
    await search(db,job)
   elif kind=='feed_import':
    from app.modules.supplier_feeds.service import import_batch
    await import_batch(db, UUID(job.payload['batch_id']))
   elif kind=='feed_catalog_sync':
    from app.modules.supplier_feeds.pipeline import run_sync
    await run_sync(db,UUID(job.payload['sync_id']),int(job.payload['step']))
   elif kind=='catalog_bulk':
    from app.modules.catalog_bulk.service import execute_run
    await execute_run(db, UUID(job.payload['run_id']))
   elif kind=='catalog_enrichment':
    from app.modules.catalog_enrichment.service import execute_run as execute_enrichment
    await execute_enrichment(db, UUID(job.payload['run_id']))
   elif kind=='multi_enrichment':
    from app.modules.multi_enrichment.service import execute_run as execute_multi
    await execute_multi(db, UUID(job.payload['run_id']))
   elif kind=='draft_materialization':
    from app.modules.draft_materialization.service import execute_run as execute_draft
    await execute_draft(db, UUID(job.payload['run_id']),
                        auto_step=job.payload.get('auto_step'),
                        auto_generation=job.payload.get('auto_generation'))
   elif kind=='procurement':await procurement(db,job)
   elif kind=='refund':await refund(db,job)
   elif kind=='replacement':await replacement(db,job)
   elif kind=='payment_reconcile':
    from app.modules.payments.reconciliation import reconcile_payment_job
    await reconcile_payment_job(db,job)
   elif kind=='refund_reconcile':
    from app.modules.payments.refund_reconciliation import reconcile_refund_job
    await reconcile_refund_job(db,job)
   elif kind=='marketplace_reservation_expire':
    from app.modules.marketplace.checkout_v017 import expire_reservation
    await expire_reservation(db,UUID(job.payload['order_id']))
   else:raise ValueError('Unknown job kind')
   if kind == 'procurement':
    # No external write contract: a reviewed-but-unplaced supplier purchase
    # must stay visible to operators, never disappear as a successful job.
    after = await db.get(Procurement, UUID(job.payload['id']))
    job.status = 'manual_review' if after and after.status == 'manual_review' else 'done'
   elif kind not in ('payment_reconcile','refund_reconcile') or job.status=='running':
    job.status='done'
   await db.commit()
   if kind=='search':
    from app.modules.offer_engine.cache import invalidate
    state=await db.scalar(select(CacheState).where(
     CacheState.supplier_code==job.payload['supplier'],CacheState.lookup_key==job.payload['key']))
    if state and state.product_id:
     await invalidate(state.product_id)
  except Exception as e:
   await db.rollback();job=await db.get(Job,job_id)
   job.status='manual_review' if kind in ('procurement','refund','replacement','marketplace_reservation_expire','payment_reconcile','refund_reconcile') else 'failed'
   job.last_error=type(e).__name__;await db.commit()
   logging.getLogger(__name__).warning('worker_job_failed job_id=%s kind=%s error_type=%s',
                                       job_id,kind,type(e).__name__)
  return True
async def _refresh_loop():
 from app.modules.offer_engine.service import hot_refresh
 while True:
  try:
   async with get_session_factory()() as session:
    await hot_refresh(session)
    from app.modules.payments.reconciliation import schedule_payment_reconciliation
    await schedule_payment_reconciliation(session)
    from app.modules.payments.refund_reconciliation import schedule_refund_reconciliation
    await schedule_refund_reconciliation(session)
    # Coarse health counters; log only operational aggregates, never job payloads.
    pending=await session.scalar(select(func.count()).select_from(Job).where(Job.status=='pending'))
    overdue=await session.scalar(select(func.count()).select_from(Job).where(
        Job.status=='pending',Job.due_at<utc()-timedelta(minutes=5)))
    manual=await session.scalar(select(func.count()).select_from(Job).where(Job.status=='manual_review'))
    logging.getLogger(__name__).info('queue_health pending=%d overdue=%d manual_review=%d',
        pending or 0,overdue or 0,manual or 0)
  except Exception as exc:
   logging.getLogger(__name__).warning('hot_refresh_failed error_type=%s',type(exc).__name__)
  await asyncio.sleep(30)

async def _consumer_loop():
 while True:
  try:
   if not await one():await asyncio.sleep(2)
  except Exception as exc:
   logging.getLogger(__name__).warning('worker_cycle_failed error_type=%s',type(exc).__name__)
   await asyncio.sleep(3)

async def main():
 from app.modules.offer_engine.secret_redaction import install_redaction
 install_redaction()
 # Each task calls one(), which opens a separate AsyncSession. Never share a
 # session between concurrent consumers. SQL SKIP LOCKED prevents double claim.
 concurrency=get_settings().worker_concurrency
 try:
  await asyncio.gather(_refresh_loop(),*(_consumer_loop() for _ in range(concurrency)))
 finally:
  from app.modules.offer_engine.cache import close_client
  await close_client()
  from app.modules.offer_engine.http_pool import close_clients
  await close_clients()

if __name__=='__main__':
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 asyncio.run(main())
