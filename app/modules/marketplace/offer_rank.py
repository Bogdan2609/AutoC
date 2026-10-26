"""One deterministic fulfillment ordering for stock and supplier quotes.

Unknown lead time is LAST; never compare None and integers. Independent
account, warehouse and route variants must remain separate elsewhere.
"""
from decimal import Decimal


def fulfillment_rank(option):
    days = option.get('days')
    fee = Decimal(str(option.get('fee') if option.get('fee') is not None else '0'))
    if not fee.is_finite() or fee < 0:
        raise ValueError('Fulfillment fee must be a finite nonnegative amount')
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 0):
        raise ValueError('Fulfillment days must be nonnegative integer or unknown')
    return (days is None, days if days is not None else 0, fee,
            tuple(str(x) for x in option.get('route_node_ids') or ()),
            str(option.get('node_id') or ''), str(option.get('snapshot_id') or ''))
