"""Allow-list policy for employee API. Denies new unclassified paths by default.

The route prefix is not enough for mixed workspace endpoints; classify them
before checking the generic prefix. Do not infer access from frontend menus.
"""

ROLES = ('owner', 'operator', 'catalog_manager', 'supplier_manager', 'finance_manager', 'analyst', 'viewer')
# Scopes are ordered: narrowest URL prefixes before wider ones.
_SCOPES = (
    ('/api/v1/admin/staff/sessions', 'sessions'),
    ('/api/v1/admin/staff', 'staff'),
    ('/api/v1/admin/supplier-purchases', 'purchases'),
    ('/api/v1/admin/commerce', 'orders'),
    ('/api/v1/admin/sales', 'orders'),
    ('/api/v1/admin/marketplace/order-requests', 'orders'),
    ('/api/v1/admin/marketplace', 'marketplace'),
    ('/api/v1/admin/supplier-feeds', 'suppliers'),
    ('/api/v1/admin/draft-materialization', 'catalog'),
    ('/api/v1/admin/catalog-bulk', 'catalog'),
    ('/api/v1/admin/catalog-enrichment', 'catalog'),
    ('/api/v1/admin/multi-enrichment', 'catalog'),
    ('/api/v1/payments/admin', 'payments_admin'),
    ('/api/v1/offer-cache/products', 'suppliers'),
    ('/api/v1/backoffice/brands', 'catalog'),
    ('/api/v1/backoffice/products', 'catalog'),
    ('/api/v1/backoffice/offer-snapshots', 'suppliers'),
    ('/api/v1/backoffice/supplier-items', 'suppliers'),
    ('/api/v1/backoffice/suppliers', 'suppliers'),
    ('/api/v1/backoffice/price-policy', 'finance'),
    ('/api/v1/backoffice/workspace/accounts', 'suppliers'),
    ('/api/v1/backoffice/workspace/destinations', 'suppliers'),
    ('/api/v1/backoffice/workspace/used', 'marketplace'),
    ('/api/v1/backoffice/workspace/supplier-accounts', 'suppliers'),
    ('/api/v1/backoffice/workspace/supplier-destinations', 'suppliers'),
    ('/api/v1/backoffice/workspace/suppliers', 'suppliers'),
    ('/api/v1/backoffice/workspace/orders', 'orders'),
    ('/api/v1/backoffice/workspace/catalog', 'catalog'),
    ('/api/v1/backoffice/workspace/brands', 'catalog'),
    ('/api/v1/backoffice/workspace/merchants', 'marketplace'),
    ('/api/v1/backoffice/workspace/stores', 'marketplace'),
    ('/api/v1/backoffice/workspace/offers', 'marketplace'),
    ('/api/v1/backoffice/workspace/used-parts', 'marketplace'),
    ('/api/v1/backoffice/workspace/nodes', 'warehouses'),
    ('/api/v1/backoffice/workspace/inventory', 'warehouses'),
    ('/api/v1/backoffice/workspace/routes', 'warehouses'),
    ('/api/v1/backoffice/workspace/summary', 'analytics'),
    ('/api/v1/backoffice/catalog-editor', 'catalog'),
    ('/api/v1/backoffice/master-catalog', 'catalog'),
    ('/api/v1/backoffice/catalog-v021', 'catalog'),
    ('/api/v1/backoffice/finance', 'finance'),
    ('/api/v1/backoffice/analytics', 'analytics'),
    ('/api/v1/backoffice/monitor/attention', 'analytics'),
    ('/api/v1/backoffice/monitor/configuration', 'analytics'),
    ('/api/v1/backoffice/monitor/receiving-readiness', 'analytics'),
    ('/api/v1/backoffice/monitor', 'system'),
    ('/api/v1/backoffice/offer-engine', 'suppliers'),
    ('/api/v1/backoffice/operations', 'operations'),
)
# All listed role scopes include read access; write is a separate grant.
READ = {
    'owner': {'*'},
    'operator': {'sessions','analytics','orders','purchases','catalog','marketplace','warehouses','suppliers','system','operations'},
    'catalog_manager': {'sessions','catalog','analytics','suppliers'},
    'supplier_manager': {'sessions','suppliers','catalog','warehouses','analytics'},
    'finance_manager': {'sessions','finance','orders','analytics'},
    'analyst': {'sessions','analytics','catalog','suppliers','marketplace','warehouses','system'},
    'viewer': {'sessions','analytics','catalog','marketplace','warehouses'},
}
WRITE = {
    'owner': {'*'},
    'operator': {'sessions','orders','marketplace','warehouses'},
    'catalog_manager': {'sessions','catalog'},
    'supplier_manager': {'sessions','suppliers'},
    'finance_manager': {'sessions','finance'},
    'analyst': {'sessions'},
    'viewer': {'sessions'},
}


def resource_scope(path: str) -> str | None:
    for prefix, scope in _SCOPES:
        if path == prefix or path.startswith(prefix + '/'):
            return scope
    return None


def role_allowed(role: str, scope: str, method: str) -> bool:
    allowed = READ if method in ('GET','HEAD','OPTIONS') else WRITE
    return role in allowed and ('*' in allowed[role] or scope in allowed[role])


def role_capabilities(role: str) -> dict[str, dict[str, bool]]:
    """Server-authoritative, serializable grants for the UI. API still enforces RBAC."""
    scopes = sorted({scope for _, scope in _SCOPES})
    return {scope: {'read': role_allowed(role, scope, 'GET'),
                    'write': role_allowed(role, scope, 'POST')} for scope in scopes}
