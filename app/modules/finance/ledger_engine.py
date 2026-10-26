"""Pure exact-decimal journal validator and deterministic payload fingerprint."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal

CENT = Decimal('0.01')


def canonical_lines(lines):
    result = []
    for line in lines:
        account = str(line.account_id)
        side = str(line.side)
        amount = Decimal(str(line.amount))
        if amount <= 0 or not amount.is_finite() or amount != amount.quantize(CENT):
            raise ValueError('Line amount must be positive and have at most two decimal places')
        if side not in ('DEBIT', 'CREDIT'):
            raise ValueError('Debit/credit side required')
        result.append({'account_id':account, 'side':side, 'amount':f'{amount:.2f}'})
    if len(result) < 2 or len({x['account_id'] for x in result}) < 2:
        raise ValueError('Journal needs at least two distinct accounts')
    debit = sum((Decimal(x['amount']) for x in result if x['side']=='DEBIT'), Decimal('0'))
    credit = sum((Decimal(x['amount']) for x in result if x['side']=='CREDIT'), Decimal('0'))
    if debit <= 0 or debit != credit:
        raise ValueError('Journal debit and credit amounts must be equal and positive')
    return sorted(result, key=lambda x:(x['account_id'], x['side'], x['amount']))


def fingerprint(book_id, source_ref, event_kind, memo, lines, reverses_id=None):
    material = {'book':str(book_id), 'source_ref':source_ref, 'event_kind':event_kind,
                'memo':memo, 'reverses_id':str(reverses_id) if reverses_id else None,
                'lines':canonical_lines(lines)}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()


def signed_balance(kind, debit, credit):
    if kind in ('ASSET','EXPENSE'):
        return debit-credit
    return credit-debit
