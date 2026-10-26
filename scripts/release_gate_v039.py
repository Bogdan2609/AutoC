"""Read-only deployment gate for Autocodex 0.39.0.

This tool NEVER authenticates to suppliers/banks or writes to PostgreSQL.
A passing local check is NOT certification for production payments.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from http.client import HTTPConnection

RELEASE = '0.39.0'
EXPECTED_HEAD = '20261026_0037'
# These capabilities are deliberately NOT enabled by source update.
DANGEROUS_FLAGS = (
    'SUPPLIER_PROCUREMENT_ENABLED',
    'REAL_SUPPLIER_CHECKOUT_ENABLED',
    'PAYMENTS_RECONCILIATION_ENABLED',
    'MARKETPLACE_DEMO_MODE',
    'YOOKASSA_FAKE_ENABLED',
)
TRUE = frozenset(('true', '1', 'yes', 'on'))
FALSE = frozenset(('false', '0', 'no', 'off', ''))
REQUIRED_FILES = (
    'Dockerfile', 'compose.yaml', 'app/main.py',
    'app/modules/payments/api.py',
    'app/modules/payments/refund_reconciliation.py',
    'app/modules/marketplace/purchase_lifecycle.py',
    'app/modules/marketplace/purchase_operations.py',
    'app/modules/offer_engine/providers.py',
    'admin-ui/src/App.tsx',
    'admin-ui/src/pages/Staff.tsx',
)


def env_file(path: Path) -> dict[str, str]:
    """Read ordinary KEY=VALUE assignments; never print secrets."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def enabled(name: str, values: dict[str, str]) -> bool | None:
    value = values.get(name, '').strip().lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    return None


def migrations(path: Path) -> dict[str, object]:
    """Parse revision graph without touching a database or importing migrations."""
    revisions: dict[str, tuple[str, ...]] = {}
    for file in sorted(path.glob('*.py')):
        if file.name.startswith('__'):
            continue
        module = ast.parse(file.read_text(encoding='utf-8'), filename=str(file))
        fields: dict[str, object] = {}
        for node in module.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ('revision', 'down_revision'):
                    fields[node.targets[0].id] = ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in ('revision', 'down_revision') and node.value is not None:
                    fields[node.target.id] = ast.literal_eval(node.value)
        revision = fields.get('revision')
        parents = fields.get('down_revision')
        if not isinstance(revision, str) or not revision:
            raise ValueError(f'Missing revision in {file.name}')
        if revision in revisions:
            raise ValueError(f'Duplicate Alembic revision: {revision}')
        if isinstance(parents, str):
            ancestors = (parents,)
        elif parents is None:
            ancestors = ()
        elif isinstance(parents, (tuple, list)) and all(isinstance(p, str) for p in parents):
            ancestors = tuple(parents)
        else:
            raise ValueError(f'Invalid Alembic parent in {file.name}')
        revisions[revision] = ancestors
    if not revisions:
        raise ValueError('No Alembic revisions')
    children = {parent for parents in revisions.values() for parent in parents}
    if missing := children - revisions.keys():
        raise ValueError(f'Missing Alembic parent(s): {len(missing)}')
    heads = sorted(revisions.keys() - children)
    roots = sorted(k for k, parents in revisions.items() if not parents)
    if len(heads) != 1 or len(roots) != 1:
        raise ValueError(f'Unexpected Alembic topology: {len(roots)} roots, {len(heads)} heads')
    seen: set[str] = set()
    current: str | None = heads[0]
    while current is not None:
        if current in seen:
            raise ValueError('Alembic revision cycle')
        seen.add(current)
        parents = revisions[current]
        if len(parents) > 1:
            raise ValueError('Merge migration requires a reviewed release gate')
        current = parents[0] if parents else None
    if len(seen) != len(revisions):
        raise ValueError('Disconnected Alembic revision(s)')
    return {'revisions': len(revisions), 'head': heads[0], 'root': roots[0]}


