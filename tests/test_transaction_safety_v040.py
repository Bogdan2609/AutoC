"""v0.40 regression contracts. External bank/supplier network access is forbidden by conftest."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.bounded_requests import BoundedRequestMiddleware, request_policy
from app.core.redis_lease import RedisLease
from app.core.utc_time import as_utc
from app.modules.admin_staff.policy import role_allowed
from app.modules.payments.money_contract import payable_total, rub
from app.modules.payments.selection import selected_payment_stmt


def test_utc_time_aware_uses_conversion_not_relabel():
    plus_three = timezone(timedelta(hours=3))
    source = datetime(2026, 10, 26, 17, 0, tzinfo=plus_three)
    assert as_utc(source) == datetime(2026, 10, 26, 14, 0, tzinfo=timezone.utc)
    assert as_utc(datetime(2026, 10, 26, 14, 0)).tzinfo == timezone.utc


def test_canonical_rub_precision_and_quantities():
    assert payable_total([(Decimal('0.99'), 3), (Decimal('10.01'), 2)]) == Decimal('22.99')
    assert rub(Decimal('1.00')) == Decimal('1.00')
    for amount in [Decimal('0.005'), Decimal('-1.00'), Decimal('NaN'), 1.0, '1.00']:
        with pytest.raises(ValueError):
            rub(amount)
    with pytest.raises(ValueError):
        payable_total([(Decimal('1.00'), 0)])
    with pytest.raises(ValueError):
        payable_total([(Decimal('1.00'), True)])


def test_rbac_unknown_http_verb_denied():
    assert role_allowed('owner', 'marketplace', 'POST')
    assert not role_allowed('owner', 'marketplace', 'TRACE')
    assert not role_allowed('operator', 'marketplace', 'CONNECT')


def test_deterministic_payment_selection_expression():
    sql = str(selected_payment_stmt('fake-order-id'))
    assert 'ORDER BY' in sql and 'CASE' in sql and 'created_at DESC' in sql


def test_json_mutation_delete_is_bounded():
    assert request_policy('/api/v1/demo', 'DELETE', 'application/json') == (1048576, None)
    assert request_policy('/api/v1/payments/yookassa/webhook', 'POST', 'application/json') == (16384, 'webhook')
    assert request_policy('/api/v1/demo', 'GET', '') is None


@pytest.mark.asyncio
async def test_asgi_json_chunked_and_delete_bounds():
    app = FastAPI()
    app.add_middleware(BoundedRequestMiddleware)

    @app.delete('/api/v1/limited')
    async def delete_limited(request: Request):
        return {'bytes': len(await request.body())}

    @app.post('/api/v1/limited')
    async def post_limited(request: Request):
        return {'bytes': len(await request.body())}

    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as client:
        result = await client.request('DELETE', '/api/v1/limited', headers={'content-type': 'application/json'},
                                      content=b'a' * (1048576 + 1))
        assert result.status_code == 413
        result = await client.post('/api/v1/limited', content=b'{}',
                                    headers={'content-type': 'application/json'})
        assert result.status_code == 200 and result.json()['bytes'] == 2

        async def stream():
            yield b'a' * 700000
            yield b'b' * 500000
        result = await client.post('/api/v1/limited', content=stream(),
                                   headers={'content-type': 'application/json'})
        assert result.status_code == 413


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, nx, ex):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, keys_count, key, token, *args):
        assert keys_count == 1
        if self.values.get(key) != token:
            return 0
        if 'DEL' in script:
            del self.values[key]
            return 1
        return 1


@pytest.mark.asyncio
async def test_redis_lease_does_not_delete_or_renew_new_owner():
    fake = FakeRedis()
    lease = await RedisLease.acquire(fake, 'payment:abc', ttl=30)
    assert lease is not None
    assert await RedisLease.acquire(fake, 'payment:abc', ttl=30) is None
    fake.values['payment:abc'] = 'other-worker-token'
    await lease.release()
    assert fake.values['payment:abc'] == 'other-worker-token'


@pytest.mark.asyncio
async def test_redis_lease_release_of_owner_removes_only_own_key():
    fake = FakeRedis()
    lease = await RedisLease.acquire(fake, 'payment:def', ttl=30)
    assert lease is not None
    await lease.release()
    assert 'payment:def' not in fake.values
