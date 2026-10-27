"""AutoC v0.44 technical release gate: safe read-only repository checks.

No database connection, network, subprocess, migrations, payment or supplier calls.
A successful *technical* check never authorizes live money.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    'app/workers/job_lease_v043.py',
    'app/presentation/operations_health_v043.py',
    'app/presentation/sbp_result_v043.py',
    'app/preproduction_v044/assessment.py',
    'app/presentation/preproduction_v044.py',
    'scripts/acceptance_report_v044.py',
    'tests/test_preproduction_acceptance_v044.py',
    'tests/test_postgres_preproduction_v044.py',
    'docs/AUTOCODEX_V044_PREPRODUCTION_ACCEPTANCE_RU.md',
)


def check(root: Path = ROOT) -> dict[str, bool]:
    version = tomllib.loads((root / 'pyproject.toml').read_text('utf8'))['project']['version']
    syntax = True
    for name in REQUIRED:
        if name.endswith('.py') and (root / name).is_file():
            try:
                ast.parse((root / name).read_text('utf8'), filename=name)
            except SyntaxError:
                syntax = False
    main = (root / 'app/main.py').read_text('utf8')
    guard = (root / 'app/presentation/preproduction_v044.py').read_text('utf8')
    policy = (root / 'app/modules/admin_staff/policy.py').read_text('utf8')
    return {
        'version': version == '0.44.0',
        'files': all((root / name).is_file() for name in REQUIRED),
        'python_syntax': syntax,
        'router_wired': 'app.include_router(preproduction_v044_router)' in main,
        'admin_guard': 'dependencies=[Depends(require_admin)]' in guard,
        'staff_read_scope': "('/api/v1/backoffice/preproduction-v044', 'system')" in policy and "('/api/v1/backoffice/operations-health', 'system')" in policy,
        'explicit_no_go': "'live_money_decision': 'NO_GO'" in guard,
        'no_live_activation_api': '@router.post(' not in guard and '@router.patch(' not in guard,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    results = check()
    if args.json:
        print(json.dumps({'technical_checks': results, 'live_money_decision': 'NO_GO'}, indent=2))
    else:
        for name, ok in results.items():
            print(('PASS' if ok else 'BLOCK') + ' ' + name)
        print('EXTERNAL, LEGAL, FISCAL, BANK AND PROVIDER EVIDENCE: NOT VERIFIED')
        print('LIVE PAYMENTS / AUTOMATIC PURCHASES: NO_GO')
    return 0 if all(results.values()) else 2


if __name__ == '__main__':
    sys.exit(main())
