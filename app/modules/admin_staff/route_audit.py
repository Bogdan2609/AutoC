"""Structural RBAC coverage audit for the registered FastAPI route table.

This reports *configuration coverage*, not authorization correctness or UI
functional parity. Login/session/logout are intentionally self-authenticated.
It never makes HTTP requests, changes grants or exposes credentials.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.core.security import require_admin
from app.modules.admin_staff.policy import resource_scope

_SELF_AUTHENTICATING = frozenset({
    ('/api/v1/admin/staff/login', 'POST'),
    ('/api/v1/admin/staff/session', 'GET'),
    ('/api/v1/admin/staff/logout', 'POST'),
})
_PROTECTED_PREFIXES = (
    '/api/v1/admin/', '/api/v1/backoffice/', '/api/v1/payments/admin/',
)


def _depends_on(node, target, *, seen=None) -> bool:
    """Find an actual dependency, including router-level nested Depends."""
    seen = set() if seen is None else seen
    if id(node) in seen:
        return False
    seen.add(id(node))
    if getattr(node, 'call', None) is target:
        return True
    return any(_depends_on(dep, target, seen=seen)
               for dep in getattr(node, 'dependencies', ()))


def route_audit(app: FastAPI) -> dict:
    """Audit missing scope/guard on admin routes and duplicate registrations.

    `policy_complete` must never be used as evidence of validated UX parity,
    login strength, CSRF effectiveness, or role-grant correctness.
    """
    uncovered: list[str] = []
    unguarded: list[str] = []
    duplicates: list[str] = []
    registered: set[tuple[str, str]] = set()
    protected_routes = 0

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path
        if resource_scope(path):
            protected_routes += 1
        for method in sorted(route.methods or ()):
            if method in ('HEAD', 'OPTIONS'):
                continue
            key = (path, method)
            if key in registered:
                duplicates.append(f'{method} {path}')
            registered.add(key)
            classified = resource_scope(path)
            is_admin = path.startswith(_PROTECTED_PREFIXES)
            if is_admin and not classified:
                uncovered.append(f'{method} {path}')
            if classified:
                if (key not in _SELF_AUTHENTICATING and
                        not _depends_on(route.dependant, require_admin)):
                    unguarded.append(f'{method} {path}')

    return {
        'policy_complete': not (uncovered or unguarded or duplicates),
        'protected_routes': protected_routes,
        'unclassified_admin_routes': sorted(uncovered),
        'unguarded_classified_routes': sorted(unguarded),
        'duplicate_method_paths': sorted(duplicates),
        'functional_parity_verified': False,
    }
