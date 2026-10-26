"""Read-only ROSSKO GetCheckoutDetails requisite-id validation.

No person/company labels, tax numbers or raw supplier payloads are logged.
This module never calls GetCheckout or alters supplier account settings.
"""
from __future__ import annotations


class RosskoRequisiteError(ValueError):
    """Only safe, developer-authored messages may appear in exceptions."""


def _root(response: object) -> dict:
    if not isinstance(response, dict):
        raise RosskoRequisiteError('Unexpected ROSSKO GetCheckoutDetails response')
    obj = response.get('CheckoutDetailsResult', response)
    if (not isinstance(obj, dict) or
        str(obj.get('success', obj.get('Success', ''))).lower() not in ('true', '1')):
        raise RosskoRequisiteError('ROSSKO GetCheckoutDetails did not confirm success')
    return obj


def live_requisite_ids(response: object) -> list[str]:
    """Extract only positive integer company IDs from the CURRENT API response.

    CompanyList.company may be an object or a list in Zeep's serialized output.
    Missing/invalid CompanyList means no verified IDs, not a default ID.
    """
    root = _root(response)
    companies = root.get('CompanyList') or {}
    if not isinstance(companies, dict):
        return []
    rows = companies.get('company') or []
    if not isinstance(rows, list):
        rows = [rows]
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get('id')
        if isinstance(raw, bool):
            continue
        value = str(raw).strip() if raw is not None else ''
        if not value.isascii() or not value.isdecimal() or len(value) > 18:
            continue
        number = int(value)
        if number > 0:
            ids.add(str(number))
    return sorted(ids, key=int)


def require_live_requisite(response: object, requisite_id: object) -> str:
    """Fail closed if requisite is missing or is not offered by THIS account."""
    value = str(requisite_id or '').strip()
    if (not value or not value.isascii() or not value.isdecimal() or
        len(value) > 18 or int(value) <= 0):
        raise RosskoRequisiteError('ROSSKO payment.requisite_id is required')
    normalized = str(int(value))
    if normalized not in live_requisite_ids(response):
        raise RosskoRequisiteError('Selected requisite_id not present in live ROSSKO CompanyList')
    return normalized


def make_payment(payment_id: object, requisite_id: object) -> dict[str, str]:
    """Construct only the requisite-bearing payment fragment (no dispatch)."""
    payment = str(payment_id or '').strip()
    requisite = str(requisite_id or '').strip()
    if not payment.isascii() or not payment.isdecimal() or int(payment) <= 0:
        raise RosskoRequisiteError('ROSSKO payment_id must be a positive integer')
    if not requisite.isascii() or not requisite.isdecimal() or int(requisite) <= 0:
        raise RosskoRequisiteError('ROSSKO payment.requisite_id is required')
    return {'payment_id': str(int(payment)), 'requisite_id': str(int(requisite))}


def resolve_live_requisite(response: object, stored_requisite_id: object = None,
                           supplied_requisite_id: object = None) -> str:
    """Select the account's ROSSKO company strictly from live API evidence.

    A persisted destination binding takes precedence. Explicit caller values
    cannot override it. With no binding, a single live company is unambiguous.
    Multiple companies without a binding MUST fail closed, never choose first.
    """
    stored = str(stored_requisite_id or '').strip()
    supplied = str(supplied_requisite_id or '').strip()
    if stored and supplied and stored != supplied:
        raise RosskoRequisiteError('Supplier destination requisite differs from requested company')
    chosen = stored or supplied
    if chosen:
        return require_live_requisite(response, chosen)
    candidates = live_requisite_ids(response)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RosskoRequisiteError('Supplier API has not returned any valid company requisite')
    raise RosskoRequisiteError('Multiple ROSSKO companies: bind external_requisite_id to the supplier destination')
