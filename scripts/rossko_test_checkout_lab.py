"""Explicit, one-shot ROSSKO portal-test-mode checkout lab for Autocodex v0.33.0.

NOT used by public APIs, workers or production purchase pipeline.
Portal test-mode status is NOT exposed by ROSSKO's GetCheckoutDetails; enabling
that setting is an independent operator action. A real supplier order may be
created if the portal setting is wrong or has expired. `submit` requires
operator acknowledgement and persists an exclusive attempt BEFORE GetCheckout.
Never retry an ambiguous GetCheckout. Use GetOrders with known IDs or support.

The test journal resides on the named Docker catalog_media volume so it survives
web-container recreation. Do not delete it to try a failed/uncertain request.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.db import get_session_factory
from app.modules.marketplace.receiving_contact import receiving_contact_for_destination_ids
from app.core.config import get_settings
from app.modules.marketplace.account_credentials import for_account
from app.modules.marketplace.account_probe import live_rossko_details, normalize_checkout_details
from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
from app.modules.offer_engine.providers import PROVIDERS, exact
from app.modules.suppliers.models import Supplier
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.marketplace.rossko_requisites import resolve_live_requisite, make_payment
from app.modules.marketplace.supplier_delivery_policy import (
    ROSSKO_COURIER_ID, require_rossko_courier, require_live_rossko_courier,
)

JOURNAL_DIR = Path('/srv/app/media/rossko_test_lab')
EXPECTED_BRAND = 'Trialli'
EXPECTED_ARTICLE = 'CF 705'
EXPECTED_QTY = 1
DEFAULT_STOCK = 'HST282827366'
PICKUP_DELIVERY_ID = '000000001'
COURIER_DELIVERY_ID = '000000002'
MAX_TEST_PRICE = Decimal('492.27')
TEST_CONFIRMATION = 'I_CONFIRM_ROSSKO_PORTAL_TEST_MODE_AND_ONE_TEST_ORDER'
SENTINEL_STATES = frozenset({'dispatch_unknown', 'accepted_unverified', 'verified', 'supplier_rejected', 'manual_review'})

# Only literal, developer-authored messages may be shown to an operator.
# Never print arbitrary API/HTTP/SOAP exception texts: they may include keys,
# personal data or entire supplier payloads.
SAFE_PREFLIGHT_REASONS = frozenset({
    'Supplier API is not enabled',
    'Invalid ROSSKO seller/account/destination binding',
    'ROSSKO keys unavailable for account profile',
    'Configured delivery address is not valid for this delivery method',
    'Configured delivery is not offered by ROSSKO',
    'This test only allows documented bank-transfer payment ID 1',
    'ROSSKO supplier purchasing only supports courier delivery 000000002',
    'ROSSKO courier delivery requires a configured address ID',
    'Configured delivery address is not present in ROSSKO checkout details',
    'Exact stock offer absent, changed or ambiguous (no order sent)',
    'Unexpected SOAP result',
    'Supplier rejected checkout detail lookup',
    'ROSSKO supplier purchasing only supports courier delivery 000000002',
    'ROSSKO courier delivery is not currently offered',
    'Configured ROSSKO courier address was not returned by supplier',
    'Configured address is not eligible for ROSSKO courier delivery',
    'Configured ROSSKO supplier payment method is unavailable',
})


def format_preview_failure(exc: Exception) -> str:
    """Allowlisted diagnostics only; never expose supplier/credential payloads."""
    reason = str(exc)
    if isinstance(exc, (RuntimeError, ValueError)) and reason in SAFE_PREFLIGHT_REASONS:
        return 'READ-ONLY PREFLIGHT BLOCKED: ' + reason
    kind = type(exc).__name__
    safe_kinds = {'Unavailable', 'ConnectTimeout', 'ReadTimeout', 'ConnectError',
                  'HTTPStatusError', 'Fault', 'ValidationError', 'TypeError'}
    if kind in safe_kinds:
        return 'READ-ONLY PREFLIGHT FAILED: ' + kind + ' (no order sent)'
    return 'READ-ONLY PREFLIGHT FAILED: unexpected supplier/database response (no order sent)'



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_number(value: Any) -> Decimal | None:
    try:
        n = Decimal(str(value).replace(',', '.'))
        return n if n.is_finite() else None
    except (TypeError, InvalidOperation):
        return None


def classify_supplier_message(value: Any) -> str:
    """Map potentially sensitive supplier text to a fixed, non-sensitive code.

    Never log or persist the raw error message: supplier messages may contain
    buyer contacts, addresses, account identifiers or credentials.
    """
    if not isinstance(value, str) or not value.strip():
        return 'message_missing'
    message = value[:4096].casefold()
    if (any(term in message for term in ('минималь', 'minimum', 'minimal')) and
            any(term in message for term in ('сумм', 'стоимост', 'заказ', 'amount', 'total', 'order'))):
        return 'courier_minimum'
    if any(term in message for term in ('адрес', 'достав', 'курьер', 'delivery', 'courier', 'address')):
        return 'delivery_or_address'
    if any(term in message for term in ('реквизит', 'оплат', 'платеж', 'платёж', 'payment', 'requisite')):
        return 'payment'
    if any(term in message for term in ('налич', 'остат', 'склад', 'stock', 'availab')):
        return 'stock'
    if any(term in message for term in ('цен', 'price')):
        return 'price'
    if any(term in message for term in ('авторизац', 'ключ', 'auth', 'credential')):
        return 'authentication'
    if any(term in message for term in ('лимит', 'limit')):
        return 'supplier_limit'
    return 'unclassified'


def normalize_checkout(response: dict) -> tuple[str, list[int], str | None]:
    """Return fail-closed status and order IDs; never treat HTTP 200 as an order."""
    if not isinstance(response, dict):
        return 'manual_review', [], 'invalid_soap_response'
    obj = response.get('CheckoutResult', response)
    if not isinstance(obj, dict):
        return 'manual_review', [], 'invalid_soap_result'
    raw_success = obj.get('success', obj.get('Success'))
    if raw_success is None:
        return 'manual_review', [], 'supplier_success_flag_missing'
    if str(raw_success).lower() not in ('true', 'false', '1', '0'):
        return 'manual_review', [], 'supplier_success_flag_invalid'
    success = str(raw_success).lower() in ('true', '1')
    raw_ids = (obj.get('OrderIDS') or {})
    if not isinstance(raw_ids, dict):
        return 'manual_review', [], 'malformed_order_ids'
    ids = raw_ids.get('id') or []
    if not isinstance(ids, list):
        ids = [ids]
    try:
        order_ids = list(dict.fromkeys(int(i) for i in ids if str(i).strip()))
    except (TypeError, ValueError):
        return 'manual_review', [], 'malformed_order_ids'
    if not all(i > 0 for i in order_ids) or len(order_ids) > 20:
        return 'manual_review', [], 'malformed_order_ids'
    errors = obj.get('ItemsErrorList') or {}
    if errors and (not isinstance(errors, dict) or errors.get('ItemError')):
        return 'manual_review', order_ids, 'partial_item_failure'
    if success and order_ids:
        return 'accepted_unverified', order_ids, None
    if order_ids:
        return 'manual_review', order_ids, 'ids_without_success'
    if not success:
        message_code = classify_supplier_message(obj.get('message', obj.get('Message')))
        return 'supplier_rejected', [], 'supplier_rejected_' + message_code
    return 'manual_review', [], 'supplier_no_confirmed_order_ids'


def safe_order_summary(raw: dict, order_ids: list[int]) -> dict:
    """Scrub names, phone, address and payment details; verify only exact SKU."""
    if not isinstance(raw, dict):
        return {'status': 'manual_review', 'reason': 'invalid_orders_result'}
    data = raw.get('OrdersResult', raw)
    if not isinstance(data, dict) or str(data.get('success', data.get('Success', ''))).lower() not in ('true', '1'):
        return {'status': 'manual_review', 'reason': 'supplier_orders_lookup_failed'}
    outer = data.get('OrdersList') or {}
    if not isinstance(outer, dict):
        return {'status': 'manual_review', 'reason': 'invalid_orders_list'}
    orders = outer.get('Order') or []
    if not isinstance(orders, list):
        orders = [orders]
    found = {}
    for o in orders:
        if not isinstance(o, dict):
            continue
        try:
            oid = int(o.get('id'))
        except (TypeError, ValueError):
            continue
        if oid in order_ids:
            found[oid] = o
    if not found:
        return {'status': 'pending', 'reason': 'order_not_visible_yet', 'found_order_ids': []}
    if len(found) != len(order_ids):
        return {'status': 'pending', 'reason': 'not_all_orders_visible', 'found_order_ids': sorted(found)}
    matched_qty = 0
    other_items = 0
    prices = []
    for o in found.values():
        parts = o.get('parts') or {}
        if not isinstance(parts, dict):
            return {'status': 'manual_review', 'reason': 'invalid_order_parts'}
        entries = parts.get('part') or []
        if not isinstance(entries, list):
            entries = [entries]
        if not entries:
            return {'status': 'pending', 'reason': 'orders_without_parts'}
        for p in entries:
            if not isinstance(p, dict):
                return {'status': 'manual_review', 'reason': 'invalid_order_item'}
            try:
                qty = int(p.get('count'))
            except (TypeError, ValueError):
                return {'status': 'manual_review', 'reason': 'invalid_order_quantity'}
            if qty < 1:
                return {'status': 'manual_review', 'reason': 'invalid_order_quantity'}
            if (normalize_brand(str(p.get('brand', ''))) == normalize_brand(EXPECTED_BRAND) and
                normalize_article(str(p.get('partnumber', ''))) == normalize_article(EXPECTED_ARTICLE)):
                matched_qty += qty
                prices.append(safe_number(p.get('price')))
            else:
                other_items += 1
    if other_items or matched_qty != EXPECTED_QTY or not prices or any(p is None or p > MAX_TEST_PRICE for p in prices):
        return {'status': 'manual_review', 'reason': 'order_contents_do_not_match_authorized_test',
                'found_order_ids': sorted(found)}
    return {'status': 'verified', 'reason': 'supplier_orders_match',
            'found_order_ids': sorted(found), 'item_quantity': matched_qty}


def journal_path(account_id: str, destination_id: str) -> Path:
    date = datetime.now(timezone.utc).strftime('%Y%m%d')
    key = hashlib.sha256(f'{account_id}:{destination_id}:{EXPECTED_BRAND}:{EXPECTED_ARTICLE}'.encode()).hexdigest()[:20]
    return JOURNAL_DIR / f'{date}-{key}.json'


def write_initial(path: Path, record: dict) -> None:
    """Exclusive durable marker prevents concurrent and ambiguous re-dispatch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(record, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())


