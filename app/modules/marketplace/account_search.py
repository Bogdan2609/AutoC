"""Verified destination + independent supplier account search scheduling.

Uses existing durable Job + CacheState, never performs HTTP in request handlers.
"""
import hashlib
import uuid
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.modules.accounts.models import utc
from app.integrations.suppliers.registry import SUPPLIER_CODES
from app.modules.offer_engine.discovery import nested
from app.modules.commerce.models import Job
from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
from app.modules.marketplace.account_credentials import ready
from app.modules.offer_engine.models import CacheState
from app.modules.offer_engine.service import aware
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.core.config import get_settings
from app.modules.suppliers.models import Supplier


def account_key(brand, article, account_id, destination_id):
    digest=hashlib.sha256(f'{account_id}:{destination_id}'.encode()).hexdigest()[:20]
    return f'{normalize_brand(brand)}:{normalize_article(article)}:account:{digest}'


async def configured_accounts(db, code):
    supplier=await db.scalar(select(Supplier).where(Supplier.code==code))
    if not supplier:return []
    return (await db.scalars(select(SupplierAccount).where(
        SupplierAccount.supplier_id==supplier.id).order_by(SupplierAccount.created_at,SupplierAccount.id))).all()


async def available_destinations(db, code, *, city=None):
    accounts=await configured_accounts(db,code)
    if not accounts:
        return [], False
    # One destination query for all accounts (instead of N SQL queries).
    enabled_accounts = {a.id: a for a in accounts if (
        a.enabled and a.read_enabled and get_settings().supplier_api_enabled)}
    if not enabled_accounts:
        return [], True
    rows = (await db.execute(select(SupplierDestination, FulfillmentNode).join(
        FulfillmentNode, SupplierDestination.node_id == FulfillmentNode.id).where(
        SupplierDestination.account_id.in_(enabled_accounts),
        SupplierDestination.enabled.is_(True), SupplierDestination.verified.is_(True),
        FulfillmentNode.active.is_(True)).order_by(
        SupplierDestination.account_id, SupplierDestination.id))).all()
    items = []
    for destination, node in rows:
        account = enabled_accounts[destination.account_id]
        if node.merchant_id != account.merchant_id:  # strict tenant isolation
            continue
        if city and node.city.casefold() != city.casefold():
            continue
        if ready(code, account, destination):
            items.append((account, destination, node))
    # Accounts own this supplier namespace even with zero active destinations.
    return items, True


# Keep public discovery fanout bounded; stale-first selection prevents a fixed
# first-four subset from starving every additional account indefinitely.
MAX_ACCOUNT_REFRESH_PER_SUPPLIER = 4


async def refresh_candidates(db, code, brand, article, *, city=None, force=False):
    rows, configured = await available_destinations(db, code, city=city)
    if not configured or not rows:
        return []
    keys = [account_key(brand, article, a.id, d.id) for a, d, _ in rows]
    states = {}
    # Production DB: one read for all cache states; lightweight test fakes
    # may expose scalar() only.
    if hasattr(db, 'execute'):
        found = (await db.scalars(select(CacheState).where(
            CacheState.supplier_code == code, CacheState.lookup_key.in_(keys)))).all()
        states = {s.lookup_key: s for s in found}
    else:
        for key in keys:
            states[key] = await db.scalar(select(CacheState).where(
                CacheState.supplier_code == code, CacheState.lookup_key == key))
    now = utc()
    due = []
    for account, destination, node in rows:
        key = account_key(brand, article, account.id, destination.id)
        state = states.get(key)
        if state and state.status == 'refreshing' and state.expires_at and aware(state.expires_at) > now:
            continue
        if state and force and state.last_requested_at and (
            now - aware(state.last_requested_at)).total_seconds() < 30:
            continue
        if state and not force and state.expires_at and aware(state.expires_at) > now:
            continue
        # Never-requested entries first; for ties, oldest attempt first.
        priority = aware(state.last_requested_at) if state and state.last_requested_at else now - timedelta(days=3650)
        due.append((priority, key, account, destination, node, state))
    due.sort(key=lambda x: (x[0], x[1]))
    return [(a, d, n, k, state) for _, k, a, d, n, state in due[:MAX_ACCOUNT_REFRESH_PER_SUPPLIER]]


async def pending_account_refreshes(db, brand, article, *, city=None):
    """Count new account-scoped jobs without mutating state or quota.

    Cached account destinations must not charge the public discovery budget.
    """
    count = 0
    for code in SUPPLIER_CODES:
        count += len(await refresh_candidates(db, code, brand, article, city=city))
    return count


async def enqueue_accounts(db, brand, article, product_id=None, *, city=None, force=False, codes=None):
    now=utc()
    out=[]
    # Admin scoped refresh must not contact unrelated supplier accounts.
    allowed = SUPPLIER_CODES
    for code in allowed if codes is None else codes:
        if code not in allowed:
            continue
        candidates = await refresh_candidates(db, code, brand, article, city=city, force=force)
        for account, destination, node, key, state in candidates:
            if state is not None:
                state = await db.scalar(select(CacheState).where(
                    CacheState.supplier_code == code, CacheState.lookup_key == key).with_for_update())
            if state is None:
                try:
                    async with nested(db):
                        state=CacheState(supplier_code=code,lookup_key=key,product_id=product_id,
                                         expires_at=now,status='pending')
                        db.add(state)
                        await db.flush()
                except IntegrityError:
                    state=await db.scalar(select(CacheState).where(
                        CacheState.supplier_code==code,CacheState.lookup_key==key).with_for_update())
            if not state:continue
            if product_id:state.product_id=product_id
            if not force and aware(state.expires_at)>now:continue
            if state.status=='refreshing' and aware(state.expires_at)>now:continue
            if force and state.last_requested_at and (now-aware(state.last_requested_at)).total_seconds()<30:
                continue
            request_id=str(uuid.uuid4())
            state.status='refreshing'
            state.expires_at=now+timedelta(minutes=5)
            state.last_requested_at=now
            db.add(Job(kind='search',status='pending',dedup_key='account-search:'+request_id,
                       payload={'supplier':code,'brand':brand,'article':article,
                                'account_id':str(account.id),'destination_id':str(destination.id),
                                'product_id':str(product_id) if product_id else None,
                                'key':key,'requested_at':now.isoformat(),
                                'discovery':product_id is None}))
            out.append({'account_id':str(account.id),'destination_id':str(destination.id),
                        'supplier':code,'city':node.city,'queued':True})
    return out
