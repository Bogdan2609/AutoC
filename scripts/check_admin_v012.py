"""Read-only v0.12 acceptance gate for route security and UI wiring.

Run inside the web container. This NEVER invokes HTTP routes, touches the DB,
or dispatches supplier requests or payments. Exit non-zero on regressions.
It is not a substitute for browser E2E or old/new functional parity review.
"""
import json
import sys
from pathlib import Path

from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app
from app.modules.admin_staff.policy import resource_scope, role_allowed
from app.modules.admin_staff.route_audit import route_audit

REQUIRED = {
    '/api/v1/backoffice/monitor/purchases/{attempt_id}': {'GET'},
    '/api/v1/backoffice/monitor/purchases': {'GET'},
    '/api/v1/backoffice/monitor/orders/{order_id}/events': {'GET'},
    '/api/v1/backoffice/operations/orders/{order_id}': {'GET'},
    '/api/v1/backoffice/operations/lines/{line_id}/propose-replacement': {'POST'},
}


def acceptance_inventory():
    permissions = route_audit(app)
    methods = {r.path: r.methods or set() for r in app.routes if isinstance(r, APIRoute)}
    failures = []
    if not permissions['policy_complete']:
        failures.append('Unprotected or unclassified administrative API routes')
    for path, expected in REQUIRED.items():
        actual = set(methods.get(path, set()))
        if not expected.issubset(actual):
            failures.append(f'Missing method on {path}: {sorted(expected - actual)}')
    if resource_scope('/api/v1/backoffice/operations/lines/00000000-0000-4000-8000-000000000001/propose-replacement') != 'orders':
        failures.append('Replacement proposal is not classified as an orders operation')
    if not role_allowed('operator', 'orders', 'POST'):
        failures.append('Order operator cannot submit a replacement proposal')
    if role_allowed('viewer', 'orders', 'POST'):
        failures.append('Viewer must not mutate orders')
    if role_allowed('finance_manager', 'orders', 'POST'):
        failures.append('Finance manager must not mutate orders')
    if resource_scope('/api/v1/backoffice/monitor/purchases/00000000-0000-4000-8000-000000000002') != 'purchases':
        failures.append('Purchase diagnostics lack a restricted scope')
    react = {
        'admin-ui/src/features/PurchaseAttemptDetail.tsx': ('canAccessPath', 'manual_reconciliation_required'),
        'admin-ui/src/pages/OrderPurchases.tsx': ('PurchaseAttemptInspector', 'expectedOrderId'),
        'admin-ui/src/pages/Orders.tsx': ('PurchaseAttemptInspector', 'tab=','purchases'),
        'admin-ui/src/pages/MigrationAudit.tsx': ('functional_parity_verified',),
    }
    for rel, tokens in react.items():
        file = ROOT / rel
        content = file.read_text(encoding='utf-8') if file.is_file() else ''
        if any(token not in content for token in tokens):
            failures.append(f'UI integration incomplete: {rel}')
    return {
        'version': '0.12.0',
        'passed': not failures,
        'route_policy_complete': permissions['policy_complete'],
        'classified_routes': permissions['classified_routes'],
        'protected_routes': permissions['protected_routes'],
        'critical_api_methods_present': all(set(expected).issubset(set(methods.get(path,set()))) for path,expected in REQUIRED.items()),
        'functional_parity_verified': False,
        'browser_e2e_executed': False,
        'real_supplier_or_payment_operations_executed': False,
        'failures': failures,
        'next_steps': ['docker build with tsc and vite', 'Playwright with mocked external APIs',
                       'isolated PostgreSQL workflow acceptance', 'manual comparison against /operator'],
    }


if __name__ == '__main__':
    report = acceptance_inventory()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['passed'] else 1)