def replace_journal(path: Path, record: dict) -> None:
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(record, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def latest_journal(account_id: str, destination_id: str) -> Path | None:
    key = hashlib.sha256(f'{account_id}:{destination_id}:{EXPECTED_BRAND}:{EXPECTED_ARTICLE}'.encode()).hexdigest()[:20]
    candidates = sorted(JOURNAL_DIR.glob(f'*-{key}.json'), reverse=True) if JOURNAL_DIR.exists() else []
    return candidates[0] if candidates else None


async def context(account_id: str, destination_id: str):
    if not get_settings().supplier_api_enabled:
        raise RuntimeError('Supplier API is not enabled')
    async with get_session_factory()() as db:
        account = await db.get(SupplierAccount, UUID(account_id))
        dest = await db.get(SupplierDestination, UUID(destination_id))
        vendor = await db.get(Supplier, account.supplier_id) if account else None
        node = await db.get(FulfillmentNode, dest.node_id) if dest else None
        if (not account or not vendor or vendor.code != 'rossko' or not dest or
            dest.account_id != account.id or not account.enabled or not account.read_enabled or
            not dest.enabled or not dest.verified or not node or not node.active or
            node.merchant_id != account.merchant_id):
            raise RuntimeError('Invalid ROSSKO seller/account/destination binding')
        cfg = for_account('rossko', account, dest)
        if not (cfg.rossko_key1 and cfg.rossko_key2):
            raise RuntimeError('ROSSKO keys unavailable for account profile')
        delivery_id = dest.external_delivery_id
        address_id = dest.external_address_id
    return cfg, delivery_id, address_id


def validate_test_delivery(delivery_id: str | None, address_id: str | None,
                           addresses: list[dict]) -> str:
    """Strictly validate already-configured supplier delivery; never invent addresses.

    ROSSKO courier delivery 000000002 requires address_id. For any supplied
    address the ID must appear in live GetCheckoutDetails and, if present,
    the allowed delivery method list must contain the configured method.
    """
    delivery_id = str(delivery_id or '')
    require_rossko_courier(delivery_id, address_id)
    if address_id:
        matched = next((a for a in addresses if a.get('id') == str(address_id)), None)
        if matched is None:
            raise RuntimeError('Configured delivery address is not present in ROSSKO checkout details')
        allowed = matched.get('allowed_delivery_ids') or []
        if allowed and delivery_id not in allowed:
            raise RuntimeError('Configured delivery address is not valid for this delivery method')
    return delivery_id


async def live_quote(account_id: str, destination_id: str, stock: str, payment_id: str):
    cfg, delivery_id, address_id = await context(account_id, destination_id)
    raw_details = await live_rossko_details(cfg)
    details = normalize_checkout_details(raw_details)
    requisite_id = resolve_live_requisite(
        raw_details, getattr(cfg, 'rossko_requisite_id', None))
    delivery_id = validate_test_delivery(delivery_id, address_id, details['addresses'])
    if str(payment_id) != '1':
        raise RuntimeError('This test only allows documented bank-transfer payment ID 1')
    require_live_rossko_courier(delivery_id, address_id, details, payment_id=payment_id)
    offers = await PROVIDERS['rossko'].search(EXPECTED_BRAND, EXPECTED_ARTICLE, cfg)
    matches = [o for o in offers if exact(o, EXPECTED_BRAND, EXPECTED_ARTICLE) and o.warehouse == stock and
               o.qty is not None and o.qty >= EXPECTED_QTY and o.minimum <= EXPECTED_QTY and
               EXPECTED_QTY % o.multiple == 0 and o.price > 0 and o.price <= MAX_TEST_PRICE]
    if len(matches) != 1:
        raise RuntimeError('Exact stock offer absent, changed or ambiguous (no order sent)')
    return cfg, delivery_id, address_id, matches[0], requisite_id


async def soap_request(endpoint: str, service: str, payload: dict) -> dict:
    import httpx
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from zeep.helpers import serialize_object
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=4)) as http:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4)) as wsdl_http:
            client = AsyncClient(f'https://api.rossko.ru/service/v2.1/{endpoint}?wsdl',
                                 transport=AsyncTransport(client=http, wsdl_client=wsdl_http))
            result = await getattr(client.service, service)(**payload)
    return serialize_object(result, target_cls=dict)


