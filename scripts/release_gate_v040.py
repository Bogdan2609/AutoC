"""Read-only v0.40 release gate, extending stable v0.39 preflight checks.

This gate deliberately cannot grant live-money authorization or execute supplier
or bank writes. It is a static preflight only; CI and PostgreSQL tests are separate.
"""
import release_gate_v039 as previous

previous.RELEASE = '0.40.1'
previous.REQUIRED_FILES += (
    'app/core/bounded_requests.py',
    'app/core/redis_lease.py',
    'app/core/utc_time.py',
    'app/modules/payments/money_contract.py',
    'app/modules/payments/selection.py',
    'tests/test_transaction_safety_v040.py',
    'docs/TRANSACTION_LOCK_ORDER.md',
)

if __name__ == '__main__':
    raise SystemExit(previous.main())
