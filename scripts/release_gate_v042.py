"""Read-only v0.42 source/commercial invariant gate. NO external calls or DB writes."""
import ast
from pathlib import Path
import tomllib

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=(
 'app/modules/marketplace/commercial_guardrails.py',
 'app/modules/marketplace/data_integrity.py',
 'app/modules/marketplace/quote_preview.py',
 'app/modules/marketplace/checkout_v017.py',
 'app/modules/marketplace/cart.py',
 'app/modules/supplier_feeds/parsers.py',
 'tests/test_marketplace_integrity_v042.py',
 'docs/AUTOCODEX_V042_MARKETPLACE_DATA_INTEGRITY_RU.md',
)

def inspect(root:Path=ROOT):
    checks={}
    project=tomllib.loads((root/'pyproject.toml').read_text('utf8'))
    checks['version']=project['project']['version'] in ('0.42.0','0.42.1')
    checks['files']=all((root/name).is_file() for name in REQUIRED)
    if checks['files']:
        try:
            for file in (root/'app').rglob('*.py'):
                ast.parse(file.read_text('utf8'),filename=str(file))
            checks['python_syntax']=True
        except (SyntaxError,UnicodeError,OSError):
            checks['python_syntax']=False
    else:
        checks['python_syntax']=False
    checks['live_money']=False  # Gate never authorizes money, even if all static checks pass.
    return checks

if __name__=='__main__':
    result=inspect()
    for name,value in result.items():
        print(('PASS' if value else 'NO_GO' if name=='live_money' else 'BLOCK'),name)
    print('LIVE PAYMENTS / AUTOMATIC PURCHASES: NO_GO')
    raise SystemExit(0 if all(value for key,value in result.items() if key!='live_money') else 2)