async def preview(args) -> int:
    _, delivery_id, _, offer, requisite_id = await live_quote(args.account_id, args.destination_id, args.stock, args.payment_id)
    print('READ-ONLY TEST CHECKOUT PREVIEW; NOT SENT')
    print(f'product={EXPECTED_BRAND} {EXPECTED_ARTICLE} count=1 stock={offer.warehouse} purchase_rub={offer.price}')
    print(f'delivery_id={delivery_id} payment_id={args.payment_id} requisite_id={requisite_id} max_price_rub={MAX_TEST_PRICE}')
    print('COURIER ONLY: configured address matched live ROSSKO GetCheckoutDetails (address hidden).')
    print('COURIER: supplier minimum order amount and possible delivery charges are UNKNOWN here.')
    print('COURIER: do NOT submit unless supplier portal terms are independently confirmed.')
    print('ROSSKO portal test-mode status cannot be checked via the API; user must verify it in portal.')
    print('A submission will remain restricted to this SKU, one piece, exact stock and price cap.')
    return 0


async def submit(args) -> int:
    if args.confirm != TEST_CONFIRMATION or not args.portal_test_enabled:
        print('BLOCKED: no explicit acknowledgement of supplier TEST mode and one write.')
        return 2
    if get_settings().app_env not in ('development', 'local'):
        print('BLOCKED: test lab can only operate in a local development environment.')
        return 2
    # Validate the latest quote BEFORE asking for contact and before marking an attempt.
    cfg, delivery_id, address_id, offer, requisite_id = await live_quote(args.account_id, args.destination_id, args.stock, args.payment_id)
    if delivery_id == COURIER_DELIVERY_ID:
        # Courier minimum and fees are branch/contract-specific; they are not
        # returned by GetCheckoutDetails. Never infer them from the stock quote.
        supplier_minimum = safe_number(args.courier_minimum_rub)
        if (not args.courier_terms_confirmed or supplier_minimum is None or
            supplier_minimum <= 0 or supplier_minimum > offer.price):
            print('BLOCKED: ROSSKO courier minimum/terms not confirmed for this destination.')
            print('Check the ROSSKO portal: courier minimum must be <= current test order amount.')
            return 2
    path = journal_path(args.account_id, args.destination_id)
    if latest_journal(args.account_id, args.destination_id):
        print('BLOCKED: a prior test attempt is recorded for this account/destination/SKU; do not resend.')
        return 2
    print('TEST MODE CHECK: Portal test mode must still be ON. Any supplier checkout can create an order.')
    receiver = await receiving_contact_for_destination_ids(args.account_id, args.destination_id)
    name, phone = receiver.name, receiver.phone
    record = {'state': 'dispatch_unknown', 'created_utc': now_iso(), 'account_id': args.account_id,
              'destination_id': args.destination_id, 'sku': f'{EXPECTED_BRAND} {EXPECTED_ARTICLE}',
              'quantity': EXPECTED_QTY, 'stock': offer.warehouse, 'quote_rub': str(offer.price),
              'ceiling_rub': str(MAX_TEST_PRICE), 'delivery_id': delivery_id,
              'payment_id': str(args.payment_id), 'order_ids': [],
              'mode': 'operator_confirmed_supplier_portal_test_mode', 'error_code': None}
    try:
        write_initial(path, record)
    except FileExistsError:
        print('BLOCKED: test attempt exists; no repeat permitted.')
        return 2
    # No additional validation calls once the external-write fence is committed.
    payload = {
        'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2,
        'delivery': {'delivery_id': delivery_id},
        'payment': make_payment(args.payment_id, requisite_id),
        'contact': {'name': name, 'phone': phone, 'comment': 'AUTO_CODEX_PORTAL_TEST_MODE_ONE_PIECE'},
        'delivery_parts': False,
        'PARTS': {'Part': [{'partnumber': offer.article, 'brand': offer.brand,
                            'stock': offer.warehouse, 'count': EXPECTED_QTY}]},
    }
    if address_id:
        payload['delivery']['address_id'] = address_id
    print('DISPATCH MARKER SAVED: one test GetCheckout attempt; no retries, even on timeout.')
    try:
        raw = await soap_request('GetCheckout', 'GetCheckout', payload)
        state, ids, reason = normalize_checkout(raw)
        record.update(state=state, order_ids=ids, error_code=reason, response_utc=now_iso())
        replace_journal(path, record)
        print(f'GETCHECKOUT status={state} order_ids={ids} reason={reason or "none"}')
        if ids:
            print('Next: run reconcile command (read-only GetOrders).')
        elif state != 'supplier_rejected':
            print('UNKNOWN/MANUAL REVIEW: no automatic retry. Reconcile with ROSSKO support.')
        return 0 if state == 'accepted_unverified' else 3
    except Exception:
        # A SOAP fault or timeout after attempting delivery is ambiguous: never retry.
        record.update(error_code='network_or_supplier_response_ambiguous', response_utc=now_iso())
        try:
            replace_journal(path, record)
        except Exception:
            pass
        print('DISPATCH UNKNOWN: supplier may have created order. Do NOT repeat GetCheckout.')
        print('Contact ROSSKO support / inspect test orders using the local portal. No secrets logged.')
        return 3


