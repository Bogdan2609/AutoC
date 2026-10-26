"""Staff session, CSRF, RBAC, revocation and legacy switch integration tests.

Uses isolated SQLite only. Never calls supplier or payment providers.
"""
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SyncSession, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.db import get_session
from app.modules.accounts.models import Session, User
from app.modules.admin_staff import api as staff_api
from app.modules.admin_staff.models import AdminAuditLog, AdminStaff, AdminLoginSession
from app.modules.admin_staff.policy import resource_scope, role_allowed


@pytest.fixture
def security_settings(monkeypatch):
    obj = SimpleNamespace(admin_legacy_token_enabled=True, admin_api_token='test-only-owner-secret')
    monkeypatch.setattr(security, 'get_settings', lambda: obj)
    return obj


def test_role_map():
    assert resource_scope('/api/v1/admin/supplier-purchases/42') == 'purchases'
    assert resource_scope('/api/v1/backoffice/finance/ledger/overview') == 'finance'
    assert resource_scope('/api/v1/backoffice/workspace/accounts') == 'suppliers'
    assert resource_scope('/api/v1/payments/admin/refunds/42/reconcile') == 'payments_admin'
    assert resource_scope('/api/v1/admin/unknown-secret/new') is None
    assert role_allowed('catalog_manager', 'catalog', 'PATCH')
    assert not role_allowed('catalog_manager', 'purchases', 'POST')
    assert not role_allowed('supplier_manager', 'finance', 'POST')
    assert not role_allowed('viewer', 'orders', 'GET')
    assert role_allowed('owner', 'payments_admin', 'POST')
    assert role_allowed('owner', 'staff', 'POST')


