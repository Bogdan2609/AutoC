"""v0.33.4 offline regression: route RBAC coverage and durable bank polling."""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.main import app
from app.modules.accounts.models import User, utc
from app.modules.admin_staff.route_audit import route_audit
from app.modules.commerce.models import Job, Order, OrderLine, Procurement
from app.modules.payments.models import Payment
from app.modules.payments.reconciliation import (
    next_check_delay, reconcile_payment_job, schedule_payment_reconciliation,
)


def test_v012_admin_audit_import_and_real_guard_coverage():
    result = route_audit(app)
    assert result['policy_complete'] is True, result
    assert result['protected_routes'] >= 310
    assert result['functional_parity_verified'] is False


def test_admin_audit_detects_missing_guard_and_unclassified_admin_route():
    copy = FastAPI()
    router = APIRouter(prefix='/api/v1/admin')

    @router.get('/staff/audit')
    async def uncovered_guard():
        return {'ok': True}

    @router.post('/not-classified')
    async def unknown():
        return {'ok': True}

    copy.include_router(router)
    result = route_audit(copy)
    assert not result['policy_complete']
    assert 'GET /api/v1/admin/staff/audit' in result['unguarded_classified_routes']
    assert 'POST /api/v1/admin/not-classified' in result['unclassified_admin_routes']


def test_admin_audit_detects_duplicate_registration():
    copy = FastAPI()

    @copy.get('/api/v1/admin/staff/audit', dependencies=[Depends(require_admin)])
    async def first():
        return 'first'

    @copy.get('/api/v1/admin/staff/audit', dependencies=[Depends(require_admin)])
    async def second():
        return 'second'

    report = route_audit(copy)
    assert not report['policy_complete']
    assert 'GET /api/v1/admin/staff/audit' in report['duplicate_method_paths']


def test_exponential_requery_delay_is_bounded():
    assert next_check_delay(0) == timedelta(minutes=1)
    assert next_check_delay(3) == timedelta(minutes=8)
    assert next_check_delay(500) == timedelta(minutes=240)


