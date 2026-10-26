"""Offline integration tests for isolated admin sessions, capabilities and revocation.

Only FastAPI + in-memory SQLite. No payment provider, supplier requests, or live DB.
"""
from types import SimpleNamespace
from datetime import timedelta
import hashlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.db import get_session
from app.modules.accounts.models import User, Session, utc
from app.modules.admin_staff import api as staff_api
from app.modules.admin_staff.models import AdminStaff, AdminAuditLog, AdminLoginSession
from app.modules.admin_staff.policy import resource_scope, role_capabilities


@pytest.mark.asyncio
async def test_admin_sessions_are_separate_and_revocable(monkeypatch):
    monkeypatch.setattr(security, 'get_settings', lambda: SimpleNamespace(
        admin_legacy_token_enabled=True, admin_api_token='v09-isolated-test-key'))
    async def no_budget(*args, **kwargs):
        pass
    monkeypatch.setattr(staff_api, 'enforce_budget', no_budget)
    engine = create_engine('sqlite+pysqlite:///:memory:',
                           connect_args={'check_same_thread': False}, poolclass=StaticPool)
    User.metadata.create_all(engine, tables=[User.__table__, Session.__table__,
                            AdminStaff.__table__, AdminAuditLog.__table__, AdminLoginSession.__table__])
    factory = sessionmaker(engine, expire_on_commit=False)

    class Adapter:
        def __init__(self): self.db = factory()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): self.db.close()
        async def scalar(self, stmt): return self.db.scalar(stmt)
        async def scalars(self, stmt): return self.db.scalars(stmt)
        async def execute(self, stmt): return self.db.execute(stmt)
        async def get(self, model, key, **kwargs): return self.db.get(model, key, **kwargs)
        async def flush(self): self.db.flush()
        async def refresh(self, obj): self.db.refresh(obj)
        async def commit(self): self.db.commit()
        async def rollback(self): self.db.rollback()
        def add(self, obj): self.db.add(obj)

    async def override():
        async with Adapter() as db:
            yield db

    app = FastAPI()
    app.include_router(staff_api.router)
    app.dependency_overrides[get_session] = override
    transport = ASGITransport(app=app)
    credentials = {'email': 'admin@example.com', 'password': 'secure-admin-test-only-password'}
    try:
        async with AsyncClient(transport=transport, base_url='http://testserver') as admin_a, \
                   AsyncClient(transport=transport, base_url='http://testserver') as admin_b:
            create = await admin_a.post('/api/v1/admin/staff',
                headers={'X-Admin-Token': 'v09-isolated-test-key'},
                json={**credentials, 'display_name': 'Owner', 'role': 'owner'})
            assert create.status_code == 201, create.text
            user_id = create.json()['user_id']
            # An ordinary consumer session MUST NOT be treated as an admin session.
            with factory() as db:
                from uuid import UUID
                db.add(Session(user_id=UUID(user_id), digest=hashlib.sha256(b'consumer').hexdigest(),
                               expires_at=utc() + timedelta(hours=2)))
                db.commit()
            la = await admin_a.post('/api/v1/admin/staff/login', json=credentials)
            lb = await admin_b.post('/api/v1/admin/staff/login', json=credentials)
            assert la.status_code == lb.status_code == 200
            assert la.json()['permissions']['staff']['write']
            assert la.json()['permissions']['sessions']['write']
            assert la.json()['scope_rules'][0]['scope'] == 'sessions'
            ca, cb = la.json()['csrf'], lb.json()['csrf']
            sessions = await admin_a.get('/api/v1/admin/staff/sessions')
            assert sessions.status_code == 200, sessions.text
            assert len(sessions.json()) == 2
            assert sum(item['current'] for item in sessions.json()) == 1
            assert (await admin_a.post('/api/v1/admin/staff/sessions/revoke-others')).status_code == 403
            revoke = await admin_a.post('/api/v1/admin/staff/sessions/revoke-others',
                                        headers={'X-Admin-CSRF': ca})
            assert revoke.status_code == 200 and revoke.json()['revoked'] == 1
            assert (await admin_a.get('/api/v1/admin/staff/session')).status_code == 200
            assert (await admin_b.get('/api/v1/admin/staff/session')).status_code == 401
            with factory() as db:
                from uuid import UUID
                assert db.scalar(select(Session).where(Session.user_id == UUID(user_id))) is not None
            # Current-session deletion invalidates its cookie.
            current_id = (await admin_a.get('/api/v1/admin/staff/sessions')).json()[0]['id']
            own_delete = await admin_a.delete('/api/v1/admin/staff/sessions/' + current_id,
                                               headers={'X-Admin-CSRF': ca})
            assert own_delete.status_code == 200 and own_delete.json()['current'] is True
            assert (await admin_a.get('/api/v1/admin/staff/session')).status_code == 401
            # A logged-out device cannot revoke any sessions using its stale CSRF token.
            assert (await admin_b.post('/api/v1/admin/staff/sessions/revoke-others',
                                       headers={'X-Admin-CSRF': cb})).status_code == 401
    finally:
        engine.dispose()


def test_role_capabilities_come_from_backend_rules():
    assert resource_scope('/api/v1/admin/staff/sessions/revoke-others') == 'sessions'
    assert resource_scope('/api/v1/admin/staff') == 'staff'
    assert role_capabilities('viewer')['sessions'] == {'read': True, 'write': True}
    assert role_capabilities('viewer')['purchases']['write'] is False
    assert role_capabilities('supplier_manager')['suppliers']['write'] is True
    assert role_capabilities('supplier_manager')['finance']['write'] is False
