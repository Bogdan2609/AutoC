"""Read-only GetOrders audit for an ambiguous ROSSKO portal-test-mode checkout.

Queries the supplier's most recent N orders WITHOUT an order ID (documented
GetOrders limit=...). Never dispatches checkout, prints contacts, addresses,
credentials or full supplier payloads, modifies existing lab journals, or
interprets lack of matches as proof that an order was not created.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.rossko_test_checkout_lab import (
    EXPECTED_BRAND, EXPECTED_ARTICLE, EXPECTED_QTY, MAX_TEST_PRICE,
    context, latest_journal, safe_number, soap_request,
)
from app.modules.catalog.normalize import normalize_brand, normalize_article


def verified_boolean(value: Any) -> bool:
    return value is True or str(value).lower() in ('true', '1')


def parse_recent_orders(response: Any) -> tuple[str, list[dict]]:
    if not isinstance(response, dict):
        return 'invalid_response', []
    root = response.get('OrdersResult', response)
    if not isinstance(root, dict) or not verified_boolean(root.get('success', root.get('Success'))):
        return 'supplier_lookup_failed', []
    data = root.get('OrdersList') or {}
    if not isinstance(data, dict):
        return 'invalid_list', []
    entries = data.get('Order') or []
    if not isinstance(entries, list):
        entries = [entries]
    if len(entries) > 500:
        return 'oversized_list', []
    safe = []
    for order in entries:
        if not isinstance(order, dict):
            return 'invalid_order', []
        try:
            oid = int(order.get('id'))
        except (TypeError, ValueError):
            return 'invalid_order_id', []
        if oid <= 0:
            return 'invalid_order_id', []
        parts = order.get('parts') or {}
        if not isinstance(parts, dict):
            return 'invalid_parts', []
        entries_part = parts.get('part') or []
        if not isinstance(entries_part, list):
            entries_part = [entries_part]
        matching = []
        for part in entries_part:
            if not isinstance(part, dict):
                return 'invalid_part', []
            if (normalize_brand(str(part.get('brand') or '')) == normalize_brand(EXPECTED_BRAND)
                    and normalize_article(str(part.get('partnumber') or '')) == normalize_article(EXPECTED_ARTICLE)):
                try:
                    qty = int(part.get('count'))
                except (TypeError, ValueError):
                    qty = None
                price = safe_number(part.get('price'))
                matching.append({'quantity': qty,
                                 'price': str(price) if price is not None else None,
                                 'within_price_cap': bool(price is not None and 0 < price <= MAX_TEST_PRICE)})
        if matching:
            raw_date = str(order.get('created_date') or '')
            date_only = re.match(r'^\d{2}\.\d{2}\.\d{4}', raw_date)
            safe.append({'order_id': oid, 'created_date': date_only.group() if date_only else 'unavailable',
                         'matching_lines': matching, 'total_lines': len(entries_part)})
    return 'ok', safe


async def check(account_id: str, destination_id: str, limit: int) -> int:
    path = latest_journal(account_id, destination_id)
    if path is None:
        print('BLOCKED: no prior test attempt journal; no supplier query made')
        return 2
    try:
        attempt = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        print('BLOCKED: unreadable attempt journal; no supplier query made')
        return 2
    if (attempt.get('account_id') != account_id or
            attempt.get('destination_id') != destination_id):
        print('BLOCKED: journal account/destination mismatch; no supplier query made')
        return 2
    if attempt.get('order_ids'):
        print('Use existing reconcile command for known order IDs; no supplier query made')
        return 2
    print('READ-ONLY GetOrders by recent-order limit; NO GetCheckout calls')
    print(f'EXISTING ATTEMPT state={attempt.get("state")} created_utc={attempt.get("created_utc")}')
    cfg, _, _ = await context(account_id, destination_id)
    raw = await soap_request('GetOrders', 'GetOrders', {
        'KEY1': cfg.rossko_key1, 'KEY2': cfg.rossko_key2, 'limit': limit,
    })
    status, candidates = parse_recent_orders(raw)
    if status != 'ok':
        print(f'GETORDERS_READ_FAILED category={status}; no raw supplier response printed')
        return 3
    print(f'GETORDERS_READ_OK requested_last={limit} matching_sku_orders={len(candidates)}')
    for candidate in candidates[:12]:
        print('POSSIBLE_SKU_MATCH ' + json.dumps(candidate, sort_keys=True, ensure_ascii=False))
    if len(candidates) > 12:
        print(f'REMAINING_SKU_MATCHES_NOT_SHOWN={len(candidates)-12}')
    print('IMPORTANT: matches may be unrelated earlier orders; absence does NOT prove checkout failed.')
    print('Do not re-submit or remove the attempt marker; ask ROSSKO to confirm using test-mode orders.')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='ROSSKO GetOrders read-only audit WITHOUT order IDs')
    p.add_argument('--account-id', required=True)
    p.add_argument('--destination-id', required=True)
    p.add_argument('--limit', type=int, default=100)
    args = p.parse_args()
    if not 1 <= args.limit <= 500:
        p.error('--limit must be 1..500 (ROSSKO GetOrders contract)')
    try:
        return asyncio.run(check(args.account_id, args.destination_id, args.limit))
    except Exception:
        print('READ-ONLY GETORDERS ERROR: supplier transport or unrecognized response; no sensitive details displayed')
        return 3


if __name__ == '__main__':
    sys.exit(main())
