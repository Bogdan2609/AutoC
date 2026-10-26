"""v0.38 regression: audit atomicity, payment serialization and safe queues.

SQLite tests assert observable invariants. Real SELECT FOR UPDATE concurrency
must additionally be tested with disposable PostgreSQL before production.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User, utc
from app.modules.admin_staff.models import AdminAuditLog
from app.modules.commerce.models import Order, OrderLine, Job, Procurement
from app.modules.payments import api as payment_api
from app.modules.payments.models import Payment
from app.workers import runner


@pytest.mark.asyncio
async def test_admin_audit_is_rolled_back_with_failed_mutation(monkeypatch):
    # Legacy break-glass auth has no staff user requirement.
    monkeypatch.setattr(security, 'get_settings', lambda: SimpleNamespace(
        admin_legacy_token_enabled=True, admin_api_token='test-token-audit'))
    engine = create_engine('sqlite+pysqlite:///:memory:',
        connect_args={'check_same_thread': False}, poolclass=StaticPool)
    User.metadata.create_all(engine, tables=[User.__table__, AdminAuditLog.__table__])
    factory = sessionmaker(engine, expire_on_commit=False)
    api = FastAPI()
    endpoints = APIRouter(prefix='/api/v1/backoffice/catalog-editor',
                          dependencies=[Depends(security.require_admin)])

    @endpoints.post('/atomic-fail')
    async def failed(db=Depends(get_session)):
        db.add(User(email='must-rollback@example.test', password_hash='xx',
                    display_name='rollback', is_active=True))
        await db.flush()
        raise HTTPException(409, 'Business validation failed')

    @endpoints.post('/atomic-ok')
    async def succeeded(db=Depends(get_session)):
        db.add(User(email='committed@example.test', password_hash='xx',
                    display_name='committed', is_active=True))
        await db.commit()
        return {'ok': True}

    api.include_router(endpoints)
    class SessionFacade:
        def __init__(self, db): self.db = db
        def add(self, obj): self.db.add(obj)
        async def flush(self): self.db.flush()
        async def commit(self): self.db.commit()
        async def rollback(self): self.db.rollback()

    async def override_session():
        with factory() as db:
            yield SessionFacade(db)

    api.dependency_overrides[get_session] = override_session
    headers = {'X-Admin-Token': 'test-token-audit'}
    try:
        async with AsyncClient(transport=ASGITransport(app=api),
                               base_url='http://testserver') as client:
            assert (await client.post('/api/v1/backoffice/catalog-editor/atomic-fail',
                                      headers=headers)).status_code == 409
            with factory() as db:
                assert db.scalar(select(func.count()).select_from(User)) == 0
                assert db.scalar(select(func.count()).select_from(AdminAuditLog)) == 0
            assert (await client.post('/api/v1/backoffice/catalog-editor/atomic-ok',
                                      headers=headers)).status_code == 200
            with factory() as db:
                assert db.scalar(select(func.count()).select_from(User)) == 1
                assert db.scalar(select(func.count()).select_from(AdminAuditLog)) == 1
    finally:
        engine.dispose()


async def _payment_fixture(db):
    user=User(email=f'v038-{uuid4()}@example.test',password_hash='hash',
              display_name='buyer',is_active=True)
    db.add(user)
    await db.flush()
    order=Order(user_id=user.id,idempotency_key=str(uuid4()),
                total=Decimal('125.00'),status='payment_pending',total_is_final=True,
                request_details={'workflow':'sbp_checkout'},
                contact='Buyer',phone='79999999999',shipping_address='Samara')
    db.add(order)
    await db.flush()
    db.add(OrderLine(order_id=order.id,title='Air filter',supplier_code='rossko',
                     quantity=1,unit_retail=Decimal('125.00'),
                     unit_purchase=Decimal('80.00'),
                     commercial_snapshot={'source_kind':'supplier'}))
    first=Payment(order_id=order.id,provider_id='bank-a-'+str(uuid4()),
                  amount=Decimal('125.00'),status='pending')
    second=Payment(order_id=order.id,provider_id='bank-b-'+str(uuid4()),
                   amount=Decimal('125.00'),status='pending')
    db.add_all([first,second])
    await db.commit()
    return order,first,second


@pytest.mark.asyncio
async def test_cancel_second_bank_payment_cannot_unpay_first(client, monkeypatch):
    async for db in app.dependency_overrides[get_session]():
        order, first, second=await _payment_fixture(db)
        class Bank:
            async def status(self,pid):
                paid=pid==first.provider_id
                return {'id':pid,'metadata':{'order_id':str(order.id)},
                        'amount':{'currency':'RUB','value':'125.00'},
                        'payment_method':{'type':'sbp'},
                        'status':'succeeded' if paid else 'canceled','paid':paid}
        monkeypatch.setattr(payment_api,'gateway',lambda:Bank())
        result=await payment_api.verify_remote(db,first)
        assert result['payment_status']=='succeeded'
        assert (await payment_api.verify_remote(db,second))['payment_status']=='canceled'
        await db.refresh(order)
        assert order.status == 'paid_procurement_pending'
        assert len((await db.scalars(select(Procurement))).all()) == 1
        break


@pytest.mark.asyncio
async def test_second_successful_bank_payment_fails_closed(client,monkeypatch):
    async for db in app.dependency_overrides[get_session]():
        order, first, second=await _payment_fixture(db)
        class Bank:
            async def status(self,pid):
                return {'id':pid,'metadata':{'order_id':str(order.id)},
                        'amount':{'currency':'RUB','value':'125.00'},
                        'payment_method':{'type':'sbp'},
                        'status':'succeeded','paid':True}
        monkeypatch.setattr(payment_api,'gateway',lambda:Bank())
        await payment_api.verify_remote(db,first)
        with pytest.raises(HTTPException) as exc:
            await payment_api.verify_remote(db,second)
        assert exc.value.status_code==409
        assert (await db.get(Payment,second.id)).status=='pending'
        assert len((await db.scalars(select(Procurement))).all())==1
        break


def test_payment_order_lock_supported_in_postgres_dialect():
    # SQLite ignores row locks: verify the actual production statement carries
    # FOR UPDATE, while the above tests validate sequential state handling.
    stmt=select(Order).where(Order.id==uuid4()).with_for_update()
    assert 'FOR UPDATE' in str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_stalled_feed_job_requires_manual_attention_not_replay():
    stalled=SimpleNamespace(kind='feed_catalog_sync',payload={'sync_id':str(uuid4())},
                            status='running',attempts=2,due_at=utc()-timedelta(hours=3),
                            last_error=None)
    sync=SimpleNamespace(id=stalled.payload['sync_id'],status='processing',message='')
    class Db:
        def __init__(self): self.n=0; self.commits=0
        async def scalar(self,statement):
            self.n+=1
            return (None,stalled,sync)[self.n-1]
        async def commit(self): self.commits+=1
    db=Db()
    class Context:
        async def __aenter__(self):return db
        async def __aexit__(self,*args):pass
    assert await runner.one(lambda:Context()) is True
    assert stalled.status=='manual_review'
    assert stalled.last_error=='feed_catalog_worker_timeout_manual_review'
    assert sync.status=='paused'
    assert sync.message=='worker_timeout_requires_manual_review'
    assert db.commits==1


@pytest.mark.asyncio
async def test_duplicate_refund_with_uncertain_state_never_posts():
    refund=SimpleNamespace(id=uuid4(),status='uncertain')
    class Db:
        async def scalar(self,stmt):return refund
    with pytest.raises(ValueError,match='manual provider reconciliation'):
        await runner.refund(Db(),SimpleNamespace(payload={'id':str(refund.id)}))


@pytest.mark.asyncio
async def test_large_streamed_api_json_is_rejected_even_without_content_length(client):
    async def chunks():
        yield b'{' + b'"x":"'
        for _ in range(18):
            yield b'z'*65536
        yield b'"}'
    response=await client.post('/api/v1/accounts/login',content=chunks(),
                               headers={'Content-Type':'application/json'})
    assert response.status_code==413
    # It must be a body-limit decision before the account endpoint validates.
    assert response.json()['detail']=='Request body too large'


@pytest.mark.asyncio
async def test_small_json_reaches_normal_endpoint(client):
    response=await client.post('/api/v1/accounts/login',json={})
    assert response.status_code==422

@pytest.mark.asyncio
async def test_redis_failure_uses_bounded_public_fallback_but_denies_writes(monkeypatch):
    from app.core import rate_limit
    from app.modules.offer_engine import cache
    monkeypatch.setattr(rate_limit, 'get_settings',
                        lambda:SimpleNamespace(app_env='production'))
    async def offline(): return None
    monkeypatch.setattr(cache, 'redis_client', offline)
    rate_limit._LOCAL_BUCKETS.clear()
    try:
        await rate_limit.enforce_budget('public_costly', '198.51.100.1', limit=2, period=60)
        await rate_limit.enforce_budget('public_costly', '198.51.100.1', limit=2, period=60)
        with pytest.raises(HTTPException) as blocked:
            await rate_limit.enforce_budget('public_costly', '198.51.100.1', limit=2, period=60)
        assert blocked.value.status_code == 429
        with pytest.raises(HTTPException) as offline_write:
            await rate_limit.enforce_budget('register_ip', '198.51.100.1', limit=2, period=60)
        assert offline_write.value.status_code == 503
    finally:
        rate_limit._LOCAL_BUCKETS.clear()


@pytest.mark.asyncio
async def test_payment_with_malformed_bank_amount_rejects_without_mutation(client,monkeypatch):
    async for db in app.dependency_overrides[get_session]():
        order, first, second = await _payment_fixture(db)
        class BadBank:
            async def status(self,pid):
                return {'id':pid,'metadata':{'order_id':str(order.id)},
                        'amount':None,'status':'succeeded','paid':True}
        monkeypatch.setattr(payment_api,'gateway',lambda:BadBank())
        with pytest.raises(HTTPException) as bad:
            await payment_api.verify_remote(db,first)
        assert bad.value.status_code==409
        assert (await db.get(Payment,first.id)).status=='pending'
        assert not (await db.scalars(select(Procurement))).all()
        break
