"""Static release checks, no external services."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def text(path):return (ROOT/path).read_text(encoding='utf-8')

def test_theme_button_and_scope_gate_exist():
    app=text('admin-ui/src/App.tsx')
    assert 'function ThemeButton()' in app
    assert 'function ScopeGate(' in app
    assert 'path="/system/sessions"' in app
    assert 'canShowNavigation' in app

def test_client_does_not_have_independent_roles_matrix():
    api=text('admin-ui/src/lib/api.ts')
    assert 'scopeGrants=result.permissions' in api
    assert 'scopeRules=result.scope_rules' in api
    assert 'canAccessPath(path,mutation)' in api
    assert "operator:['/','/orders'" not in text('admin-ui/src/App.tsx')

def test_sessions_isolated_from_customer_table():
    model=text('app/modules/admin_staff/models.py')
    assert 'class AdminLoginSession' in model
    security=text('app/core/security.py')
    assert 'select(AdminLoginSession)' in security
    assert 'select(Session)' not in security
    assert 'admin_login_sessions' in text('migrations/versions/20261023_0035_admin_login_sessions.py')

def test_browser_test_does_not_call_live_payments_or_procurement():
    e2e=text('admin-ui/e2e/admin.spec.ts')
    assert "page.route('**/api/v1/**'" in e2e
    assert "'/api/v1/payments'" not in e2e
    assert "'/api/v1/admin/supplier-purchases'" not in e2e
