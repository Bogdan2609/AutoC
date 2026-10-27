"""Admin-only read-side operations center. No provider/network calls, no mutations.

Keep operational case types distinct: manual review is not a system failure;
provider pending is not a refund failure. No raw payloads or sensitive fields.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.accounts.models import utc
from app.modules.commerce.models import Job, Order, Procurement
from app.modules.payments.models import Payment, Refund
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from app.modules.suppliers.models import Supplier
from app.workers.job_lease_v043 import classify_job

router = APIRouter(prefix='/api/v1/backoffice/operations-health',
                   tags=['operations-health-v043'], dependencies=[Depends(require_admin)])


def safe_job(row: Job) -> dict:
    return {
        'id': str(row.id), 'kind': row.kind, 'status': row.status,
        'attempts': row.attempts, 'lease_until': row.due_at,
        **classify_job(row.kind, row.status),
        'error_type': (row.last_error if row.last_error in (
            'TimeoutError','NetworkError','Unavailable','HTTPException',
            'ValueError','IntegrityError','OperationalError',
            'worker_timeout_requires_reconciliation',
            'worker_timeout_requires_manual_review',
            'retry_budget_exhausted_manual_review',
            'retry_backoff_after_timeout') else None),
        'external_write_retry_allowed': False,
    }


@router.get('/summary')
async def summary(db: AsyncSession = Depends(get_session)):
    async def count(model, where):
        return int((await db.scalar(select(func.count()).select_from(model).where(where))) or 0)
    now = utc()
    return {
        'as_of': now, 'read_only': True, 'live_money_approved': False,
        'jobs': {
            'pending': await count(Job, Job.status == 'pending'),
            'overdue_pending': await count(Job, (Job.status == 'pending') & (Job.due_at < now)),
            'running': await count(Job, Job.status == 'running'),
            'lease_expired': await count(Job, (Job.status == 'running') & (Job.due_at < now)),
            'failed': await count(Job, Job.status == 'failed'),
            'manual_review': await count(Job, Job.status == 'manual_review'),
        },
        'payments': {
            'needs_attention': await count(Payment, Payment.status.in_((
                'creating', 'pending', 'waiting_for_capture', 'reconciliation_required'))),
        },
        'refunds': {
            'needs_attention': await count(Refund, Refund.status.in_((
                'uncertain', 'provider_pending', 'canceled'))),
        },
        'purchases': {
            'uncertain_or_manual': await count(SupplierPurchaseAttempt,
                SupplierPurchaseAttempt.status.in_((
                    'dispatch_unknown', 'accepted_unverified', 'manual_review', 'reconciling'))),
            'procurement_manual': await count(Procurement, Procurement.status == 'manual_review'),
        },
    }


@router.get('/jobs')
async def jobs(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Job).where(Job.status.in_((
        'failed', 'manual_review', 'running', 'pending')))
        .order_by(Job.due_at, Job.id).limit(100))).all()
    return {'read_only': True, 'jobs': [safe_job(job) for job in rows],
            'no_automatic_external_replay': True}


@router.get('/payments')
async def payments(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Payment).where(Payment.status.in_((
        'creating', 'pending', 'waiting_for_capture', 'reconciliation_required')))
        .order_by(Payment.created_at, Payment.id).limit(100))).all()
    return {'read_only': True, 'items': [
        {'id': str(p.id), 'order_id': str(p.order_id), 'status': p.status,
         'provider_reference_present': bool(p.provider_id),
         'manual_review_required': p.status == 'reconciliation_required' or not p.provider_id,
         'next_action': 'read_only_provider_reconcile_or_manual_review'}
        for p in rows]}


@router.get('/refunds')
async def refunds(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Refund).where(Refund.status.in_((
        'uncertain', 'provider_pending', 'canceled')))
        .order_by(Refund.id).limit(100))).all()
    return {'read_only': True, 'items': [
        {'id': str(r.id), 'status': r.status, 'payment_id': str(r.payment_id),
         'provider_reference_present': bool(r.provider_id),
         'manual_review_required': not r.provider_id or r.status == 'canceled',
         'next_action': 'verify_remote_refund_or_manual_review'}
        for r in rows]}


@router.get('/purchases')
async def purchases(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(SupplierPurchaseAttempt).where(
        SupplierPurchaseAttempt.status.in_((
            'dispatch_unknown', 'accepted_unverified', 'manual_review', 'reconciling')))
        .order_by(SupplierPurchaseAttempt.created_at, SupplierPurchaseAttempt.id).limit(100))).all()
    return {'read_only': True, 'items': [
        {'id': str(a.id), 'supplier_account_id': str(a.supplier_account_id),
         'destination_id': str(a.supplier_destination_id), 'status': a.status,
         'remote_reference_present': bool(a.remote_order_ids),
         'verified': a.verified_at is not None,
         'next_action': 'readback_and_operator_review', 'retry_create_allowed': False}
        for a in rows]}


@router.get('/supplier-factory')
async def supplier_factory(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(SupplierAccount, Supplier).join(
        Supplier, Supplier.id == SupplierAccount.supplier_id)
        .order_by(Supplier.code, SupplierAccount.id).limit(150))).all()
    ids = [a.id for a, _ in rows]
    routes = (await db.scalars(select(SupplierDestination).where(
        SupplierDestination.account_id.in_(ids)))).all() if ids else []
    by_account = {}
    for d in routes:
        by_account.setdefault(d.account_id, []).append(d)
    return {'read_only': True, 'live_supplier_order_dispatch': False,
        'accounts': [
            {'id': str(a.id), 'label': a.label, 'supplier': s.code,
             'enabled': a.enabled, 'read_enabled': a.read_enabled,
             'procurement_enabled_flag': a.procurement_enabled,
             'requests_per_hour': a.requests_per_hour,
             'destinations': len(by_account.get(a.id, ())),
             'verified_courier_routes': sum(1 for d in by_account.get(a.id, ())
                                             if d.verified and d.enabled)}
            for a,s in rows]}
