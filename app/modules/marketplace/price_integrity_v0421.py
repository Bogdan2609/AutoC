"""Pure commercial drift checks; never imply that quoted totals are payable."""
from __future__ import annotations
from decimal import Decimal
from app.modules.marketplace.commercial_guardrails import checked_money


def compare_accepted_price(accepted_unit, current_unit, accepted_route_fee, current_route_fee):
    """Fail closed on unquoted/invalid price; expose exact deltas in RUB."""
    current = checked_money(current_unit, positive=True)
    current_fee = checked_money(current_route_fee)
    if accepted_unit is None or accepted_route_fee is None:
        return {'changed': True, 'reason': 'unconfirmed_price_or_delivery',
                'unit_delta': None, 'route_delta': None}
    old = checked_money(accepted_unit, positive=True)
    old_fee = checked_money(accepted_route_fee)
    diff = current - old
    route_diff = current_fee - old_fee
    return {'changed': diff != Decimal('0') or route_diff != Decimal('0'),
            'reason': 'commercial_terms_changed' if diff or route_diff else None,
            'unit_delta': str(diff), 'route_delta': str(route_diff)}
