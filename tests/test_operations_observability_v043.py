"""v0.43 contract regression: no bank or supplier network."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from app.modules.accounts.models import utc
from app.workers.job_lease_v043 import (
    classify_job, lease_duration, heartbeat_once, still_owner, REPLAYABLE,
    EXTERNAL_WRITE_JOBS,
)
from app.presentation.operations_health_v043 import safe_job
from app.presentation.sbp_result_v043 import interpret, buyer_result
from app.main import app


def test_job_kinds_split_manual_from_failure():
    assert classify_job('procurement','manual_review')['severity']=='action_required'
    assert classify_job('search','failed')['severity']=='error'
    assert classify_job('search','pending')['retry_safe'] is True
    assert classify_job('refund','pending')['retry_safe'] is False
    assert classify_job('search','done')['severity']=='normal'


def test_no_external_write_is_replayable():
    assert REPLAYABLE.isdisjoint(EXTERNAL_WRITE_JOBS)
    assert 'feed_catalog_sync' not in REPLAYABLE
    assert lease_duration('catalog_bulk') == timedelta(hours=2)
    assert lease_duration('search') == timedelta(minutes=5)


def test_job_logging_redacts_arbitrary_errors_and_payload():
    row = SimpleNamespace(id=uuid4(),kind='search',status='manual_review',attempts=2,
                          due_at=utc(),last_error='secret=provider-api-key')
    result = safe_job(row)
    assert result['error_type'] is None
    assert 'payload' not in result
    assert result['external_write_retry_allowed'] is False


def test_sbp_result_does_not_assume_success_from_redirect():
    assert interpret('pending')[0] == 'pending'
    assert interpret('reconciliation_required')[0] == 'pending'
    assert interpret('succeeded')[0] == 'paid'
    assert interpret(None)[0] == 'unknown'


@pytest.mark.asyncio
async def test_sbp_buyer_access_is_tenant_isolated():
    from fastapi import HTTPException
    class Session:
        async def get(self, _, __):
            return SimpleNamespace(user_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        await buyer_result(Session(),uuid4(),uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_heartbeat_scopes_generation_with_conditional_sql():
    class Session:
        def __init__(self):self.statement=None;self.committed=False
        async def execute(self,statement):
            self.statement=statement
            return SimpleNamespace(rowcount=1)
        async def commit(self):self.committed=True
    ses=Session()
    class Ctx:
        async def __aenter__(self):return ses
        async def __aexit__(self,*_):return False
    ok=await heartbeat_once(lambda:Ctx(),uuid4(),2,'search')
    assert ok and ses.committed
    sql=str(ses.statement).lower()
    assert 'jobs.attempts' in sql and 'jobs.status' in sql and 'jobs.due_at' in sql
    assert 'update jobs' in sql


@pytest.mark.asyncio
async def test_heartbeat_rejects_stale_generation():
    class Session:
        async def execute(self,query):return SimpleNamespace(rowcount=0)
        async def commit(self):pass
    class Ctx:
        async def __aenter__(self):return Session()
        async def __aexit__(self,*_):return False
    assert await heartbeat_once(lambda:Ctx(),uuid4(),999,'search') is False


@pytest.mark.asyncio
async def test_final_job_fence_requires_running_owner():
    class Session:
        no_autoflush=nullcontext()
        async def scalar(self,stmt):
            self.stmt=stmt
            return None
    session=Session()
    assert await still_owner(session,uuid4(),4) is False
    from sqlalchemy.dialects.postgresql import dialect
    assert 'FOR UPDATE' in str(session.stmt.compile(dialect=dialect()))


def test_admin_routes_have_guards_from_nested_router():
    routes=[x for x in iter_route_contexts(app.routes)
            if isinstance(x.original_route,APIRoute) and
            x.path.startswith('/api/v1/backoffice/operations-health/')]
    assert len(routes)==6
    from app.core.security import require_admin
    for x in routes:
        assert any(d.call is require_admin for d in x.dependant.dependencies)


@pytest.mark.asyncio
async def test_health_endpoints_not_public(client):
    for path in ('summary','jobs','payments','refunds','purchases','supplier-factory'):
        r=await client.get('/api/v1/backoffice/operations-health/'+path)
        assert r.status_code in (401,403)


@pytest.mark.asyncio
async def test_sbp_result_requires_login(client):
    path='/api/v1/orders/'+str(uuid4())+'/sbp-result'
    resp=await client.get(path)
    assert resp.status_code==401
    page=await client.get('/sbp/result/'+str(uuid4()))
    assert page.status_code==200
    assert 'Cache-Control' in page.headers
    assert 'не подтверждает' in page.text
    assert 'succeeded' not in page.text
