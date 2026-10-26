"""v0.40.1: regressions for FastAPI >=0.137 nested route trees.

No bank, supplier, database or internet operations.
"""
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import iter_route_contexts

from app.core.security import require_admin
from app.modules.admin_staff.route_audit import route_audit


def test_fastapi_included_route_tree_carries_effective_prefixes():
    internal = APIRouter()
    @internal.get('/audit')
    async def read_audit():
        return {'ok': True}
    app = FastAPI()
    app.include_router(internal, prefix='/api/v1/admin/staff', dependencies=[Depends(require_admin)])
    routes = list(iter_route_contexts(app.routes))
    assert any(r.path == '/api/v1/admin/staff/audit' for r in routes)
    report = route_audit(app)
    assert report['policy_complete'], report
    assert report['protected_routes'] == 1
    assert report['inspected_api_routes'] >= 1


def test_fastapi_nested_route_audit_cannot_silently_pass_unprotected_write():
    nested = APIRouter()
    @nested.post('/secret')
    async def write_secret():
        return {'ok': True}
    middle = APIRouter()
    middle.include_router(nested, prefix='/not-classified')
    app = FastAPI()
    app.include_router(middle, prefix='/api/v1/admin')
    report = route_audit(app)
    assert not report['policy_complete']
    assert 'POST /api/v1/admin/not-classified/secret' in report['unclassified_admin_routes']


def test_fastapi_route_audit_detects_same_method_registered_twice():
    app = FastAPI()
    @app.get('/api/v1/admin/staff/audit', dependencies=[Depends(require_admin)])
    async def first():
        return {'one': True}
    @app.get('/api/v1/admin/staff/audit', dependencies=[Depends(require_admin)])
    async def second():
        return {'two': True}
    report = route_audit(app)
    assert not report['policy_complete']
    assert 'GET /api/v1/admin/staff/audit' in report['duplicate_method_paths']
