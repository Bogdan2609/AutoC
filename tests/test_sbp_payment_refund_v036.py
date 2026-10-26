"""v0.36: fake-bank-only SBP refunds and durable read-only reconciliation."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User
from app.modules.commerce.models import Job, Order, OrderLine, Replacement
from app.modules.payments.gateway import FakeYooKassa, GatewayError, idempotence_key, provider_reference
from app.modules.payments.models import Payment, Refund
from app.modules.payments.refund_reconciliation import (
    refund_next_check_delay, reconcile_refund_job, schedule_refund_reconciliation,
    validate_refund_response, verify_refund_status,
)
from app.workers import runner


def _enabled(monkeypatch, active=True):
    monkeypatch.setenv('PAYMENTS_RECONCILIATION_ENABLED', 'true' if active else 'false')
    get_settings.cache_clear()


async def make_refund(db, *, status='provider_pending', provider_id='refo-123'):
    user = User(email=f'refund-36-{uuid4()}@example.test', password_hash='hash',
                display_name='Test buyer', is_active=True)
    db.add(user)
    await db.flush()
    order = Order(user_id=user.id, idempotency_key=str(uuid4()), total=Decimal('90.00'),
                  request_details={'workflow': 'sbp_checkout'},
                  contact='Buyer', phone='79999999999', shipping_address='Warehouse')
    db.add(order)
    await db.flush()
    line = OrderLine(order_id=order.id, title='Air filter', supplier_code='rossko',
                     quantity=1, unit_retail=Decimal('90.00'), unit_purchase=Decimal('50.00'))
    db.add(line)
    await db.flush()
    replacement = Replacement(order_line_id=line.id)
    db.add(replacement)
    await db.flush()
    payment = Payment(order_id=order.id, status='succeeded',
                      provider_id='pay-' + str(uuid4()), amount=Decimal('90.00'))
    db.add(payment)
    await db.flush()
    refund = Refund(payment_id=payment.id, replacement_id=replacement.id,
                    provider_id=provider_id, amount=Decimal('25.00'), status=status)
    db.add(refund)
    await db.commit()
    return refund, payment


@pytest.mark.parametrize('bad', ('', '../payments', 'a?b', 'abc/def', 'a' * 151, None))
def test_provider_reference_rejects_url_injection(bad):
    with pytest.raises(GatewayError):
        provider_reference(bad)


@pytest.mark.parametrize('bad', ('', 'x' * 65, 'key with spaces', '../../test', None))
def test_idempotence_key_is_bounded_and_opaque(bad):
    with pytest.raises(GatewayError):
        idempotence_key(bad)


def test_refund_response_requires_explicit_rub_and_exact_bank_identity():
    reference = {'id': 'ref-36', 'payment_id': 'pay-36', 'status': 'pending',
                 'amount': {'value': '25.00', 'currency': 'RUB'}}
    assert validate_refund_response(reference, refund_id='ref-36',
                                    payment_id='pay-36', amount=Decimal('25')) == 'pending'
    for changed in (
        {'amount': {'value': '25.00', 'currency': 'EUR'}},
        {'amount': {'value': '25.00'}},
        {'amount': {'value': '26.00', 'currency': 'RUB'}},
        {'payment_id': 'other-pay'},
        {'id': 'other-ref'},
        {'status': 'unknown'},
    ):
        with pytest.raises(HTTPException) as exc:
            validate_refund_response({**reference, **changed}, refund_id='ref-36',
                                     payment_id='pay-36', amount=Decimal('25'))
        assert exc.value.status_code == 409


def test_refund_polling_bounded_backoff():
    assert refund_next_check_delay(0).total_seconds() == 60
    assert refund_next_check_delay(3).total_seconds() == 480
    assert refund_next_check_delay(100).total_seconds() == 4 * 3600


@pytest.mark.asyncio
async def test_refund_scheduler_never_reissues_refund_and_deduplicates(client, monkeypatch):
    _enabled(monkeypatch)
    async for db in app.dependency_overrides[get_session]():
        valid, payment = await make_refund(db, provider_id='refund-36-known')
        await make_refund(db, status='uncertain', provider_id=None)
        await make_refund(db, status='succeeded', provider_id='refund-36-finished')
        assert await schedule_refund_reconciliation(db) == 1
        assert await schedule_refund_reconciliation(db) == 0
        jobs = (await db.scalars(select(Job).where(Job.kind == 'refund_reconcile'))).all()
        assert len(jobs) == 1
        assert jobs[0].payload['refund_id'] == str(valid.id)
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pending_refund_rechecked_until_bank_confirms_once(client, monkeypatch):
    _enabled(monkeypatch)
    async for db in app.dependency_overrides[get_session]():
        refund, payment = await make_refund(db, provider_id='refund-36-pending')
        assert await schedule_refund_reconciliation(db) == 1
        job = (await db.scalars(select(Job).where(Job.kind == 'refund_reconcile'))).one()
        job.attempts = 1
        class FakeBank:
            status = 'pending'
            reads = 0
            async def refund_status(self, provider_id):
                self.reads += 1
                return {'id': provider_id, 'payment_id': payment.provider_id,
                        'amount': {'value': '25.00', 'currency': 'RUB'}, 'status': self.status}
            async def refund(self, *args):
                raise AssertionError('No refund POST is allowed during reconciliation')
        fake = FakeBank()
        from app.modules.payments import refund_reconciliation
        monkeypatch.setattr(refund_reconciliation, 'gateway', lambda: fake)
        await reconcile_refund_job(db, job)
        await db.commit()
        assert job.status == 'pending' and (await db.get(Refund, refund.id)).status == 'provider_pending'
        fake.status = 'succeeded'
        job.attempts = 2
        await reconcile_refund_job(db, job)
        await db.commit()
        assert job.status == 'done' and (await db.get(Refund, refund.id)).status == 'succeeded'
        await reconcile_refund_job(db, job)
        assert fake.reads == 2
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_refund_wrong_currency_enters_manual_review_without_bank_write(client, monkeypatch):
    _enabled(monkeypatch)
    async for db in app.dependency_overrides[get_session]():
        refund, payment = await make_refund(db, provider_id='refund-36-foreign')
        assert await schedule_refund_reconciliation(db) == 1
        job = (await db.scalars(select(Job).where(Job.kind == 'refund_reconcile'))).one()
        class BadBank:
            async def refund_status(self, provider_id):
                return {'id': provider_id, 'payment_id': payment.provider_id,
                        'amount': {'value': '25.00', 'currency': 'USD'}, 'status': 'succeeded'}
        from app.modules.payments import refund_reconciliation
        monkeypatch.setattr(refund_reconciliation, 'gateway', lambda: BadBank())
        await reconcile_refund_job(db, job)
        await db.commit()
        assert job.status == 'manual_review' and job.last_error == 'provider_validation_409'
        assert (await db.get(Refund, refund.id)).status == 'provider_pending'
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_refund_without_provider_id_must_stay_manual_not_bank_recreated(client):
    async for db in app.dependency_overrides[get_session]():
        refund, _ = await make_refund(db, status='uncertain', provider_id=None)
        with pytest.raises(HTTPException) as exc:
            await verify_refund_status(db, refund.id)
        assert exc.value.status_code == 409
        assert (await db.get(Refund, refund.id)).status == 'uncertain'
        break


@pytest.mark.asyncio
async def test_initial_refund_wrong_ack_does_not_reissue_on_retry(client, monkeypatch):
    async for db in app.dependency_overrides[get_session]():
        refund, payment = await make_refund(db, status='pending', provider_id=None)
        sent = []
        class BadAck:
            async def refund(self, pid, amount, key):
                sent.append(str(key))
                return {'id': 'ref-new-id', 'payment_id': pid, 'status': 'pending',
                        'amount': {'value': '25.00', 'currency': 'EUR'}}
        monkeypatch.setattr(runner, 'gateway', lambda: BadAck())
        job = SimpleNamespace(payload={'id': str(refund.id)})
        with pytest.raises(HTTPException):
            await runner.refund(db, job)
        await db.commit()
        assert (await db.get(Refund, refund.id)).status == 'uncertain'
        with pytest.raises(ValueError, match='manual provider reconciliation'):
            await runner.refund(db, job)
        assert sent == [str(refund.id)]
        break


@pytest.mark.asyncio
async def test_bank_admin_refund_reconcile_is_private_and_read_only(client, admin_headers, monkeypatch):
    from app.modules.payments import api
    async for db in app.dependency_overrides[get_session]():
        refund, payment = await make_refund(db, provider_id='refund-36-admin')
        ref_id = str(refund.id)
        payment_id = payment.provider_id
        break
    class Bank:
        async def refund_status(self, provider_id):
            return {'id': provider_id, 'payment_id': payment_id,
                    'amount': {'value': '25.00', 'currency': 'RUB'}, 'status': 'succeeded'}
        async def refund(self, *args):
            raise AssertionError('Admin reconcile must never send POST /refunds')
    monkeypatch.setattr(api, 'gateway', lambda: Bank())
    path = f'/api/v1/payments/admin/refunds/{ref_id}/reconcile'
    assert (await client.post(path)).status_code == 403
    response = await client.post(path, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'succeeded'
    assert (await client.post(path, headers=admin_headers)).json()['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_reconciliation_is_disabled_by_default(client, monkeypatch):
    _enabled(monkeypatch, False)
    async for db in app.dependency_overrides[get_session]():
        await make_refund(db)
        assert await schedule_refund_reconciliation(db) == 0
        break
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fake_refund_requires_explicit_settlement(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'test')
    get_settings.cache_clear()
    fake = FakeYooKassa()
    created = await fake.refund('pay-36', Decimal('25.00'), uuid4())
    assert created['status'] == 'pending'
    fake.settle_refund(created['id'])
    assert (await fake.refund_status(created['id']))['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_payment_cannot_fulfill_tampered_order_line_amount(client, monkeypatch):
    """Paid bank total alone must not authorize procurement on corrupted line data."""
    from app.modules.payments import api as payments_api
    from app.modules.commerce.models import Procurement
    async for db in app.dependency_overrides[get_session]():
        user = User(email=f'payment-line-36-{uuid4()}@example.test', password_hash='hash',
                    display_name='Buyer', is_active=True)
        db.add(user)
        await db.flush()
        order = Order(user_id=user.id, idempotency_key=str(uuid4()), total=Decimal('100.00'),
                      total_is_final=True, status='payment_pending',
                      request_details={'workflow': 'sbp_checkout'}, contact='Buyer',
                      phone='79999999999', shipping_address='Warehouse')
        db.add(order)
        await db.flush()
        db.add(OrderLine(order_id=order.id, title='Tampered filter', supplier_code='rossko',
                         quantity=1, unit_retail=Decimal('90.00'),
                         unit_purchase=Decimal('50.00')))
        payment = Payment(order_id=order.id, status='pending',
                          provider_id='payment-v036-corrupt', amount=Decimal('100.00'))
        db.add(payment)
        await db.commit()
        class Bank:
            async def status(self, provider_id):
                return {'id': provider_id, 'status': 'succeeded', 'paid': True,
                        'payment_method': {'type': 'sbp'},
                        'metadata': {'order_id': str(order.id)},
                        'amount': {'value': '100.00', 'currency': 'RUB'}}
        monkeypatch.setattr(payments_api, 'gateway', lambda: Bank())
        with pytest.raises(HTTPException) as exc:
            await payments_api.verify_remote(db, payment)
        assert exc.value.status_code == 409
        assert (await db.get(Payment, payment.id)).status == 'pending'
        assert not (await db.scalars(select(Procurement))).all()
        break
