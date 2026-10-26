"""One explicitly approved ROSSKO test attempt with validated contact and SOAP XML.

CAUTION: GetCheckout IS an external order-creation request. A real order can
be created if the ROSSKO account's portal test mode is disabled. The program
NEVER silently retries. It preserves all previous attempt journals.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.marketplace.receiving_contact import receiving_contact_for_destination_ids
from app.modules.marketplace.rossko_checkout_payload import build_rossko_checkout_payload
from app.modules.marketplace.receiving_contact import ReceivingContact
from uuid import uuid4
from app.core.config import get_settings
from app.modules.marketplace.rossko_requisites import make_payment
from scripts.rossko_test_checkout_lab import (
    JOURNAL_DIR, EXPECTED_BRAND, EXPECTED_ARTICLE, EXPECTED_QTY,
    DEFAULT_STOCK, MAX_TEST_PRICE, COURIER_DELIVERY_ID,
    live_quote, soap_request, normalize_checkout, write_initial,
    replace_journal, now_iso, safe_order_summary,
)
from scripts.rossko_test_order_audit import parse_recent_orders
from scripts.rossko_test_requisite_confirm import (
    final_journal_path, checkout_messages,
)

CONFIRM = 'I_CONFIRM_ONE_TEST_CHECKOUT_WITH_FULL_RECIPIENT_CONTACT'
# Explicit stock authorized for this isolated test. Never auto-switch supplier warehouse.
AUTHORIZED_STOCK = 'HST181'


def attempt_path(account_id: str, destination_id: str) -> Path:
    token = hashlib.sha256(f'{account_id}:{destination_id}:{EXPECTED_BRAND}:{EXPECTED_ARTICLE}'.encode()).hexdigest()[:20]
    return JOURNAL_DIR / f'{token}-contact-verified-fourth-attempt.json'


def validate_recipient(full_name: str, phone: str) -> tuple[str, str]:
    """Reject incomplete names and normalize RU phones without printing them."""
    full_name = ' '.join(full_name.split())
    words = full_name.split(' ')
    if not 5 <= len(full_name) <= 120 or len(words) < 2 or len(words) > 4:
        raise ValueError('Expected full recipient name (surname + given name, patronymic if any)')
    if not all(len(word) >= 2 and word.replace('-', '').isalpha() for word in words):
        raise ValueError('Recipient name contains invalid or incomplete words')
    digits = ''.join(re.findall(r'\d', phone))
    if len(digits) != 11 or digits[0] not in ('7', '8'):
        raise ValueError('Expected valid 11-digit Russian contact number')
    return full_name, '+7' + digits[1:]


def require_previous_contact_rejection(account_id: str, destination_id: str) -> None:
    p = final_journal_path(account_id, destination_id)
    if not p.is_file():
        raise ValueError('The prior attempt journal is missing')
    record = json.loads(p.read_text(encoding='utf-8'))
    if (record.get('account_id') != account_id or
        record.get('destination_id') != destination_id or
        record.get('state') != 'supplier_rejected' or record.get('order_ids')):
        raise ValueError('Prior attempt is not documented as rejected without order ID')
    texts = ' '.join(str(x) for x in (record.get('redacted_supplier_messages') or []))
    if not ('контактн' in texts.lower() and ('не указан' in texts.lower() or 'отсутств' in texts.lower())):
        raise ValueError('Prior rejection does not confirm missing contact information')


def build_payload(cfg: Any, delivery: str, address: str, requisite: str, offer: Any,
                  full_name: str, phone: str, *, comment: str = '',
                  order_uuid: str) -> dict:
    """Read-only contract builder: test reference is not a buyer's order ID."""
    return build_rossko_checkout_payload(
        keys=(cfg.rossko_key1,cfg.rossko_key2),
        delivery_id=delivery,address_id=address,payment_id='1',
        requisite_id=requisite,
        receiving_contact=ReceivingContact(full_name,phone,comment),
        parts=[{'partnumber':offer.article,'brand':offer.brand,
                'stock':offer.warehouse,'count':EXPECTED_QTY}],
        order_uuid=order_uuid,
    )


