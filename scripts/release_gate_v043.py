"""v0.43 source release gate; NO financial/procurement GO decisions."""
from __future__ import annotations
import ast
from pathlib import Path
import tomllib

ROOT=Path(__file__).resolve().parent.parent
REQUIRED=(
    'app/workers/job_lease_v043.py',
    'app/presentation/operations_health_v043.py',
    'app/presentation/sbp_result_v043.py',
    'admin-ui/src/pages/OperationsHealth.tsx',
    'tests/test_operations_observability_v043.py',
    'docs/AUTOCODEX_V043_OPERATIONS_OBSERVABILITY_RU.md',
)

def main():
    checks={
      'version': tomllib.loads((ROOT/'pyproject.toml').read_text('utf8'))['project']['version']=='0.43.0',
      'files': all((ROOT/p).is_file() for p in REQUIRED),
      'python_syntax': True,
      'worker_lease': False,
      'admin_guard': False,
      'customer_result_read_only': False,
    }
    for p in REQUIRED:
        if p.endswith('.py') and (ROOT/p).is_file():
            try:ast.parse((ROOT/p).read_text('utf8'))
            except SyntaxError:checks['python_syntax']=False
    worker=(ROOT/'app/workers/runner.py').read_text('utf8')
    checks['worker_lease']='async with heartbeat(factory,job_id,generation,kind)' in worker and 'still_owner(db,job_id,generation)' in worker
    monitor=(ROOT/'app/presentation/operations_health_v043.py').read_text('utf8')
    checks['admin_guard']='dependencies=[Depends(require_admin)]' in monitor
    sbp=(ROOT/'app/presentation/sbp_result_v043.py').read_text('utf8')
    checks['customer_result_read_only']='buyer_result' in sbp and 'Depends(current_user)' in sbp and 'bank_not_contacted' in sbp
    for name,ok in checks.items():print(('PASS' if ok else 'BLOCK')+' '+name)
    print('LIVE PAYMENTS / SUPPLIER PURCHASES: NO_GO')
    return 0 if all(checks.values()) else 2
if __name__=='__main__':raise SystemExit(main())
