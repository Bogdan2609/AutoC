"""Pure validation for F4 simulated SBP events and fiscal planning, no bank I/O."""
import hashlib
import json
from decimal import Decimal, InvalidOperation


def money(v):
    try:
        x=Decimal(str(v))
        if not x.is_finite() or x<=0 or x!=x.quantize(Decimal('0.01')):
            raise ValueError('Positive amount with at most two decimal places required')
        return x
    except (InvalidOperation, ArithmeticError) as exc:
        raise ValueError('Invalid amount') from exc


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def next_payment_status(old,new):
    return old == new or (old in ('CREATED','PENDING') and new in ('PENDING','SUCCEEDED','CANCELED'))


def ensure_refund_budget(paid,refund_rows,new_amount):
    reserved=sum((r.amount for r in refund_rows if r.status in ('REQUESTED','SUCCEEDED')), Decimal('0'))
    if money(new_amount)+reserved>paid:
        raise ValueError('Refund requests exceed original simulated payment')


def reconcile(direction,recorded,expected,kind):
    if direction != ('IN' if kind=='PAYMENT' else 'OUT'):
        return 'DIRECTION_MISMATCH'
    return 'MATCH' if recorded==expected else 'AMOUNT_MISMATCH'
