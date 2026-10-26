"""Canonical immutable supplier READ offer; every price is in RUB.

Multicurrency is a future epic. No provider may infer a missing currency.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from .currency import CENT
from .supplier_errors import MalformedResponseError, UnsupportedCurrencyError


@dataclass(frozen=True)
class Offer:
    code: str
    key: str
    brand: str
    article: str
    price: Decimal
    qty: int | None
    days: int | None
    token: str
    warehouse: str = ''
    minimum: int = 1
    multiple: int = 1
    public_meta: dict = field(default_factory=dict)
    currency: str = 'RUB'

    def __post_init__(self):
        if self.currency != 'RUB':
            raise UnsupportedCurrencyError('Offer currency must equal RUB')
        if (not isinstance(self.price, Decimal) or not self.price.is_finite()
                or self.price <= 0 or self.price != self.price.quantize(CENT)):
            raise MalformedResponseError('Offer price must be a positive RUB Decimal in kopecks')
        if (type(self.minimum) is not int or type(self.multiple) is not int
                or self.minimum < 1 or self.multiple < 1):
            raise MalformedResponseError('Supplier packaging constraints invalid')
        if self.qty is not None and (type(self.qty) is not int or self.qty < 0):
            raise MalformedResponseError('Supplier stock invalid')