def inspect(root: Path, config: dict[str, str] | None = None, check_http: bool = False) -> dict[str, object]:
    root = root.resolve()
    checks: list[dict[str, str]] = []
    def add(name: str, status: str, explanation: str):
        checks.append({'name': name, 'status': status, 'detail': explanation})

    try:
        version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        add('version', 'PASS' if version == RELEASE else 'BLOCK', f'Expected {RELEASE}; found {version}')
    except (OSError, KeyError, ValueError) as exc:
        add('version', 'BLOCK', f'pyproject.toml not readable: {type(exc).__name__}')

    missing = [p for p in REQUIRED_FILES if not (root / p).is_file()]
    add('critical_source_files', 'BLOCK' if missing else 'PASS',
        'Missing: ' + ', '.join(missing) if missing else f'{len(REQUIRED_FILES)} required files found')

    try:
        graph = migrations(root / 'migrations' / 'versions')
        add('alembic_graph', 'PASS' if graph['head'] == EXPECTED_HEAD else 'BLOCK',
            f"{graph['revisions']} revisions; head {graph['head']}")
    except (OSError, SyntaxError, ValueError) as exc:
        add('alembic_graph', 'BLOCK', f'Graph invalid: {exc}')

    try:
        app_ts = (root / 'admin-ui/src/App.tsx').read_text(encoding='utf-8')
        staff_ts = (root / 'admin-ui/src/pages/Staff.tsx').read_text(encoding='utf-8')
        required_staff = ('Staff', 'MySessions', 'MySecurity')
        missing_exports = [name for name in required_staff if f'export function {name}(' not in staff_ts]
        add('staff_page_exports', 'BLOCK' if missing_exports else 'PASS',
            'Missing: ' + ', '.join(missing_exports) if missing_exports else 'Staff, MySessions, MySecurity present (static check, not TS build)')
        if not all(name in app_ts for name in required_staff):
            add('staff_app_import', 'BLOCK', 'App.tsx missing expected staff page imports')
        else:
            add('staff_app_import', 'PASS', 'App.tsx includes staff page names')
    except OSError as exc:
        add('staff_page_exports', 'BLOCK', f'Cannot read frontend source: {type(exc).__name__}')
    try:
        for file in (root / 'app').rglob('*.py'):
            compile(file.read_bytes(), str(file), 'exec')
        add('python_syntax', 'PASS', 'Application Python files compile without importing or executing')
    except (OSError, SyntaxError) as exc:
        add('python_syntax', 'BLOCK', f'{type(exc).__name__}: {str(exc)[:160]}')

    caller_supplied_config = config is not None
    if config is None:
        config = env_file(root / '.env')
        # The .env is merged with inherited values in Docker Compose. Values are not disclosed.
        config = {**config, **{k: v for k, v in os.environ.items() if k in DANGEROUS_FLAGS or k in (
            'APP_ENV', 'ADMIN_API_TOKEN', 'ADMIN_LEGACY_TOKEN_ENABLED', 'BROWSER_CSRF_SECRET',
            'POSTGRES_PASSWORD', 'PUBLIC_BASE_URL')}}
    from_process = bool(os.environ.get('DATABASE_URL') and os.environ.get('REDIS_URL'))
    if not (root / '.env').is_file() and not caller_supplied_config and not from_process:
        add('environment', 'BLOCK', '.env missing; Docker Compose env_file requires it')
    else:
        add('environment', 'PASS', 'Environment keys inspected; values not logged (verify docker compose config separately)')
    unknown = [key for key in DANGEROUS_FLAGS if enabled(key, config) is None]
    active = [key for key in DANGEROUS_FLAGS if enabled(key, config) is True]
    add('external_write_switches', 'BLOCK' if active or unknown else 'PASS',
        'Unsafe/unknown flag(s): ' + ', '.join(active + unknown) if active or unknown else
        'Live supplier writes, reconciliation and fake/demo modes disabled')

    environment = config.get('APP_ENV', 'development').strip().lower()
    is_prod = environment == 'production'
    issues = []
    if environment not in ('development', 'test', 'production'):
        issues.append('APP_ENV must be development, test or production')
    if is_prod:
        # Staff-only deployments may legitimately omit the legacy admin token.
        for key in ('BROWSER_CSRF_SECRET', 'POSTGRES_PASSWORD'):
            raw = config.get(key, '').strip()
            if len(raw) < 24 or 'replace' in raw.lower() or 'change-' in raw.lower():
                issues.append(f'{key}: missing, placeholder or too short')
        if not config.get('PUBLIC_BASE_URL', '').startswith('https://'):
            issues.append('PUBLIC_BASE_URL must be HTTPS')
        if enabled('ADMIN_LEGACY_TOKEN_ENABLED', config) is not False:
            issues.append('ADMIN_LEGACY_TOKEN_ENABLED must be explicitly false')
    add('production_configuration', 'BLOCK' if issues else 'PASS',
        '; '.join(issues) if issues else ('Production baseline accepted' if is_prod else 'Nonproduction configuration'))

    if check_http:
        for endpoint in ('live', 'ready'):
            try:
                # Intentionally localhost only. Never hit external or live production endpoints.
                connection = HTTPConnection('127.0.0.1', 8000, timeout=3)
                try:
                    connection.request('GET', f'/health/{endpoint}')
                    response = connection.getresponse()
                    code = response.status
                    payload = json.loads(response.read(2048))
                    ok = code == 200 and payload == {'status': 'ok'}
                finally:
                    connection.close()
                add(f'health_{endpoint}', 'PASS' if ok else 'BLOCK', 'Local HTTP endpoint responded' if ok else 'Unexpected local HTTP result')
            except Exception as exc:
                add(f'health_{endpoint}', 'BLOCK', f'Cannot reach local server: {type(exc).__name__}')
    else:
        add('runtime_health', 'NOT_TESTED', 'Run with --check-http after starting localhost Docker')

    add('supplier_contracts', 'NOT_TESTED', 'ADEO, PARTKOM, Forum live purchase/status/cancel contracts unverified')
    add('payment_sandbox', 'NOT_TESTED', 'YooKassa SBP success/refund/webhook sandbox acceptance not performed')
    add('postgres_races', 'NOT_TESTED', 'Independent concurrent transactions on PostgreSQL not performed')
    add('frontend_build', 'NOT_TESTED', 'Run npm build in Docker; this gate does not compile TypeScript')
    add('db_migration_applied', 'NOT_TESTED', 'Graph-only inspection, not a PostgreSQL migration')
    static_status = 'BLOCK' if any(c['status'] == 'BLOCK' for c in checks) else 'PASS'
    # Failure to validate critical provider contracts is a release blocker, not a warning.
    return {'release': RELEASE, 'static_status': static_status, 'live_money_decision': 'NO_GO',
            'read_only': True, 'checks': checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only Autocodex 0.39 release preflight')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check-http', action='store_true', help='check ONLY localhost:8000 health endpoints')
    parser.add_argument('--json', action='store_true', help='machine-readable report; never print secrets')
    args = parser.parse_args(argv)
    result = inspect(args.root, check_http=args.check_http)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result['checks']:
            print(f"{check['status']:10s} {check['name']}: {check['detail']}")
        print(f"STATIC: {result['static_status']} | LIVE PAYMENTS/PURCHASES: NO_GO")
    return 2 if result['static_status'] == 'BLOCK' else 0


if __name__ == '__main__':
    sys.exit(main())
