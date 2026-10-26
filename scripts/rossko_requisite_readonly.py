"""Read-only ROSSKO requisites preflight. NEVER calls GetCheckout.

The list of numeric IDs is not enough to determine the correct legal entity:
verify company ownership privately in the ROSSKO supplier portal.
"""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.marketplace.account_probe import live_rossko_details, normalize_checkout_details
from app.modules.marketplace.rossko_requisites import (
    RosskoRequisiteError, live_requisite_ids, resolve_live_requisite, make_payment,
)
from app.modules.marketplace.supplier_delivery_policy import require_live_rossko_courier
from scripts.rossko_test_checkout_lab import context


async def check(account_id: str, destination_id: str, requisite_id: str, payment_id: str) -> int:
    cfg, delivery_id, address_id = await context(account_id, destination_id)
    response = await live_rossko_details(cfg)  # Only GetCheckoutDetails
    details = normalize_checkout_details(response)
    require_live_rossko_courier(delivery_id, address_id, details, payment_id=payment_id)
    available = live_requisite_ids(response)
    selected = resolve_live_requisite(response, getattr(cfg, 'rossko_requisite_id', None), requisite_id)
    fragment = make_payment(payment_id, selected)
    print('READ_ONLY=True GETCHECKOUT_CALLED=False')
    print(f'COURIER_ROUTE_VALID=True ADDRESS_VALID=True PAYMENT_METHOD_VALID=True')
    print(f'AVAILABLE_REQUISITE_IDS={available}')
    print(f'SELECTED_REQUISITE_VALID=True PAYMENT_FRAGMENT={fragment}')
    print('Company selected via GetCheckoutDetails and optional saved supplier destination mapping.')
    print('Existing original and second-attempt journals are unchanged; no new submit allowed by this tool.')
    return 0


def main():
    p = argparse.ArgumentParser(description='ROSSKO courier + requisite verification (read only)')
    p.add_argument('--account-id', required=True)
    p.add_argument('--destination-id', required=True)
    p.add_argument('--requisite-id', default=None, help='Optional company ID; unique live company is auto-selected')
    p.add_argument('--payment-id', default='1')
    args = p.parse_args()
    try:
        return asyncio.run(check(args.account_id, args.destination_id, args.requisite_id, args.payment_id))
    except RosskoRequisiteError as exc:
        print('READ_ONLY BLOCKED: ' + str(exc))
    except Exception as exc:
        print('READ_ONLY FAILED: ' + type(exc).__name__ + ' (supplier data not printed)')
    return 2


if __name__ == '__main__':
    sys.exit(main())
