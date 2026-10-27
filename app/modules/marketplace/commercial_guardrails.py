"""Marketplace commercial invariants; imported by read-only previews and carts.

No storage access, provider requests, reservations, or payment dispatch.
"""
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

MAX_CART_LINES = 40
CENT = Decimal('0.01')


def checked_money(value, *, positive=False):
    """Reject floats, NaN, infinities, negative, and fractional kopecks.

    Never silently round a supplier's purchase price or a merchant's tariff.
    """
    if isinstance(value, (float, bool)) or value is None:
        raise HTTPException(409, 'Invalid commercial amount')
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise HTTPException(409, 'Invalid commercial amount') from None
    if (not amount.is_finite() or amount < 0 or (positive and amount <= 0) or
            amount > Decimal('999999999999.99')):
        raise HTTPException(409, 'Invalid commercial amount')
    try:
        if amount != amount.quantize(CENT):
            raise HTTPException(409, 'Invalid commercial amount')
    except InvalidOperation:
        raise HTTPException(409, 'Invalid commercial amount') from None
    return amount


def receiving_uuid(preview: dict) -> str:
    """UUID is the source of truth. Display names cannot be promoted to IDs."""
    from uuid import UUID
    try:
        return str(UUID(preview['receiving_node_id']))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(409, 'Receiving node UUID unavailable') from None