async def reconcile(args) -> int:
    path = latest_journal(args.account_id, args.destination_id)
    if not path:
        print('No prior test journal found. No GetOrders call made.')
        return 2
    record = json.loads(path.read_text())
    if record.get('account_id') != args.account_id or record.get('destination_id') != args.destination_id:
        print('BLOCKED: journal identity mismatch.')
        return 2
    ids = record.get('order_ids') or []
    if not ids:
        print(f'NO IDs: prior attempt status={record.get("state")}. Do not repeat checkout.')
        return 3
    cfg, _, _ = await context(args.account_id, args.destination_id)
    raw = await soap_request('GetOrders', 'GetOrders', {
        'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'order_ids': {'id': ids},
    })
    summary = safe_order_summary(raw, ids)
    # Persistent records are inspectable; never overwrite a previously verified status with pending.
    if summary['status'] == 'verified':
        record.update(state='verified', reconciliation_utc=now_iso(), reconciliation=summary)
        replace_journal(path, record)
    elif summary['status'] == 'manual_review':
        record.update(state='manual_review', reconciliation_utc=now_iso(), reconciliation=summary)
        replace_journal(path, record)
    print('GETORDERS ' + json.dumps(summary, ensure_ascii=False))
    return 0 if summary['status'] == 'verified' else 3


