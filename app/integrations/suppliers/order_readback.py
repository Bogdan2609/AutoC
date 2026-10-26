"""Supplier order READ normalization v0.41.2.

No payment, order status mutation, procurement approval or outbound write.
Parsers reject unknown/error envelopes rather than fabricate successful orders.
All returned data is an explicit allowlist: NEVER return raw response, contact,
address, tax ids, order comments, credentials or arbitrary supplier messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as SafeET


class ReadbackError(ValueError):
    """Safe fixed-code readback failure; not a supplier exception/payload."""


CODES = ('rossko', 'adeo', 'partkom', 'forum-auto')
_NUMERIC = re.compile(r'^[1-9][0-9]{0,17}$')
_REFERENCE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$')
_MAX_LINES = 100
_MAX_BODY = 1024 * 1024


def validate_reference(code: str, reference: str) -> str:
    if code not in CODES:
        raise ReadbackError('unsupported_supplier')
    if not isinstance(reference, str):
        raise ReadbackError('invalid_external_reference')
    pattern = _REFERENCE if code == 'partkom' else _NUMERIC
    if not pattern.fullmatch(reference):
        raise ReadbackError('invalid_external_reference')
    return reference


def _str(value: Any, cap: int = 100) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ''
    v = str(value).strip()
    return v[:cap] if len(v) <= cap and v.isprintable() else ''


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
        return None
    return int(raw)


def _money(value: Any) -> str | None:
    if value is None or isinstance(value, (float, bool)):
        return None
    try:
        v = Decimal(str(value).strip())
        if not v.is_finite() or v < 0 or v != v.quantize(Decimal('0.01')):
            return None
        return str(v.quantize(Decimal('0.01')))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _rows(value: Any, *, field: str = '') -> list[dict]:
    if value is None or value == '':
        return []
    if field and isinstance(value, dict):
        value = value.get(field)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or len(value) > _MAX_LINES or any(not isinstance(v, dict) for v in value):
        raise ReadbackError('invalid_supplier_rows')
    return value


@dataclass(frozen=True)
class OrderLineObservation:
    brand: str
    article: str
    requested: int | None
    observed: int | None
    state: str
    unit_price_rub: str | None = None

    def public(self) -> dict:
        return {'brand': self.brand, 'article': self.article,
                'requested': self.requested, 'observed': self.observed,
                'state': self.state, 'unit_price_rub': self.unit_price_rub}


@dataclass(frozen=True)
class OrderReadback:
    supplier: str
    external_reference: str
    observation: str  # found | not_found_in_response | partial | canceled | manual_review
    lines: tuple[OrderLineObservation, ...]
    source_method: str

    def public(self) -> dict:
        # Explicitly NEVER signal fulfillment/payment confirmation.
        return {'supplier': self.supplier, 'external_reference': self.external_reference,
                'observation': self.observation, 'source_method': self.source_method,
                'lines': [x.public() for x in self.lines], 'read_only': True,
                'purchase_created': False, 'supplier_confirmed': False,
                'buyer_delivery_confirmed': False, 'authoritative_proof': False,
                'manual_reconciliation_required': True,
                'not_found_is_inconclusive': self.observation == 'not_found_in_response'}


def _outcome(lines: list[OrderLineObservation], *, canceled: bool = False) -> str:
    if canceled:
        return 'canceled'
    if not lines:
        return 'not_found_in_response'
    if any(x.state in ('refused', 'canceled') or
           (x.requested is not None and x.observed is not None and x.observed < x.requested)
           for x in lines):
        return 'partial'
    if any(x.state == 'unknown' for x in lines):
        return 'manual_review'
    return 'found'


def _rossko(raw: Any, ref: str) -> OrderReadback:
    if not isinstance(raw, dict):
        raise ReadbackError('invalid_supplier_response')
    root = raw.get('OrdersResult', raw)
    if not isinstance(root, dict) or str(root.get('success', '')).lower() not in ('true', '1'):
        raise ReadbackError('supplier_read_failed')
    entries = _rows(root.get('OrdersList'), field='Order')
    matching = [x for x in entries if str(x.get('id')) == ref]
    if len(matching) > 1:
        raise ReadbackError('ambiguous_order_identity')
    lines: list[OrderLineObservation] = []
    if matching:
        order = matching[0]
        entries = _rows(order.get('parts'), field='part')
        statuses = {0:'pending',1:'processing',2:'shipped_by_supplier',3:'processing',5:'pending',
                    6:'processing',7:'refused',8:'canceled',9:'late',31:'pending',
                    32:'return_review',33:'return_review',34:'return_review',
                    35:'return_review',36:'returned'}
        for item in entries:
            state = statuses.get(_integer(item.get('status')), 'unknown')
            lines.append(OrderLineObservation(_str(item.get('brand')), _str(item.get('partnumber')),
                _integer(item.get('count')), None, state, _money(item.get('price'))))
    return OrderReadback('rossko',ref,_outcome(lines),tuple(lines),'GetOrders')


def _adeo(raw: Any, ref: str) -> OrderReadback:
    if isinstance(raw, bytes):
        if len(raw) > _MAX_BODY:
            raise ReadbackError('supplier_response_too_large')
        try:
            doc = SafeET.fromstring(raw)
        except (ET.ParseError, ValueError):
            raise ReadbackError('invalid_supplier_xml') from None
    elif isinstance(raw, ET.Element):
        doc = raw
    else:
        raise ReadbackError('invalid_supplier_response')
    if doc.tag.lower() not in ('result','response'):
        raise ReadbackError('invalid_supplier_xml')
    # ADEO's getOrderStatus may return flat <detail> with multiple <id>
    # children; it is unsafe to infer individual line identity in this case.
    details = doc.findall('.//detail')
    lines = []
    for detail in details[:_MAX_LINES]:
        sub = detail.findall('./detail')
        candidates = sub or ([detail] if len(detail.findall('id')) <= 1 else [])
        for row in candidates:
            identifiers = [_str(row.findtext(k)) for k in ('id','orderid')]
            if ref not in identifiers:
                continue
            article = _str(row.findtext('article') or row.findtext('art'))
            brand = _str(row.findtext('producer') or row.findtext('fabrik'))
            requested = _integer(row.findtext('count'))
            delivered = _integer(row.findtext('count_vidano'))
            refused = _integer(row.findtext('count_orkaz'))
            state = 'refused' if refused and refused > 0 else (
                'shipped_by_supplier' if delivered and delivered > 0 else 'pending')
            lines.append(OrderLineObservation(brand, article, requested, delivered,
                                              state, _money(row.findtext('h_cost'))))
    if len(lines) > _MAX_LINES:
        raise ReadbackError('supplier_rows_limit')
    result = _outcome(lines)
    if not lines and details:
        result = 'manual_review'  # Unparseable flat response is not proof of absence.
    return OrderReadback('adeo',ref,result,tuple(lines),'getOrderStatus')


def _partkom(raw: Any, ref: str) -> OrderReadback:
    if isinstance(raw, dict) and not all(k in raw for k in ('detailNum', 'quantity')):
        raise ReadbackError('supplier_read_failed')
    entries = _rows(raw)
    if any('detailNum' not in x or 'quantity' not in x for x in entries):
        raise ReadbackError('invalid_supplier_rows')
    lines = []
    for item in entries:
        # Path /motion/{reference} is scoped to an AutoC reference; supplier
        # does not promise the reference will be echoed on every row.
        ordered = _integer(item.get('quantity'))
        refused = _integer(item.get('refusalCount')) or 0
        issued = _integer(item.get('releaseCount')) or 0
        status = 'refused' if refused else 'shipped_by_supplier' if issued else 'pending'
        lines.append(OrderLineObservation(_str(item.get('maker')), _str(item.get('detailNum')),
            ordered, None if ordered is None else max(0,ordered-refused), status,
            _money(item.get('price'))))
    return OrderReadback('partkom',ref,_outcome(lines),tuple(lines),'basket/motion/{reference}')


def _forum(raw: Any, ref: str) -> OrderReadback:
    if isinstance(raw, dict) and 'did' not in raw:
        raise ReadbackError('supplier_read_failed')
    entries = _rows(raw)
    matching = [x for x in entries if str(x.get('did')) == ref]
    if len(matching) > 1:
        raise ReadbackError('ambiguous_order_identity')
    lines = []
    canceled = False
    if matching:
        order = matching[0]
        state_id = _integer(order.get('statusId'))
        states = {0:'canceled',10:'pending',20:'processing',30:'supplier_confirmed_observed',
                  50:'processing',90:'pickup_ready_not_delivered',100:'issued_not_courier_proven'}
        state = states.get(state_id,'unknown')
        canceled = state == 'canceled'
        for item in _rows(order.get('arTovs')):
            lines.append(OrderLineObservation(_str(item.get('brand')), _str(item.get('nr')),
                _integer(item.get('k1')), (_integer(item.get('k3')) if state == 'issued_not_courier_proven' else None),state,
                _money(item.get('price'))))
    outcome=_outcome(lines,canceled=canceled)
    if matching and outcome=='not_found_in_response':
        outcome='manual_review'
    return OrderReadback('forum-auto',ref,outcome,tuple(lines),'listOrders')


def normalize_order(code: str, reference: str, raw: Any) -> OrderReadback:
    ref = validate_reference(code, reference)
    return {'rossko':_rossko,'adeo':_adeo,'partkom':_partkom,'forum-auto':_forum}[code](raw,ref)