def validate_exact_xml(envelope: Any, payload: dict) -> None:
    """Verify the EXACT outgoing contact and order fields in Zeep XML in-memory."""
    from lxml import etree
    nodes = envelope.xpath("//*[local-name()='GetCheckout']")
    if len(nodes) != 1:
        raise ValueError('Unexpected SOAP GetCheckout operation count')
    operation = nodes[0]
    def child(parent: Any, name: str) -> Any:
        rows = [c for c in parent if isinstance(c.tag, str) and etree.QName(c).localname == name]
        if len(rows) != 1:
            raise ValueError(f'SOAP XML missing or duplicates {name}')
        return rows[0]
    for container, fields in (
        ('contact', {'name': payload['contact']['name'], 'phone': payload['contact']['phone']}),
        ('payment', payload['payment']),
        ('delivery', payload['delivery']),
    ):
        parent = child(operation, container)
        for field, expected in fields.items():
            observed = (child(parent, field).text or '')
            # Zeep can serialize integer IDs without leading zeroes; compare
            # documented integer fields numerically, never address IDs.
            if (container, field) in {('delivery', 'delivery_id'), ('payment', 'payment_id'), ('payment', 'requisite_id')}:
                equivalent = observed.isascii() and observed.isdecimal() and int(observed) == int(expected)
            else:
                equivalent = observed == str(expected)
            if not equivalent:
                raise ValueError(f'SOAP XML differs from intended {container}.{field}')
    parts = child(operation, 'PARTS')
    part = child(parts, 'Part')
    for field, expected in payload['PARTS']['Part'][0].items():
        if (child(part, field).text or '') != str(expected):
            raise ValueError(f'SOAP XML differs from intended parts.{field}')
    if (child(operation, 'delivery_parts').text or '').lower() not in ('false', '0'):
        raise ValueError('Unexpected delivery_parts in SOAP XML')


async def submit(args) -> int:
    if (args.confirm != CONFIRM or not args.portal_test_enabled or
        not args.accept_duplicate_risk or not args.acknowledge_unknown_courier_terms):
        print('BLOCKED: missing explicit safety acknowledgements')
        return 2
    settings = get_settings()
    if settings.app_env not in ('development', 'local') or not settings.supplier_api_enabled:
        print('BLOCKED: development/local and supplier read API are required')
        return 2
    if not (args.stock == AUTHORIZED_STOCK and str(args.payment_id) == '1'):
        print('BLOCKED: only explicitly authorized warehouse HST181 and payment=1 are allowed')
        return 2
    journal = attempt_path(args.account_id, args.destination_id)
    if journal.exists():
        print('BLOCKED: fourth attempt marker already exists. Do NOT resend.')
        return 2
    attempted = False
    try:
        require_previous_contact_rejection(args.account_id, args.destination_id)
        cfg, delivery, address, offer, requisite = await live_quote(
            args.account_id, args.destination_id, args.stock, args.payment_id)
        if delivery != COURIER_DELIVERY_ID or not address or offer.price > MAX_TEST_PRICE:
            print('BLOCKED: courier route or stock price invalid')
            return 2
        recent = await soap_request('GetOrders', 'GetOrders', {
            'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'limit': 500})
        status, candidates = parse_recent_orders(recent)
        if status != 'ok' or candidates:
            print('BLOCKED: recent orders contain a candidate or GetOrders is inconclusive')
            return 2
        print('READ-ONLY: No matching CF 705 in last 500 ROSSKO orders. This DOES NOT prove nonexistence.')
        print(f'CURRENT TEST OFFER: warehouse={offer.warehouse} quantity={EXPECTED_QTY} price={offer.price} rub, cap={MAX_TEST_PRICE} rub')
        print('CAUTION: Supplier TEST MODE cannot be verified over this API; a REAL order is possible.')
        receiver = await receiving_contact_for_destination_ids(args.account_id, args.destination_id)
        full_name, phone = receiver.name, receiver.phone
        test_order_uuid = str(uuid4())  # isolated test reference; NOT a paid customer order
        payload = build_payload(cfg, delivery, address, requisite, offer, full_name, phone,
                                comment=receiver.comment, order_uuid=test_order_uuid)
        import httpx
        from zeep import AsyncClient
        from zeep.transports import AsyncTransport
        from zeep.helpers import serialize_object
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=4)) as http:
            with httpx.Client(timeout=httpx.Timeout(10, connect=4)) as wsdl_http:
                client = AsyncClient('https://api.rossko.ru/service/v2.1/GetCheckout?wsdl',
                                     transport=AsyncTransport(client=http, wsdl_client=wsdl_http))
                xml = client.create_message(client.service, 'GetCheckout', **payload)
                validate_exact_xml(xml, payload)
                print('EXACT SOAP XML: contact, payment, delivery, part values VERIFIED (not printed).')
                record = {
                    'state': 'dispatch_unknown', 'created_utc': now_iso(),
                    'account_id': args.account_id, 'destination_id': args.destination_id,
                    'mode': 'FOURTH_CONTACT_VERIFIED_EXPLICIT_PORTAL_TEST',
                    'sku': f'{EXPECTED_BRAND} {EXPECTED_ARTICLE}', 'stock': offer.warehouse,
                    'quantity': EXPECTED_QTY, 'price_cap_rub': str(MAX_TEST_PRICE),
                    'internal_test_order_uuid': test_order_uuid,
                    'quote_rub': str(offer.price), 'delivery_id': delivery, 'payment_id': '1',
                    'requisite_id': requisite, 'order_ids': [], 'error_code': None,
                }
                try:
                    write_initial(journal, record)
                except FileExistsError:
                    print('BLOCKED: concurrent attempt marker exists; no order sent')
                    return 2
                attempted = True
                print('FOURTH ATTEMPT MARKER SAVED. Calling GetCheckout once; no retries.')
                try:
                    result = await client.service.GetCheckout(**payload)
                    response = serialize_object(result, target_cls=dict)
                except Exception as exc:
                    record.update(error_code='unknown_soap_or_transport', response_utc=now_iso())
                    try:
                        replace_journal(journal, record)
                    except Exception:
                        pass
                    print('DISPATCH OUTCOME UNKNOWN: ' + type(exc).__name__ + '. Do NOT repeat.')
                    return 3
        state, ids, reason = normalize_checkout(response)
        messages = checkout_messages(response)
        record.update(state=state, order_ids=ids, error_code=reason,
                      response_utc=now_iso(), redacted_supplier_messages=messages)
        try:
            replace_journal(journal, record)
        except Exception:
            print('WARNING: supplier answered but journal update failed; DO NOT repeat.')
            return 3
        print('GETCHECKOUT status=' + state + ' order_ids=' + str(ids) + ' reason=' + str(reason or 'none'))
        for msg in messages:
            print('ROSSKO MESSAGE (redacted): ' + msg)
        print('Do NOT submit again. If order IDs were returned, run reconcile.')
        return 0 if state == 'accepted_unverified' else 3
    except Exception as exc:
        if attempted:
            print('DISPATCH MAY HAVE OCCURRED: ' + type(exc).__name__ + '. Do NOT repeat.')
            return 3
        print('BLOCKED BEFORE DISPATCH: ' + type(exc).__name__ + '. No order sent.')
        return 2