async def main():
    parser = argparse.ArgumentParser(description='One-time supplier-portal TEST mode ROSSKO checkout laboratory.')
    sub = parser.add_subparsers(dest='action', required=True)
    for command in ('preview', 'submit', 'reconcile'):
        p = sub.add_parser(command)
        p.add_argument('--account-id', required=True)
        p.add_argument('--destination-id', required=True)
        if command != 'reconcile':
            p.add_argument('--stock', default=DEFAULT_STOCK)
            p.add_argument('--payment-id', default='1')
        if command == 'submit':
            p.add_argument('--portal-test-enabled', action='store_true')
            p.add_argument('--confirm', default='')
            # Additional guard only for courier deliveries. Values are checked
            # against the live quote; no change to configured supplier address.
            p.add_argument('--courier-terms-confirmed', action='store_true')
            p.add_argument('--courier-minimum-rub', default=None)
    args = parser.parse_args()
    if args.action == 'submit':
        print('DISABLED: legacy checkout submit bypasses the receiving-node order UUID contract. No order sent.')
        return 2
    try:
        return await {'preview': preview, 'reconcile': reconcile}[args.action](args)
    except Exception as exc:
        if args.action == 'preview':
            print(format_preview_failure(exc))
            print('Do not change destination/delivery settings without verifying the real supplier route.')
        else:
            # Submit/reconcile failures are intentionally opaque: a supplier-side
            # write can have an unknown result. Never print external exception text.
            print('CHECK FAILED: no automatic retry; inspect the protected attempt journal.')
        return 2


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
