"""Canonical RUB arithmetic at the settlement boundary (no implicit rounding)."""
from decimal import Decimal
from collections.abc import Iterable

CENT = Decimal('0.01')


def rub(value: Decimal) -> Decimal:
    """Accept only exact, finite, two-place Decimal monetary values."""
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError('RUB value must be a finite Decimal')
    if value < 0 or value != value.quantize(CENT):
        raise ValueError('RUB amount must be nonnegative and exact to kopecks')
    return value.quantize(CENT)


def payable_total(lines: Iterable[tuple[Decimal, int]]) -> Decimal:
    """Sum immutable payable line snapshots, not untrusted client-supplied totals.

    Quantize only at the previously validated unit-price boundary. For zero
    lines, return 0.00; callers must independently reject empty orders.
    """
    total = Decimal('0.00')
    for price, quantity in lines:
        if type(quantity) is not int or quantity < 1:
            raise ValueError('Invalid payable quantity')
        total += rub(price) * quantity
    return rub(total)
