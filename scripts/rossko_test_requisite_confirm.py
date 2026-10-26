"""One fenced ROSSKO portal-test checkout WITH the corrected requisite_id.

This is a THIRD deliberately approved test attempt after two non-confirmations.
Old journals are kept intact. New journal survives container rebuild.
Uses read-only GetCheckoutDetails + GetSearch + GetOrders before dispatch.
NEVER calls GetCheckout during preview.
Could create a REAL order if supplier-side test mode is disabled.
Never run through public API, worker or unattended scheduler.
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
    JOURNAL_DIR, EXPECTED_BRAND, EXPECTED_ARTICLE, EXPECTED_QTY, DEFAULT_STOCK,
    MAX_TEST_PRICE, COURIER_DELIVERY_ID, context, live_quote,
    soap_request, latest_journal, normalize_checkout, safe_number,
    write_initial, replace_journal, now_iso, safe_order_summary,
)
from scripts.rossko_test_order_audit import parse_recent_orders
from app.modules.marketplace.rossko_requisites import make_payment
from scripts.rossko_test_retry_diagnostic import retry_journal_path

CONFIRM = 'I_CONFIRM_ROSSKO_TEST_THIRD_ATTEMPT_WITH_REQUISITE_AND_DUPLICATE_RISK'


def final_journal_path(account_id: str, destination_id: str) -> Path:
    identity = f'{account_id}:{destination_id}:{EXPECTED_BRAND}:{EXPECTED_ARTICLE}'
    token = hashlib.sha256(identity.encode()).hexdigest()[:20]
    # A single permanent retry marker: not reset by changing day or container.
    return JOURNAL_DIR / f'{token}-requisite-corrected-third-attempt.json'


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
    second = retry_journal_path(account_id, destination_id)
    if not original or not original.is_file() or not second.is_file():
        raise ValueError('Не найдены обе предыдущие попытки; новая отправка заблокирована')
    first_record = json.loads(original.read_text(encoding='utf-8'))
    if first_record.get('account_id') != account_id or first_record.get('destination_id') != destination_id:
        raise ValueError('Несовпадение исходной попытки')
    if first_record.get('state') != 'supplier_rejected' or first_record.get('order_ids'):
        raise ValueError('Исходная попытка не подтверждена как отклонённая без ID')
    record = json.loads(second.read_text(encoding='utf-8'))
    if record.get('account_id') != account_id or record.get('destination_id') != destination_id:
        raise ValueError('Несовпадение аккаунта или направления в журнале')
    if record.get('state') != 'supplier_rejected' or record.get('order_ids'):
        raise ValueError('Вторая попытка не является отклонённой без ID')
    error = str(record.get('error_code') or '')
    messages = ' '.join(str(x) for x in (record.get('redacted_supplier_messages') or []))
    if error != 'supplier_rejected_payment' and 'Не указаны реквизиты' not in messages:
        raise ValueError('Причина предыдущего отказа не подтверждена как отсутствие реквизитов')
    return record


async def attempt(args) -> int:
    if args.confirm != CONFIRM or not args.portal_test_enabled or not args.accept_duplicate_risk:
        print('BLOCKED: требуется явное подтверждение тестового режима и риска дубликата')
        return 2
    if get_settings().app_env not in ('development', 'local'):
        print('BLOCKED: запуск разрешён только в development/local')
        return 2
    if args.stock != DEFAULT_STOCK or str(args.payment_id) != '1':
        print('BLOCKED: this scenario allows only stock HST282827366 and payment 1')
        return 2
    path = final_journal_path(args.account_id, args.destination_id)
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
        # make_payment enforces that the unique live CompanyList ID is present.
        payment_fragment = make_payment(args.payment_id, requisite_id)
        if payment_fragment.get('requisite_id') != requisite_id:
            print('BLOCKED: failed supplier company mapping')
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
        print('READ-ONLY CHECK: CF 705 не найден среди последних 500 заказов; отсутствие предыдущих заказов не доказано.')
        print('ВАЖНО: тестовый режим ROSSKO невозможно проверить через эти API; при отключённом режиме создастся реальный заказ.')
        receiver = await receiving_contact_for_destination_ids(args.account_id, args.destination_id)
        name, phone = receiver.name, receiver.phone
        record = {
            'state': 'dispatch_unknown', 'created_utc': now_iso(),
            'account_id': args.account_id, 'destination_id': args.destination_id,
            'mode': 'THIRD_EXPLICIT_TEST_WITH_LIVE_REQUISITE',
            'previous_attempt_utc': old.get('created_utc'),
            'previous_attempt_state': old.get('state'),
            'sku': f'{EXPECTED_BRAND} {EXPECTED_ARTICLE}',
            'stock': offer.warehouse, 'quantity': EXPECTED_QTY,
            'quote_rub': str(offer.price), 'max_rub': str(MAX_TEST_PRICE),
            'delivery_id': delivery_id, 'payment_id': str(args.payment_id),
            'requisite_id': requisite_id,
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
            'payment': payment_fragment,
            'contact': {'name': name, 'phone': phone,
                        'comment': 'AUTOCODEX TEST MODE THIRD REQUISITE DIAGNOSTIC'},
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


async def preview(args) -> int:
    """Read-only preview; never creates journal or calls GetCheckout."""
    if get_settings().app_env not in ('development', 'local'):
        print('BLOCKED: development/local only')
        return 2
    if args.stock != DEFAULT_STOCK or str(args.payment_id) != '1':
        print('BLOCKED: unexpected stock or payment')
        return 2
    if final_journal_path(args.account_id, args.destination_id).exists():
        print('BLOCKED: third-attempt journal already exists; inspect/reconcile, do not resubmit')
        return 2
    try:
        old = load_previous(args.account_id, args.destination_id)
        cfg, delivery_id, address_id, offer, requisite_id = await live_quote(
            args.account_id, args.destination_id, args.stock, args.payment_id)
        if delivery_id != COURIER_DELIVERY_ID or not address_id:
            print('BLOCKED: courier destination not valid')
            return 2
        payment = make_payment(args.payment_id, requisite_id)
        # A live 500-order lookup here provides visibility, not proof of absence.
        orders = await soap_request('GetOrders', 'GetOrders', {
            'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'limit': 500})
        status, candidates = parse_recent_orders(orders)
        if status != 'ok' or candidates:
            print('BLOCKED: uncertain recent orders or supplier lookup; no order sent')
            return 2
        print('READ_ONLY=True GETCHECKOUT_CALLED=False')
        print('OLD_SECOND_STATE=' + str(old.get('state')))
        print('SKU=Trialli CF 705 QTY=1 STOCK=' + str(offer.warehouse) +
              ' PRICE_RUB=' + str(offer.price) + ' MAX_RUB=' + str(MAX_TEST_PRICE))
        print('COURIER_VALID=True ADDRESS_VALID=True PAYMENT=' + payment['payment_id'] +
              ' REQUISITE=' + payment['requisite_id'])
        print('RECENT_ORDERS_SKU_MATCHES=0 (not proof of nonexistence)')
        print('OLD_JOURNALS_UNCHANGED=True; NEW_JOURNAL_EXISTS=False')
        print('Supplier portal test-mode cannot be confirmed by API; no order sent.')
        return 0
    except Exception as exc:
        print('READ_ONLY_FAILED=' + type(exc).__name__ + '; no supplier write')
        return 2


async def reconcile(args) -> int:
    path = final_journal_path(args.account_id, args.destination_id)
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
    p = argparse.ArgumentParser(description='ONE third ROSSKO test checkout with verified requisite. May create order!')
    commands = p.add_subparsers(dest='command', required=True)
    for name in ('preview', 'submit-once', 'reconcile'):
        s = commands.add_parser(name)
        s.add_argument('--account-id', required=True)
        s.add_argument('--destination-id', required=True)
        if name in ('submit-once', 'preview'):
            s.add_argument('--stock', default='HST282827366')
            s.add_argument('--payment-id', default='1')
            s.add_argument('--portal-test-enabled', action='store_true')
            s.add_argument('--accept-duplicate-risk', action='store_true')
            s.add_argument('--courier-minimum-unknown', action='store_true')
            s.add_argument('--courier-minimum-rub', default=None)
            s.add_argument('--confirm', default='')
    args = p.parse_args()
    if args.command == 'submit-once':
        print('DISABLED: legacy checkout submit bypasses the receiving-node order UUID contract. No order sent.')
        return 2
    try:
        return asyncio.run(preview(args) if args.command == 'preview' else reconcile(args))
    except Exception as exc:
        print('FAIL CLOSED: ' + type(exc).__name__ + '; проверь сохранённую попытку, не повторяй')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
