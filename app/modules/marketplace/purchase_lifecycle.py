"""Supplier purchase capabilities and read-only ROSSKO order verification.

Supplier writes MUST NOT be repeated following a timeout. A GetCheckout ACK is
not proof that the ordered SKU and price were accepted. Only fresh read-only
GetOrders evidence can advance a ROSSKO supplier line to supplier_confirmed.

Other providers have preview-only contracts in this release; a preview does
not authorize a supplier write, status transition, or cancellation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.modules.catalog.normalize import normalize_article, normalize_brand


@dataclass(frozen=True)
class PurchaseCapabilities:
    supplier: str
    preview: bool
    live_create: bool
    live_order_lookup: bool
    live_cancel: bool
    reason: str


_CAPABILITIES = {
    'rossko': PurchaseCapabilities('rossko', True, True, True, False,
        'GetCheckout and GetOrders supported; supplier cancellation not contract-verified'),
    'adeo': PurchaseCapabilities('adeo', True, False, False, False,
        'make_orders preview only; order status and cancellation not verified'),
    'partkom': PurchaseCapabilities('partkom', True, False, False, False,
        'v4 order preview flagTest=1 only; paid checkout not verified'),
    'forum-auto': PurchaseCapabilities('forum-auto', True, False, False, False,
        'addGoodsToOrder may mutate quantity; read-back and cancellation unverified'),
}


def capabilities(supplier: str) -> PurchaseCapabilities:
    try:
        return _CAPABILITIES[supplier]
    except KeyError as exc:
        raise ValueError('Unsupported supplier') from exc


def capability_list() -> list[PurchaseCapabilities]:
    return list(_CAPABILITIES.values())


@dataclass(frozen=True)
class OrderEvidence:
    result: str  # verified | pending | manual_review
    reason: str  # fixed safe code; NEVER raw supplier error details
    found_ids: tuple[str, ...] = ()


def _rows(value: Any) -> list[dict] | None:
    if value is None or value == '':
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(v, dict) for v in value):
        return value
    return None


def _quantity(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 1 or amount != int(amount):
        return None
    return int(amount)


def _money(value: Any) -> Decimal | None:
    if isinstance(value, (bool, float)):
        return None
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not price.is_finite() or price <= 0 or price != price.quantize(Decimal('.01')):
        return None
    return price


def verify_rossko_orders(raw: Any, *, external_ids: list[str], brand: str,
                         article: str, quantity: int, approved_unit_price: Decimal,
                         buyer_order_id: UUID | str) -> OrderEvidence:
    """Verify the exact account-scoped, one-line ROSSKO GetCheckout result.

    There must be one and only one visible order for EACH persisted external ID,
    no unexpected SKUs, an exact combined quantity, and a strictly bounded unit
    price. Optional remote part comments, when returned, must equal our UUID.
    Absence from a limited GetOrders page is INCONCLUSIVE, never a rejection.
    """
    bad = lambda reason: OrderEvidence('manual_review', reason)
    if not isinstance(raw, dict) or not brand or not article or type(quantity) is not int or quantity < 1:
        return bad('invalid_supplier_evidence')
    try:
        ref = str(UUID(str(buyer_order_id)))
    except (TypeError, ValueError, AttributeError):
        return bad('invalid_buyer_order_reference')
    if (not external_ids or len(external_ids) > 20 or
            any(not isinstance(x, str) or not x.isascii() or not x.isdecimal() or int(x) < 1 for x in external_ids) or
            len(set(external_ids)) != len(external_ids)):
        return bad('invalid_external_order_ids')
    ceiling = _money(approved_unit_price)
    if ceiling is None:
        return bad('invalid_approved_price')
    data = raw.get('OrdersResult', raw)
    if not isinstance(data, dict) or str(data.get('success', data.get('Success', ''))).lower() not in ('true', '1'):
        return bad('supplier_orders_lookup_failed')
    outer = data.get('OrdersList') or {}
    if not isinstance(outer, dict):
        return bad('invalid_orders_list')
    orders = _rows(outer.get('Order'))
    if orders is None or len(orders) > 500:
        return bad('invalid_orders_list')
    wanted = set(external_ids)
    found = {}
    for order in orders:
        number = str(order.get('id', ''))
        if number not in wanted:
            continue
        if number in found:
            return bad('duplicate_remote_order_id')
        found[number] = order
    if len(found) < len(wanted):
        return OrderEvidence('pending', 'supplier_order_not_visible', tuple(sorted(found)))
    total_qty = 0
    for order in found.values():
        if order.get('currency') not in (None, 'RUB'):
            return bad('unsupported_order_currency')
        remote_status = str(order.get('status', '')).strip().casefold()
        if remote_status in ('cancelled', 'canceled', 'rejected', 'отменен', 'отменён', 'отказ'):
            return bad('remote_order_not_active')
        parts = order.get('parts')
        if not isinstance(parts, dict):
            return bad('invalid_order_parts')
        lines = _rows(parts.get('part'))
        if not lines:
            return bad('missing_order_parts')
        for part in lines:
            if (normalize_brand(str(part.get('brand') or '')) != normalize_brand(brand) or
                    normalize_article(str(part.get('partnumber') or '')) != normalize_article(article)):
                return bad('unexpected_order_item')
            qty = _quantity(part.get('count'))
            price = _money(part.get('price'))
            if qty is None or price is None or price > ceiling:
                return bad('order_quantity_or_price_mismatch')
            # GetOrders may omit currency. Do not infer RUB from geography or
            # an earlier quote. Contract-level RUB confirmation is required
            # before advancing any financial supplier status.
            if part.get('currency') != 'RUB':
                return bad('unsupported_order_currency')
            if 'comment' in part and str(part.get('comment')).strip() != ref:
                return bad('order_uuid_mismatch')
            total_qty += qty
    if total_qty != quantity:
        return bad('order_quantity_or_price_mismatch')
    return OrderEvidence('verified', 'verified_supplier_order', tuple(sorted(wanted)))


async def read_rossko_orders(config, *, limit: int = 500):
    """One read-only GetOrders lookup. Never emits credentials in exceptions/logs."""
    if not 1 <= limit <= 500:
        raise ValueError('GetOrders limit out of range')
    from app.core.config import get_settings
    if get_settings().app_env == 'test':
        raise RuntimeError('Live supplier access forbidden in test environment')
    import httpx
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from zeep.helpers import serialize_object
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=4)) as http:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4)) as wsdl_http:
            client = AsyncClient('https://api.rossko.ru/service/v2.1/GetOrders?wsdl',
                transport=AsyncTransport(client=http, wsdl_client=wsdl_http))
            raw = await client.service.GetOrders(KEY1=config.rossko_key1,
                KEY2=config.rossko_key2, limit=limit)
    return serialize_object(raw, target_cls=dict)
