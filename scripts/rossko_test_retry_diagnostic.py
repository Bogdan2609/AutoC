"""Single explicitly approved diagnostic RETRY of a rejected ROSSKO portal-test checkout.

Only for a previously logged supplier_rejected attempt with no order IDs. The
original journal remains untouched. This tool makes at most one further
GetCheckout request, in an independently recorded attempt. It cannot determine
ROSKKO's portal test-mode flag; if it is off, the request may create a REAL order.

Offline tests do not contact ROSSKO. Never run this from a public endpoint or
worker, or use it as a production retry mechanism.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.marketplace.receiving_contact import receiving_contact_for_destination_ids
from app.core.config import get_settings
from scripts.rossko_test_checkout_lab import (
    JOURNAL_DIR, EXPECTED_BRAND, EXPECTED_ARTICLE, EXPECTED_QTY,
    MAX_TEST_PRICE, COURIER_DELIVERY_ID, context, live_quote,
    soap_request, latest_journal, normalize_checkout, safe_number,
    write_initial, replace_journal, now_iso, safe_order_summary,
)
from scripts.rossko_test_order_audit import parse_recent_orders
from app.modules.marketplace.rossko_requisites import make_payment

CONFIRM = 'I_ACCEPT_ONE_MORE_ROSSKO_TEST_ORDER_AND_DUPLICATE_RISK'


def retry_journal_path(account_id: str, destination_id: str) -> Path:
    identity = f'{account_id}:{destination_id}:{EXPECTED_BRAND}:{EXPECTED_ARTICLE}'
    token = hashlib.sha256(identity.encode()).hexdigest()[:20]
    # A single permanent retry marker: not reset by changing day or container.
    return JOURNAL_DIR / f'{token}-one-diagnostic-retry.json'


def redact_supplier_message(raw: Any) -> str:
    """Provide a useful provider message, not raw SOAP or sensitive contacts.

    Redaction is best-effort; never log original payloads or URLs. Operator
    should review the console output before sharing it elsewhere.
    """
    if not isinstance(raw, str) or not raw.strip():
        return '[message отсутствует]'
    s = html.unescape(raw)
    s = re.sub(r'<[^>]{0,200}>', ' ', s)
    s = re.sub(r'[\x00-\x1f\x7f]+', ' ', s)
    s = re.sub(r'(?i)\b(?:KEY1|KEY2|api[_ -]?key|secret|password|token|authorization)\s*[:=]\s*[^\s,;]+', '[секрет скрыт]', s)
    s = re.sub(r'(?i)\b[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[email скрыт]', s)
    s = re.sub(r'(?<!\d)(?:\+7|7|8)[\d\s()\-]{9,21}(?!\d)', '[телефон скрыт]', s)
    s = re.sub(r'\b\d[\d\-]{8,}\d\b', '[номер скрыт]', s)
    s = re.sub(r'(?i)(?:по адресу|адрес\s*[:=]|улица\s+|ул[.]\s*|проспект\s+|квартира\s+|кв[.]\s*)[^,;]{1,90}', '[адрес скрыт]', s)
    s = re.sub(r'\s+', ' ', s).strip()
    # Do not display a long blob that might include names and full addresses.
    return s[:320] + ('…' if len(s) > 320 else '')


def checkout_messages(payload: Any) -> list[str]:
    """Extract root and per-item errors from documented ROSSKO SOAP response."""
    if not isinstance(payload, dict):
        return ['[ответ SOAP имеет неожиданный формат]']
    root = payload.get('CheckoutResult', payload)
    if not isinstance(root, dict):
        return ['[CheckoutResult имеет неожиданный формат]']
    messages = [redact_supplier_message(root.get('message', root.get('Message')))]
    items = root.get('ItemsErrorList') or {}
    if isinstance(items, dict):
        items = items.get('ItemError') or []
    if not isinstance(items, list):
        items = [items]
    for item in items[:3]:
        if isinstance(item, dict):
            messages.append('Позиция: ' + redact_supplier_message(item.get('message', item.get('Message'))))
    return messages


def load_previous(account_id: str, destination_id: str) -> dict:
    original = latest_journal(account_id, destination_id)
    if not original or not original.is_file():
        raise ValueError('Отсутствует исходная попытка; повтор заблокирован')
    record = json.loads(original.read_text(encoding='utf-8'))
    if record.get('account_id') != account_id or record.get('destination_id') != destination_id:
        raise ValueError('Несовпадение аккаунта или направления в журнале')
    if record.get('state') != 'supplier_rejected' or record.get('order_ids'):
        raise ValueError('Исходная попытка не является однозначно отклонённой без ID')
    return record


async def attempt(args) -> int:
    if args.confirm != CONFIRM or not args.portal_test_enabled or not args.accept_duplicate_risk:
        print('BLOCKED: требуется явное подтверждение тестового режима и риска дубликата')
        return 2
    if get_settings().app_env not in ('development', 'local'):
        print('BLOCKED: запуск разрешён только в development/local')
        return 2
    path = retry_journal_path(args.account_id, args.destination_id)
    dispatch_record_created = False
    if path.exists():
        print('BLOCKED: повторная диагностическая попытка уже зарегистрирована; повтор запрещён')
        return 2
    try:
        old = load_previous(args.account_id, args.destination_id)
        cfg, delivery_id, address_id, offer, requisite_id = await live_quote(
            args.account_id, args.destination_id, args.stock, args.payment_id,
        )
        if delivery_id != COURIER_DELIVERY_ID or not address_id:
            print('BLOCKED: нет подтверждённой курьерской доставки ROSSKO')
            return 2
        min_rub = safe_number(args.courier_minimum_rub) if args.courier_minimum_rub is not None else None
        if not args.courier_minimum_unknown:
            if min_rub is None or min_rub < 0 or min_rub > offer.price:
                print('BLOCKED: некорректная или превышающая цену сумма минимума курьера')
                return 2
        else:
            if args.courier_minimum_rub is not None:
                print('BLOCKED: нельзя одновременно указывать неизвестный и числовой минимум')
                return 2
            print('ВНИМАНИЕ: минимум и тариф курьера НЕ подтверждены. Проверка может получить отказ ROSSKO.')
        if not get_settings().supplier_api_enabled:
            print('BLOCKED: API поставщика отключён')
            return 2
        # Independent order audit immediately prior to any write. No retry if
        # response is erroneous, incomplete, or the SKU appears in recent orders.
        orders_raw = await soap_request('GetOrders', 'GetOrders', {
            'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'limit': 500,
        })
        status, candidates = parse_recent_orders(orders_raw)
        if status != 'ok':
            print(f'BLOCKED: GetOrders не подтверждён (category={status})')
            return 2
        if candidates:
            print('BLOCKED: GetOrders обнаружил совпадение SKU; требуется ручная сверка')
            return 2
        print('READ-ONLY CHECK: CF 705 не найден среди последних 500 заказов; это НЕ доказывает отсутствие первого заказа.')
        print('Проверь: ТЕСТОВЫЙ режим включён в личном кабинете ROSSKO прямо сейчас.')
        receiver = await receiving_contact_for_destination_ids(args.account_id, args.destination_id)
        name, phone = receiver.name, receiver.phone
        record = {
            'state': 'dispatch_unknown', 'created_utc': now_iso(),
            'account_id': args.account_id, 'destination_id': args.destination_id,
            'mode': 'SECOND_EXPLICIT_PORTAL_TEST_ATTEMPT',
            'previous_attempt_utc': old.get('created_utc'),
            'previous_attempt_state': old.get('state'),
            'sku': f'{EXPECTED_BRAND} {EXPECTED_ARTICLE}',
            'stock': offer.warehouse, 'quantity': EXPECTED_QTY,
            'quote_rub': str(offer.price), 'max_rub': str(MAX_TEST_PRICE),
            'delivery_id': delivery_id, 'payment_id': str(args.payment_id),
            'courier_minimum': ('unknown' if args.courier_minimum_unknown else str(min_rub)),
            'order_ids': [], 'error_code': None,
        }
        # Atomic durable write fence. A crash or timeout must not cause a retry.
        try:
            write_initial(path, record)
            dispatch_record_created = True
        except FileExistsError:
            print('BLOCKED: другой процесс зарегистрировал диагностическую попытку')
            return 2
        request = {
            'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2,
            'delivery': {'delivery_id': delivery_id, 'address_id': address_id},
            'payment': make_payment(args.payment_id, requisite_id),
            'contact': {'name': name, 'phone': phone,
                        'comment': 'AUTOCODEX TEST MODE SECOND DIAGNOSTIC ATTEMPT'},
            'delivery_parts': False,
            'PARTS': {'Part': [{'partnumber': offer.article, 'brand': offer.brand,
                                'stock': offer.warehouse, 'count': EXPECTED_QTY}]},
        }
        print('JOURNAL SAVED: ровно один вызов GetCheckout; автоматические повторы запрещены.')
        try:
            response = await soap_request('GetCheckout', 'GetCheckout', request)
        except Exception as exc:
            from zeep.exceptions import Fault
            if isinstance(exc, Fault):
                print('ROSSKO SOAP FAULT: ' + redact_supplier_message(str(exc)))
                record['error_code'] = 'soap_fault_ambiguous'
            else:
                print('NETWORK/SOAP UNKNOWN: ' + type(exc).__name__ + '; заказ мог быть создан')
                record['error_code'] = 'transport_ambiguous'
            record['response_utc'] = now_iso()
            replace_journal(path, record)
            return 3
        state, ids, reason = normalize_checkout(response)
        messages = checkout_messages(response)
        record.update(state=state, order_ids=ids, error_code=reason,
                      response_utc=now_iso(), redacted_supplier_messages=messages)
        try:
            replace_journal(path, record)
        except Exception:
            print('WARNING: ответ получен, но журнал не удалось сохранить; повтор запрещён')
            return 3
        print(f'GETCHECKOUT status={state} order_ids={ids} reason={reason or "none"}')
        for message in messages:
            print('ROSSKO MESSAGE (с маскировкой данных): ' + message)
        if ids:
            print('Получены ID. Выполни read-only reconcile новой попытки.')
        else:
            print('Нет ID. Повторять нельзя — проверь причину сообщения и статус в портале.')
        return 0 if state == 'accepted_unverified' else 3
    except Exception as exc:
        if dispatch_record_created:
            print('DISPATCH RESULT UNKNOWN: ' + type(exc).__name__ + '; журнал уже сохранён, повтор запрещён')
            return 3
        print('BLOCKED/FAILED BEFORE WRITE: ' + type(exc).__name__ + ': используйте диагностику без секретов')
        return 2


async def reconcile(args) -> int:
    path = retry_journal_path(args.account_id, args.destination_id)
    if not path.is_file():
        print('NO DIAGNOSTIC RETRY JOURNAL FOUND')
        return 2
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('account_id') != args.account_id or record.get('destination_id') != args.destination_id:
        print('BLOCKED: journal mismatch')
        return 2
    ids = record.get('order_ids') or []
    if not ids:
        print(f'NO IDS; state={record.get("state")}, error_code={record.get("error_code")}. Do not retry.')
        return 3
    cfg, _, _ = await context(args.account_id, args.destination_id)
    response = await soap_request('GetOrders', 'GetOrders', {
        'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'order_ids': {'id': ids},
    })
    summary = safe_order_summary(response, ids)
    print('GETORDERS ' + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary['status'] in ('verified', 'manual_review'):
        record.update(state=summary['status'], reconciliation=summary, reconciliation_utc=now_iso())
        replace_journal(path, record)
    return 0 if summary['status'] == 'verified' else 3


def main() -> int:
    p = argparse.ArgumentParser(description='ONE diagnostic ROSSKO test-mode reattempt. May create order!')
    commands = p.add_subparsers(dest='command', required=True)
    for name in ('submit-test-retry', 'reconcile'):
        s = commands.add_parser(name)
        s.add_argument('--account-id', required=True)
        s.add_argument('--destination-id', required=True)
        if name == 'submit-test-retry':
            s.add_argument('--stock', default='HST282827366')
            s.add_argument('--payment-id', default='1')
            s.add_argument('--portal-test-enabled', action='store_true')
            s.add_argument('--accept-duplicate-risk', action='store_true')
            s.add_argument('--courier-minimum-unknown', action='store_true')
            s.add_argument('--courier-minimum-rub', default=None)
            s.add_argument('--confirm', default='')
    args = p.parse_args()
    if args.command == 'submit-test-retry':
        print('DISABLED: legacy checkout submit bypasses the receiving-node order UUID contract. No order sent.')
        return 2
    try:
        return asyncio.run(reconcile(args))
    except Exception as exc:
        print('FAIL CLOSED: ' + type(exc).__name__ + '; проверь сохранённую попытку, не повторяй')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
