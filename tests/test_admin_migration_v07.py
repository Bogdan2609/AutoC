"""Offline safeguards for React admin v0.7 candidate. No DB, supplier calls or money movement."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'admin-ui'/'src'


def read(path):return (SRC/path).read_text(encoding='utf-8')


def test_all_new_routes_are_registered():
    app=read('App.tsx')
    for path in ['/catalog/brands','/catalog/brands/:brandId','/catalog/families',
                 '/suppliers/control','/finance/studio','/system/audit']:
        assert f'path="{path}"' in app
    assert 'path="/suppliers/control"' in app
    assert 'path="/suppliers/:code"' in app
    assert app.index('path="/suppliers/control"')<app.index('path="/suppliers/:code"')


def test_media_moderation_is_explicit_and_uses_admin_api():
    cat=read('pages/CatalogStudio.tsx')
    assert 'FormData()' in cat
    assert "kind==='document'?16:10" in cat
    assert '/media' in cat
    assert 'status=approved' in cat
    assert 'window.confirm' in cat


def test_purchase_and_bank_real_actions_are_not_exposed():
    combined='\n'.join(p.read_text(encoding='utf-8') for p in (SRC/'pages').glob('*.tsx'))
    for forbidden in ['/prepare-purchase','/purchase/confirm','/checkout/execute',
                      '/payments/live-charge','/refunds/execute']:
        assert forbidden not in combined
    assert 'propose-replacement' in read('pages/OrderCare.tsx')


def test_finance_forms_match_required_f2_f4_f5_names():
    finance=read('pages/FinanceStudio.tsx')
    for field in ['operation_date','source_gross','sim_order_ref','event_key',
                  'new_status','book_ref','period_start','period_end']:
        assert "key:'"+field+"'" in finance
    assert 'MOCK_SBP' in finance
    assert 'SHADOW' in finance


def test_supplier_route_is_read_only_and_courier_only():
    suppliers=read('pages/SupplierControl.tsx')
    assert 'checkout-details' in suppliers
    assert 'самовывоз' in suppliers
    assert 'warehouses/map' in suppliers
    assert 'brands/map' in suppliers
    assert 'window.confirm' in suppliers


def test_parity_gaps_are_transparent():
    audit=read('pages/MigrationAudit.tsx')
    assert 'X-Admin-Token' in audit
    assert 'E2E' in audit
    assert "status:'blocked'" in audit
    assert '/operator' in audit


def test_backend_paths_present_in_original_project():
    # File-level contract smoke check. Dynamic permissions/data must still be tested on user's running app.
    refs=[('app/presentation/operations.py','/lines/{line_id}/propose-replacement'),
          ('app/presentation/catalog_editor.py','/products/{product_id}/change-history'),
          ('app/modules/catalog_ext/media.py','/media/{media_id}'),
          ('app/modules/finance/payment_sim_api.py','/payments/{payment_id}/mock-events'),
          ('app/modules/supplier_feeds/api.py','/warehouses/map')]
    for file,fragment in refs:
        assert fragment in (ROOT/file).read_text(encoding='utf-8')
