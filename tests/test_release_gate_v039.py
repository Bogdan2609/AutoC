"""Release gate tests are offline and must never touch money, suppliers or DB."""
import json
import subprocess
import sys
from pathlib import Path

from scripts.release_gate_v039 import (
    DANGEROUS_FLAGS, RELEASE, enabled, env_file, inspect, migrations,
)

ROOT = Path(__file__).resolve().parents[1]


def test_current_migration_graph_is_one_linear_head():
    report = migrations(ROOT / 'migrations' / 'versions')
    assert report['head'] == '20261026_0037'
    assert report['revisions'] >= 37


def test_release_gate_reports_truthful_no_go_without_live_evidence():
    flags = {key: 'false' for key in DANGEROUS_FLAGS}
    report = inspect(ROOT, {**flags, 'APP_ENV': 'development'})
    assert report['release'] == RELEASE
    assert report['static_status'] == 'PASS'
    assert report['live_money_decision'] == 'NO_GO'
    assert all(check['status'] == 'NOT_TESTED' for check in report['checks'] if check['name'] in (
        'supplier_contracts', 'payment_sandbox', 'postgres_races', 'db_migration_applied'))


def test_each_dangerous_flag_blocks_preflight():
    flags = {key: 'false' for key in DANGEROUS_FLAGS}
    for name in DANGEROUS_FLAGS:
        report = inspect(ROOT, {**flags, name: 'true', 'APP_ENV': 'development'})
        assert report['static_status'] == 'BLOCK'
        check = next(x for x in report['checks'] if x['name'] == 'external_write_switches')
        assert name in check['detail']


def test_unknown_flag_does_not_get_treated_as_false():
    assert enabled('PAYMENTS_RECONCILIATION_ENABLED', {'PAYMENTS_RECONCILIATION_ENABLED': 'sometimes'}) is None
    flags = {key: 'false' for key in DANGEROUS_FLAGS}
    report = inspect(ROOT, {**flags, 'REAL_SUPPLIER_CHECKOUT_ENABLED': 'unexpected'})
    assert report['static_status'] == 'BLOCK'


def test_prod_security_baseline_is_fail_closed():
    flags = {key: 'false' for key in DANGEROUS_FLAGS}
    broken = inspect(ROOT, {**flags, 'APP_ENV':'production', 'PUBLIC_BASE_URL': 'http://localhost:8000'})
    assert broken['static_status'] == 'BLOCK'
    good = inspect(ROOT, {**flags, 'APP_ENV':'production',
         'PUBLIC_BASE_URL':'https://shop.example.invalid',
         'ADMIN_API_TOKEN':'x'*32, 'POSTGRES_PASSWORD':'y'*32,
         'BROWSER_CSRF_SECRET':'z'*32, 'ADMIN_LEGACY_TOKEN_ENABLED': 'false'})
    assert good['static_status'] == 'PASS'
    assert good['live_money_decision'] == 'NO_GO'


def test_env_file_parser_does_not_evaluate_values_or_print_secrets(tmp_path):
    secret = 'a_private_password_12345'
    config_file = tmp_path / '.env'
    config_file.write_text(f'APP_ENV="production"\nPOSTGRES_PASSWORD={secret}\nINVALID KEY=oops\n', encoding='utf8')
    assert env_file(config_file) == {'APP_ENV': 'production', 'POSTGRES_PASSWORD': secret}
    report = inspect(ROOT, {**{key:'false' for key in DANGEROUS_FLAGS},
         'APP_ENV':'production', 'POSTGRES_PASSWORD':secret})
    assert secret not in json.dumps(report)


def test_migration_rejects_duplicate_or_missing_parent(tmp_path):
    path = tmp_path/'versions'; path.mkdir()
    (path/'a.py').write_text("revision='one'\ndown_revision=None\n")
    (path/'b.py').write_text("revision='one'\ndown_revision='ghost'\n")
    import pytest
    with pytest.raises(ValueError, match='Duplicate'):
        migrations(path)
    (path/'b.py').write_text("revision='two'\ndown_revision='ghost'\n")
    with pytest.raises(ValueError, match='Missing Alembic'):
        migrations(path)


def test_cli_is_read_only_and_does_not_include_credentials(tmp_path):
    secret = 'private-super-secret-credential'
    (tmp_path / '.env').write_text('POSTGRES_PASSWORD='+secret+'\n')
    result = subprocess.run([sys.executable, str(ROOT/'scripts/release_gate_v039.py'),
                             '--root',str(tmp_path), '--json'], capture_output=True,text=True,check=False)
    data = json.loads(result.stdout)
    assert result.returncode == 2
    assert data['live_money_decision'] == 'NO_GO'
    assert secret not in result.stdout


def test_legacy_admin_exports_present_without_typescript_dependency():
    report = inspect(ROOT, {**{key:'false' for key in DANGEROUS_FLAGS}, 'APP_ENV':'development'})
    for key in ('staff_page_exports', 'staff_app_import'):
        assert next(check['status'] for check in report['checks'] if check['name'] == key) == 'PASS'


def test_unknown_app_env_blocks_bypass_of_production_rules():
    report = inspect(ROOT, {**{key:'false' for key in DANGEROUS_FLAGS},'APP_ENV':'prod'})
    assert report['static_status'] == 'BLOCK'
    assert 'APP_ENV' in next(check['detail'] for check in report['checks']
                             if check['name']=='production_configuration')


def test_staff_only_production_does_not_require_legacy_token():
    flags = {key: 'false' for key in DANGEROUS_FLAGS}
    result = inspect(ROOT, {**flags, 'APP_ENV':'production', 'PUBLIC_BASE_URL':'https://shop.example.invalid',
        'ADMIN_API_TOKEN':'', 'POSTGRES_PASSWORD':'a'*40, 'BROWSER_CSRF_SECRET':'b'*40,
        'ADMIN_LEGACY_TOKEN_ENABLED':'false'})
    assert result['static_status']=='PASS'
    assert result['live_money_decision']=='NO_GO'
