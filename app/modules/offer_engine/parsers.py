"""Pure supplier response normalizers: NO IO, credentials, logging or DB access.

Fixture-testable mappings for the verified subset of each supplier's read API.
RUB is mandatory in each matching quote, even where an upstream contract
currently omits a currency field. Multicurrency support is a separate epic.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from defusedxml.ElementTree import fromstring

from app.modules.catalog.normalize import normalize_article, normalize_brand
from .contracts import Offer
from .currency import require_rub, rub_amount
from .supplier_errors import MalformedResponseError, UpstreamBusinessError

MAX_RESPONSE_BYTES = 2_000_000
MAX_RECORDS = 5_000


def as_int(value: object, default: int | None = None) -> int | None:
    if isinstance(value, (bool, float)):
        return default
    try:
        return int(value)  # historical integer field handling
    except (ValueError, TypeError, OverflowError):
        return default


def _matching(brand: str, article: str, wanted_brand: str, wanted_article: str) -> bool:
    return (normalize_brand(brand) == normalize_brand(wanted_brand)
            and normalize_article(article) == normalize_article(wanted_article))


def _price(value: object, currency: object) -> Decimal:
    require_rub(currency)
    return rub_amount(value)


def _too_many(records: list) -> None:
    if len(records) > MAX_RECORDS:
        raise MalformedResponseError('Supplier returned too many records')


def parse_json_body(data: bytes) -> list[dict]:
    if len(data) > MAX_RESPONSE_BYTES:
        raise MalformedResponseError('Supplier JSON response too large')
    try:
        raw = json.loads(data, parse_float=Decimal,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise MalformedResponseError('Invalid supplier JSON response') from None
    if not isinstance(raw, list):
        raise MalformedResponseError('Unexpected supplier JSON root')
    _too_many(raw)
    if any(not isinstance(row, dict) for row in raw):
        raise MalformedResponseError('Supplier JSON row must be an object')
    return raw


def parse_partkom_rows(rows: list[dict], brand: str, article: str) -> list[Offer]:
    _too_many(rows)
    offers = []
    for row in rows:
        if not isinstance(row, dict):
            raise MalformedResponseError('Invalid PartKom offer row')
        if row.get('detailGroup', 'Original') not in ('Original', '1', 1):
            continue
        maker = str(row.get('maker') or '')
        part = str(row.get('number') or '')
        if not _matching(maker, part, brand, article):
            continue
        maker_id, provider_id = row.get('makerId'), row.get('providerId')
        if maker_id is None or provider_id is None:
            continue
        if 'price' not in row:
            raise MalformedResponseError('PartKom missing price')
        minimum = max(1, as_int(row.get('minQuantity'), 1))
        placement = str(row.get('placementId') or '')
        price = _price(row['price'], row.get('currency'))
        offers.append(Offer(
            'partkom', f'{maker_id}:{normalize_article(part)}:{provider_id}:{placement}',
            maker, part, price, as_int(row.get('quantity')),
            as_int(row.get('guaranteedDays'), as_int(row.get('expectedDays'))),
            str(provider_id), placement, minimum, 1,
            {'maker_id': str(maker_id), 'provider_id': str(provider_id),
             'placement_id': placement, 'success_percent': row.get('statSuccessPercent'),
             'pack_multiple_unverified': True},
        ))
    return offers


def parse_forum_rows(rows: list[dict], brand: str, article: str) -> list[Offer]:
    _too_many(rows)
    offers = []
    for row in rows:
        if not isinstance(row, dict):
            raise MalformedResponseError('Invalid Forum-Auto offer row')
        if not row.get('gid'):
            continue
        maker = str(row.get('brand') or '')
        part = str(row.get('art') or '')
        if not _matching(maker, part, brand, article):
            continue
        if 'price' not in row:
            raise MalformedResponseError('Forum-Auto missing price')
        multiple = max(1, as_int(row.get('kr'), 1))
        offers.append(Offer(
            'forum-auto', str(row['gid']), maker, part,
            _price(row['price'], row.get('currency')), as_int(row.get('num')),
            as_int(row.get('d_deliv')), str(row['gid']), str(row.get('whse') or ''),
            multiple, multiple,
            {'returnable': bool(as_int(row.get('is_returnable'), 0))},
        ))
    return offers


def parse_adeo_stock(rest: object) -> tuple[int | None, dict]:
    """`>4` is a strict lower bound (>=5), not a reported exact 5."""
    raw = str(rest or '').strip()
    if not raw:
        return None, {'stock_raw': raw, 'stock_is_lower_bound': False}
    match = re.fullmatch(r'>\s*([0-9]{1,10})', raw)
    if match:
        lower = int(match.group(1)) + 1
        if lower <= 2_147_483_647:
            return lower, {'stock_raw': raw, 'stock_is_lower_bound': True}
        return None, {'stock_raw': raw, 'stock_is_lower_bound': False}
    count = as_int(raw)
    if count is None or not 0 <= count <= 2_147_483_647:
        return None, {'stock_raw': raw[:64], 'stock_is_lower_bound': False}
    return count, {'stock_raw': raw, 'stock_is_lower_bound': False}


def parse_adeo_xml(data: bytes, brand: str, article: str) -> list[Offer]:
    if len(data) > MAX_RESPONSE_BYTES:
        raise MalformedResponseError('ADEO XML response too large')
    try:
        root = fromstring(data)
    except Exception:
        raise MalformedResponseError('Invalid ADEO XML response') from None
    nodes = root.findall('.//detail')
    _too_many(nodes)
    found = []
    for node in nodes:
        row = {child.tag: (child.text or '') for child in node}
        maker, part = row.get('producer', ''), row.get('code', '')
        if not _matching(maker, part, brand, article) or not row.get('b_id'):
            continue
        if 'price' not in row:
            raise MalformedResponseError('ADEO missing price')
        qty, stock_meta = parse_adeo_stock(row.get('rest'))
        multiple = max(1, as_int(row.get('minOrderCount'), 1))
        found.append(Offer(
            'adeo', f"{row.get('id','')}:{row.get('cella_id','')}:{row.get('n_file','')}",
            maker, part, _price(row['price'], row.get('currency')),
            qty, as_int(row.get('delivery')), row['b_id'], row.get('stock', ''),
            multiple, multiple,
            {'refusal_percent': row.get('PercentRefuse'),
             'delivery_min_days': as_int(row.get('deliverydays')),
             'delivery_max_days': as_int(row.get('delivery')),
             'currency': 'RUB', **stock_meta},
        ))
    return found


def parse_rossko_search_result(obj: Any, brand: str, article: str) -> list[Offer]:
    """Zeep's real GetSearch uses `success` and `PartsList`, not just `Success`."""
    if not isinstance(obj, dict):
        raise MalformedResponseError('Unexpected ROSSKO SOAP result')
    root = obj.get('SearchResult', obj)
    if not isinstance(root, dict):
        raise MalformedResponseError('Unexpected ROSSKO SearchResult')
    if str(root.get('success', root.get('Success', ''))).strip().lower() not in ('true', '1'):
        raise UpstreamBusinessError('ROSSKO GetSearch returned non-success status')
    parts = root.get('PartsList')
    if parts is None:
        parts = {}
    if not isinstance(parts, dict):
        raise MalformedResponseError('ROSSKO PartsList invalid')
    records = parts.get('Part') or []
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise MalformedResponseError('ROSSKO Part entries invalid')
    _too_many(records)
    found = []
    for part in records:
        if not isinstance(part, dict):
            raise MalformedResponseError('ROSSKO Part entry invalid')
        maker, number = str(part.get('brand') or ''), str(part.get('partnumber') or '')
        if not _matching(maker, number, brand, article):
            continue
        stocks = part.get('stocks') or {}
        if not isinstance(stocks, dict):
            raise MalformedResponseError('ROSSKO stocks invalid')
        entries = stocks.get('stock') or []
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise MalformedResponseError('ROSSKO stock entries invalid')
        _too_many(entries)
        for stock in entries:
            if not isinstance(stock, dict):
                raise MalformedResponseError('ROSSKO stock entry invalid')
            stock_id = str(stock.get('id') or '')
            guid = str(part.get('guid') or '')
            if not stock_id or not guid:
                continue
            if 'price' not in stock:
                raise MalformedResponseError('ROSSKO missing price')
            multiple = max(1, as_int(stock.get('multiplicity'), 1))
            found.append(Offer(
                'rossko', f'{guid}:{stock_id}', maker, number,
                _price(stock['price'], stock.get('currency') or part.get('currency')),
                as_int(stock.get('count')), as_int(stock.get('delivery')),
                f'{guid}:{stock_id}', stock_id, multiple, multiple,
                {'stock_type': stock.get('type'), 'extra_days': stock.get('extra')},
            ))
    return found
