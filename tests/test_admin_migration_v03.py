"""Static / route regressions for the partial /operator -> /admin migration.
No database, API credentials, or supplier orders are exercised.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'admin-ui'/'src'


def test_old_and_new_admin_routes_coexist():
    from app.main import app
    paths={getattr(r,'path','') for r in app.routes}
    assert '/operator' in paths
    assert '/admin' in paths


def test_v03_workspaces_have_registered_routes():
    app=(UI/'App.tsx').read_text(encoding='utf8')
    for path in ('/catalog/manage','/catalog/templates','/catalog/automation',
                 '/suppliers/manage','/suppliers/feeds','/warehouses',
                 '/marketplace','/finance','/analytics','/system'):
        assert f'path="{path}"' in app


def test_admin_workflows_do_not_directly_trigger_payment_or_purchase():
    sections=(UI/'pages'/'Section.tsx').read_text(encoding='utf8')
    forms=(UI/'features'/'entity-config.ts').read_text(encoding='utf8')
    for value in ('/checkout','/orders/supplier','/payments/execute','/procurement/create'):
        assert value not in sections
        assert value not in forms
    assert 'supplier-destinations' in forms
    assert 'receiving_contact_name' in forms


def test_specialized_catalog_flows_use_admin_endpoints():
    files={x.name:x.read_text(encoding='utf8') for x in (UI/'pages').glob('*.tsx')}
    assert '/api/v1/backoffice/catalog-v021' in files['CategoryTemplates.tsx']
    assert '/api/v1/admin/draft-materialization/automation' in files['CatalogAutomation.tsx']
    assert 'attribute-values' in files['ProductTaxonomy.tsx']
    forms=(UI/'features'/'entity-config.ts').read_text(encoding='utf8')
    assert "create:'/api/v1/backoffice/master-catalog/families'" in forms
    assert "create:'/api/v1/master-catalog/families'" not in forms
