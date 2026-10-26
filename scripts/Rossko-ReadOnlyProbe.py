"""Read-only ROSSKO supplier test preflight for Autocodex v0.33.0.

Commands: accounts, probe. NO GetCheckout. NO supplier-side order writes.
Never prints API credentials, contact details, or full configured addresses.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Docker runs this file by path, not as a package. Include the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.modules.marketplace.account_credentials import for_account
from app.modules.marketplace.account_probe import live_rossko_details, normalize_checkout_details
from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode
from app.modules.offer_engine.providers import PROVIDERS, exact
from app.modules.suppliers.models import Supplier


def valid_options(offers, brand, article):
    """Never promote a cross or unavailable stock to an order candidate."""
    return sorted(
        (offer for offer in offers if exact(offer, brand, article)
         and offer.qty is not None and offer.qty >= 1 and offer.price > Decimal('0')
         and offer.warehouse and offer.minimum <= 1 and offer.multiple == 1),
        key=lambda offer: (offer.price, offer.days is None,
                           offer.days if offer.days is not None else 9999, offer.warehouse),
    )


def delivery_matches(details, destination):
    allowed = {d['id'] for d in details['deliveries']}
    if not destination.external_delivery_id or destination.external_delivery_id not in allowed:
        return False
    if destination.external_address_id:
        addresses = {address['id']: address for address in details['addresses']}
        if destination.external_address_id not in addresses:
            return False
        a = addresses[destination.external_address_id]
        if a['allowed_delivery_ids'] and destination.external_delivery_id not in a['allowed_delivery_ids']:
            return False
    return True


async def accounts():
    async with get_session_factory()() as db:
        rows = (await db.execute(
            select(SupplierAccount, Supplier).join(Supplier, Supplier.id == SupplierAccount.supplier_id)
            .where(Supplier.code == 'rossko').order_by(SupplierAccount.label)
        )).all()
        if not rows:
            print('No ROSSKO accounts configured in database.')
            return 2
        for account, _ in rows:
            destinations = (await db.execute(select(SupplierDestination).where(
                SupplierDestination.account_id == account.id))).scalars().all()
            print(f'ACCOUNT id={account.id} label={account.label!r} profile={account.credential_profile!r} '
                  f'active={bool(account.enabled)} read_enabled={bool(account.read_enabled)}')
            for dest in destinations:
                print(f'  DESTINATION id={dest.id} active={bool(dest.enabled)} '
                      f'verified={bool(dest.verified)} configured_delivery={bool(dest.external_delivery_id)} '
                      f'configured_address={bool(dest.external_address_id)}')
        return 0


async def probe(account_id, destination_id, brand, article):
    if not get_settings().supplier_api_enabled:
        print('BLOCKED: SUPPLIER_API_ENABLED is off.')
        return 2
    async with get_session_factory()() as db:
        account = await db.get(SupplierAccount, UUID(account_id))
        dest = await db.get(SupplierDestination, UUID(destination_id))
        vendor = await db.get(Supplier, account.supplier_id) if account else None
        node = await db.get(FulfillmentNode, dest.node_id) if dest else None
        if (not account or not vendor or vendor.code != 'rossko' or not dest or
            dest.account_id != account.id or not account.enabled or not account.read_enabled or
            not dest.enabled or not dest.verified or not node or not node.active or
            node.merchant_id != account.merchant_id):
            print('BLOCKED: ROSSKO account/destination is not verified, enabled and seller-bound.')
            return 2
        if not dest.external_delivery_id:
            print('BLOCKED: destination does not have an external ROSSKO delivery ID.')
            return 2
        cfg = for_account('rossko', account, dest)
        if not (cfg.rossko_key1 and cfg.rossko_key2):
            print('BLOCKED: credentials missing for this exact account profile.')
            return 2
    # Database session is closed before network calls. Both are read-only SOAP operations.
    print('READ-ONLY: GetCheckoutDetails ...')
    details = normalize_checkout_details(await live_rossko_details(cfg))
    delivery_ok = delivery_matches(details, dest)
    print(f'DESTINATION_VALID={delivery_ok}')
    for d in details['deliveries']:
        print(f"DELIVERY id={d['id']} name={d['name']}")
    for p in details['payments']:
        print(f"PAYMENT id={p['id']} name={p['name']}")
    if not delivery_ok:
        print('BLOCKED: stored delivery/address configuration differs from ROSSKO live details.')
        return 2
    print(f'READ-ONLY: GetSearch for {brand!r} {article!r} ...')
    offers = await PROVIDERS['rossko'].search(brand, article, cfg)
    candidates = valid_options(offers, brand, article)
    print(f'ELIGIBLE_OFFERS={len(candidates)}')
    for i, offer in enumerate(candidates[:12], 1):
        print(f'OFFER #{i} brand={offer.brand} article={offer.article} stock={offer.warehouse} '
              f'purchase_rub={offer.price} quantity={offer.qty} minimum={offer.minimum} '
              f'multiple={offer.multiple} days={offer.days}')
    print('NO ORDER SENT. Global production procurement switches unchanged.')
    return 0 if candidates else 3


async def main():
    parser = argparse.ArgumentParser(description='ROSSKO read-only preflight. Never creates orders.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('accounts', help='List ROSSKO accounts and destinations without secrets.')
    pr = sub.add_parser('probe', help='Check live delivery/payment methods and one exact SKU.')
    pr.add_argument('--account-id', required=True)
    pr.add_argument('--destination-id', required=True)
    pr.add_argument('--brand', default='Trialli')
    pr.add_argument('--article', default='CF 705')
    args = parser.parse_args()
    try:
        return await (accounts() if args.command == 'accounts' else
                      probe(args.account_id, args.destination_id, args.brand, args.article))
    except (ValueError, TypeError):
        print('CHECK FAILED: invalid identifiers or malformed supplier response; no order sent.')
        return 2
    except Exception:
        # An untrusted supplier/network exception might contain auth or PII; suppress its text.
        print('CHECK FAILED: API or database read failed; no order sent. Review local logs securely.')
        return 2


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
