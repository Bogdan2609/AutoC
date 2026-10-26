"""RUB-only money boundary. Multicurrency support is a separate future epic."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from .supplier_errors import UnsupportedCurrencyError, MalformedResponseError

RUB_ALIASES = frozenset(('rub', 'rur', '643', 'руб', 'руб.', 'рубли', 'rubles', '₽'))
CENT = Decimal('0.01')

def require_rub(currency: str | None) -> None:
    if str(currency or '').strip().casefold() not in RUB_ALIASES:
        raise UnsupportedCurrencyError('Supplier quote currency is missing or not RUB')

def rub_amount(value: object) -> Decimal:
    """Forbid float and non-finite amounts; round supplier precision to kopecks."""
    if isinstance(value, (float, bool)):
        raise MalformedResponseError('Supplier price must not use floating-point')
    try:
        price = Decimal(str(value).strip().replace(',', '.'))
        if not price.is_finite() or price <= 0 or price > Decimal('9999999999.99'):
            raise ValueError('Price out of bounds')
        result = price.quantize(CENT, rounding=ROUND_HALF_UP)
        if result <= 0:
            raise ValueError('Price rounds to zero')
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MalformedResponseError('Invalid supplier price') from exc