async def reconcile(args) -> int:
    path = attempt_path(args.account_id, args.destination_id)
    if not path.is_file():
        print('No fourth attempt journal. No order lookup made.')
        return 2
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('account_id') != args.account_id or record.get('destination_id') != args.destination_id:
        print('Journal account/destination mismatch; stopped')
        return 2
    ids = record.get('order_ids') or []
    if not ids:
        print('NO ORDER IDS. Do not re-submit; consult the reported ROSSKO message.')
        return 3
    from scripts.rossko_test_checkout_lab import context
    cfg, _, _ = await context(args.account_id, args.destination_id)
    response = await soap_request('GetOrders', 'GetOrders', {
        'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2,
        'order_ids': {'id': ids},
    })
    summary = safe_order_summary(response, ids)
    print('GETORDERS ' + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary.get('status') in ('verified', 'manual_review'):
        record.update(state=summary['status'], reconciliation=summary, reconciliation_utc=now_iso())
        replace_journal(path, record)
    return 0 if summary.get('status') == 'verified' else 3


def main() -> int:
    parser = argparse.ArgumentParser(description='One controlled fourth portal-test attempt for selected warehouse HST181; can create a real order')
    subs = parser.add_subparsers(dest='cmd', required=True)
    for action in ('submit-once', 'reconcile'):
        p = subs.add_parser(action)
        p.add_argument('--account-id', required=True)
        p.add_argument('--destination-id', required=True)
        if action == 'submit-once':
            p.add_argument('--stock', required=True, help='Explicit ROSSKO warehouse; this test authorizes HST181 only')
            p.add_argument('--payment-id', default='1')
            p.add_argument('--portal-test-enabled', action='store_true')
            p.add_argument('--accept-duplicate-risk', action='store_true')
            p.add_argument('--acknowledge-unknown-courier-terms', action='store_true')
            p.add_argument('--confirm', default='')
    args = parser.parse_args()
    return asyncio.run(submit(args) if args.cmd == 'submit-once' else reconcile(args))


if __name__ == '__main__':
    raise SystemExit(main())
