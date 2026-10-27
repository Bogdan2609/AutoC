"""Read-only source inspection. Not a production/payment acceptance gate."""
import ast
from pathlib import Path
import tomllib

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=(
 'app/modules/marketplace/cart_batch_v0421.py',
 'app/modules/marketplace/quote_cache_v0421.py',
 'app/modules/marketplace/legacy_reservations_v0421.py',
 'app/modules/marketplace/snapshot_retention_v0421.py',
 'app/modules/marketplace/price_integrity_v0421.py',
 'app/modules/marketplace/data_integrity.py',
 'tests/test_marketplace_completion_v0421.py',
 'tests/test_postgres_marketplace_races_v0421.py',
 'scripts/snapshot_retention_v0421.py',
 'docs/AUTOCODEX_V0421_MARKETPLACE_COMPLETION_RU.md',
)

def inspect(root=ROOT):
    checks={}
    checks['version']=tomllib.loads((root/'pyproject.toml').read_text('utf8'))['project']['version']=='0.42.1'
    checks['files']=all((root/p).is_file() for p in REQUIRED)
    try:
        for path in (root/'app').rglob('*.py'):
            ast.parse(path.read_text('utf8'),filename=str(path))
        checks['python_syntax']=True
    except (SyntaxError,OSError,UnicodeError):
        checks['python_syntax']=False
    data=(root/'app/modules/marketplace/data_integrity.py').read_text('utf8')
    checks['reservations_read_only'] = 'analyze_legacy_lines(db, selected)' in data
    return checks

if __name__=='__main__':
    checks=inspect()
    for name,ok in checks.items():print(('PASS' if ok else 'BLOCK'),name)
    print('LIVE PAYMENTS / SUPPLIER PURCHASES: NO_GO')
    raise SystemExit(0 if all(checks.values()) else 2)