def _enable_scheduler(monkeypatch):
    monkeypatch.setenv('PAYMENTS_RECONCILIATION_ENABLED', 'true')
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_queue_only_verifiable_pending_payments_and_never_duplicate(client, monkeypatch):
    _enable_scheduler(monkeypatch)
    async for db in app.dependency_overrides[get_session]():
        user = User(email='reconcile-queue@example.test', password_hash='hash',
                    display_name='buyer', is_active=True)
        db.add(user)
        await db.flush()
        order = Order(user_id=user.id, idempotency_key='queue-001',
                      total=Decimal('100.00'), request_details={'workflow':'sbp_checkout'},
                      contact='Test Buyer', phone='79999999999', shipping_address='Samara')
        db.add(order)
        await db.flush()
        old = utc() - timedelta(minutes=15)
        db.add_all([
            Payment(order_id=order.id, amount=Decimal('100.00'),
                    provider_id='known-pending', status='pending', created_at=old),
            Payment(order_id=order.id, amount=Decimal('100.00'),
                    provider_id=None, status='reconciliation_required', created_at=old),
            Payment(order_id=order.id, amount=Decimal('100.00'),
                    provider_id='already-paid', status='succeeded', created_at=old),
        ])
        await db.commit()
        assert await schedule_payment_reconciliation(db) == 1
        assert await schedule_payment_reconciliation(db) == 0
        jobs = (await db.scalars(select(Job).where(Job.kind=='payment_reconcile'))).all()
        assert len(jobs) == 1
        assert len(jobs[0].payload['payment_id']) == 36
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_retry_unsettled_status_then_confirm_once(client, monkeypatch):
    _enable_scheduler(monkeypatch)
    from app.modules.payments import api as payment_api
    async for db in app.dependency_overrides[get_session]():
        buyer = User(email='reconcile-confirm@example.test', password_hash='hash',
                     display_name='buyer', is_active=True)
        db.add(buyer)
        await db.flush()
        order = Order(user_id=buyer.id, idempotency_key='queue-002', total=Decimal('100.00'),
                      total_is_final=True, status='payment_pending',
                      request_details={'workflow':'sbp_checkout'}, contact='Test Buyer',
                      phone='79999999999', shipping_address='Samara')
        db.add(order)
        await db.flush()
        line=OrderLine(order_id=order.id,title='Filter',supplier_code='rossko',
                       quantity=1,unit_retail=Decimal('100.00'),unit_purchase=Decimal('50.00'),
                       commercial_snapshot={'source_kind':'supplier'})
        pay=Payment(order_id=order.id,provider_id='provider-test-234',
                    status='pending',amount=Decimal('100.00'),
                    created_at=utc()-timedelta(minutes=4))
        db.add_all([line,pay])
        await db.commit()
        order_id = order.id
        assert await schedule_payment_reconciliation(db) == 1
        job=(await db.scalars(select(Job).where(Job.kind=='payment_reconcile'))).one()
        job.attempts=1
        await db.commit()
        class Bank:
            succeeded=False
            calls=0
            async def status(self,provider_id):
                self.calls+=1
                return {'id':provider_id,'status':'succeeded' if self.succeeded else 'pending',
                        'paid':self.succeeded,'metadata':{'order_id':str(order_id)},
                        'amount':{'value':'100.00','currency':'RUB'},
                        'payment_method':{'type':'sbp'}}
        bank=Bank()
        monkeypatch.setattr(payment_api,'gateway',lambda: bank)
        await reconcile_payment_job(db,job)
        await db.commit()
        assert job.status == 'pending' and job.due_at>utc()
        assert (await db.get(Payment,pay.id)).status=='pending'
        bank.succeeded=True
        job.attempts=2
        await reconcile_payment_job(db,job)
        await db.commit()
        assert job.status == 'done'
        assert (await db.get(Payment,pay.id)).status=='succeeded'
        procs=(await db.scalars(select(Procurement))).all()
        assert len(procs)==1
        await reconcile_payment_job(db,job)
        assert len((await db.scalars(select(Procurement))).all())==1
        assert bank.calls==2
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_wrong_remote_amount_escalates_without_procurement(client, monkeypatch):
    _enable_scheduler(monkeypatch)
    from app.modules.payments import api as payment_api
    async for db in app.dependency_overrides[get_session]():
        buyer = User(email='reconcile-bad@example.test', password_hash='hash',
                     display_name='buyer', is_active=True)
        db.add(buyer)
        await db.flush()
        order=Order(user_id=buyer.id,idempotency_key='queue-003',total=Decimal('123.00'),
                    status='payment_pending',request_details={'workflow':'sbp_checkout'},
                    contact='Test Buyer',phone='79999999999',shipping_address='Samara')
        db.add(order)
        await db.flush()
        pay=Payment(order_id=order.id,provider_id='fake-bad-amount',status='pending',amount=Decimal('123.00'))
        db.add(pay)
        await db.flush()
        job=Job(kind='payment_reconcile',dedup_key='payment_reconcile:bad-case',
                payload={'payment_id':str(pay.id)}, status='running', attempts=1)
        db.add_all([pay,job])
        await db.commit()
        class Bank:
            async def status(self, provider_id):
                return {'id':provider_id,'status':'succeeded','paid':True,
                        'metadata':{'order_id':str(order.id)},
                        'amount':{'value':'999.00','currency':'RUB'},
                        'payment_method':{'type':'sbp'}}
        monkeypatch.setattr(payment_api,'gateway',lambda:Bank())
        await reconcile_payment_job(db,job)
        await db.commit()
        assert job.status=='manual_review'
        assert job.last_error=='provider_validation_409'
        assert (await db.get(Payment,pay.id)).status=='pending'
        assert not (await db.scalars(select(Procurement))).all()
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_reconciliation_disabled_by_default(client,monkeypatch):
    monkeypatch.setenv('PAYMENTS_RECONCILIATION_ENABLED','false')
    get_settings.cache_clear()
    async for db in app.dependency_overrides[get_session]():
        assert await schedule_payment_reconciliation(db)==0
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_admin_unsettled_payment_queue_is_redacted_and_not_public(client, admin_headers):
    async for db in app.dependency_overrides[get_session]():
        buyer = User(email='private-buyer@example.test', password_hash='hash',
                     display_name='BUYER SECRET FULL NAME', is_active=True)
        db.add(buyer)
        await db.flush()
        order = Order(user_id=buyer.id, idempotency_key='private-sbp-004',
                      total=Decimal('199.00'), status='payment_pending',
                      request_details={'workflow': 'sbp_checkout'},
                      contact='PRIVATE BUYER NAME', phone='+71110000999',
                      shipping_address='PRIVATE BUYER STREET 999')
        db.add(order)
        await db.flush()
        payment = Payment(order_id=order.id, status='reconciliation_required',
                          provider_id=None, amount=Decimal('199.00'))
        db.add(payment)
        await db.commit()
        payment_id = str(payment.id)
        break
    assert (await client.get('/api/v1/payments/admin/payments/needs-reconciliation')).status_code == 403
    response = await client.get('/api/v1/payments/admin/payments/needs-reconciliation',
                                headers=admin_headers)
    assert response.status_code == 200, response.text
    row = next(x for x in response.json() if x['payment_id'] == payment_id)
    assert row['manual_reconciliation_required'] is True
    assert row['provider_reference_present'] is False
    assert row['reconciliation_job_status'] is None
    for secret in ('PRIVATE BUYER', '+71110000999', '199.00', 'private-buyer@example.test'):
        assert secret not in response.text