@pytest.mark.asyncio
async def test_staff_lifecycle_rbac_csrf_audit(monkeypatch, security_settings):
    engine = create_engine('sqlite+pysqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    User.metadata.create_all(engine, tables=[User.__table__, Session.__table__,
                                          AdminStaff.__table__, AdminAuditLog.__table__, AdminLoginSession.__table__])
    sync_factory = sessionmaker(engine, expire_on_commit=False)

    class AsyncSqliteTestAdapter:
        # A tiny async shim over a real isolated sqlite DB; only used in tests.
        # Avoids requiring an extra async SQLite driver on the development host.
        def __init__(self):self.db=sync_factory()
        async def __aenter__(self):return self
        async def __aexit__(self,*args):self.db.close()
        async def scalar(self,stmt):return self.db.scalar(stmt)
        async def scalars(self,stmt):return self.db.scalars(stmt)
        async def execute(self,stmt):return self.db.execute(stmt)
        async def get(self,model,key,**kwargs):return self.db.get(model,key,**kwargs)
        async def flush(self):self.db.flush()
        async def refresh(self,item):self.db.refresh(item)
        async def commit(self):self.db.commit()
        async def rollback(self):self.db.rollback()
        def add(self,obj):self.db.add(obj)

    def factory():return AsyncSqliteTestAdapter()
    async def no_limit(*args, **kwargs):
        return None
    monkeypatch.setattr(staff_api, 'enforce_budget', no_limit)
    app = FastAPI()
    app.include_router(staff_api.router)
    probe = APIRouter(prefix='/api/v1/backoffice/catalog-editor', dependencies=[Depends(security.require_admin)])
    @probe.get('/probe')
    def read_catalog():
        return {'ok': True}
    @probe.post('/probe')
    def write_catalog():
        return {'ok': True}
    app.include_router(probe)
    purchase = APIRouter(prefix='/api/v1/admin/supplier-purchases', dependencies=[Depends(security.require_admin)])
    @purchase.post('/probe')
    def write_purchase():
        return {'danger': True}
    app.include_router(purchase)
    async def override_db():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_session] = override_db
    headers={'X-Admin-Token':'test-only-owner-secret'}
    transport=ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        owner = await client.post('/api/v1/admin/staff', headers=headers, json={
            'email':'owner@example.com','password':'this-is-a-very-long-owner-password',
            'display_name':'Owner','role':'owner'})
        assert owner.status_code == 201, owner.text
        login=await client.post('/api/v1/admin/staff/login', json={
            'email':'owner@example.com','password':'this-is-a-very-long-owner-password'})
        assert login.status_code == 200, login.text
        owner_csrf=login.json()['csrf']
        assert client.cookies.get('ap_admin_session')
        # Cookie is HttpOnly (JS cannot read it) and Strict same-site.
        assert 'httponly' in login.headers['set-cookie'].lower()
        assert 'samesite=strict' in login.headers['set-cookie'].lower()
        assert (await client.get('/api/v1/admin/staff/session')).status_code == 200
        assert (await client.get('/api/v1/backoffice/catalog-editor/probe')).status_code == 200
        assert (await client.post('/api/v1/backoffice/catalog-editor/probe')).status_code == 403
        assert (await client.post('/api/v1/backoffice/catalog-editor/probe',headers={'X-Admin-CSRF':owner_csrf})).status_code == 200
        catalog=await client.post('/api/v1/admin/staff',headers={'X-Admin-CSRF':owner_csrf},json={
            'email':'catalog@example.com','password':'this-is-a-very-long-catalog-password',
            'display_name':'Catalog Manager','role':'catalog_manager'})
        assert catalog.status_code == 201, catalog.text
        catalog_id=catalog.json()['id']
        assert (await client.get('/api/v1/admin/staff/audit')).status_code == 200
        journal=(await client.get('/api/v1/admin/staff/audit')).json()
        assert journal and any(item['action']=='POST' for item in journal)
        # Independent browser cookie jar simulates second employee.
        async with AsyncClient(transport=transport,base_url='http://testserver') as user_client:
            login2=await user_client.post('/api/v1/admin/staff/login',json={
                'email':'catalog@example.com','password':'this-is-a-very-long-catalog-password'})
            assert login2.status_code == 200, login2.text
            token2=login2.json()['csrf']
            assert (await user_client.get('/api/v1/backoffice/catalog-editor/probe')).status_code == 200
            assert (await user_client.post('/api/v1/backoffice/catalog-editor/probe',headers={'X-Admin-CSRF':token2})).status_code == 200
            assert (await user_client.post('/api/v1/admin/supplier-purchases/probe',headers={'X-Admin-CSRF':token2})).status_code == 403
            assert (await user_client.get('/api/v1/admin/staff')).status_code == 403
            changed=await client.patch(f'/api/v1/admin/staff/{catalog_id}',headers={'X-Admin-CSRF':owner_csrf},json={'enabled':False})
            assert changed.status_code == 200, changed.text
            assert (await user_client.get('/api/v1/backoffice/catalog-editor/probe')).status_code == 401
        # Owner can't disable the only owner account.
        assert (await client.patch(f'/api/v1/admin/staff/{owner.json()["id"]}',headers={'X-Admin-CSRF':owner_csrf},json={'enabled':False})).status_code == 409
        # Legacy token cut-off actually takes effect server-side.
        security_settings.admin_legacy_token_enabled=False
        assert (await client.get('/api/v1/admin/staff', headers=headers)).status_code == 200  # owner cookie still active
        async with AsyncClient(transport=transport,base_url='http://testserver') as anonymous:
            assert (await anonymous.get('/api/v1/backoffice/catalog-editor/probe',headers=headers)).status_code == 403
        assert (await client.post('/api/v1/admin/staff/logout',headers={'X-Admin-CSRF':owner_csrf})).status_code == 200
        assert (await client.get('/api/v1/backoffice/catalog-editor/probe')).status_code == 403
    engine.dispose()


def test_every_existing_admin_route_is_mapped():
    from app.main import app
    from app.core.security import require_admin
    def protected(dependant):
        return any(dep.call is require_admin or protected(dep) for dep in dependant.dependencies)
    routes=[r for r in app.routes if hasattr(r, 'dependant')]
    guarded=[r for r in routes if protected(r.dependant)]
    assert len(guarded) >= 300
    assert all(resource_scope(r.path) is not None for r in guarded)
    # Prevent a future /api/v1/admin endpoint from accidentally being left public.
    exceptions={'/api/v1/admin/staff/login','/api/v1/admin/staff/session',
                '/api/v1/admin/staff/logout'}
    assert all(protected(r.dependant) or r.path in exceptions
               for r in routes if r.path.startswith('/api/v1/admin/'))
