"""Bounded batch read path for OWN-STOCK cart preview, no state mutations.

Supplier-backed seller lines stay in the existing exact live-quote validation.
All data are read in bounded SELECTs; merchant route graphs are memoized by the
existing routes_to_pickup routine on production AsyncSession.info.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.modules.marketplace.models import (
    FulfillmentNode, InventoryBalance, Merchant, SellerOffer, Store, UsedPart,
)
from app.modules.master_catalog.models import OfferSaleTerms
from app.modules.marketplace.routing import routes_to_pickup
from app.modules.marketplace.offer_rank import fulfillment_rank
from app.modules.marketplace.commercial_guardrails import checked_money


async def own_stock_preview_batch(db, lines):
    """Return line-id keyed previews for own-stock lines only (at most 40)."""
    if not lines or len(lines) > 40:
        return {}
    ids = {line.seller_offer_id for line in lines if line.seller_offer_id}
    if not ids:
        return {}
    offers = {offer.id: offer for offer in (await db.scalars(
        select(SellerOffer).where(SellerOffer.id.in_(ids)))).all()}
    own = {key: offer for key, offer in offers.items() if offer.source_kind == 'own_stock'}
    if not own:
        return {}
    store_ids = {o.store_id for o in own.values()}
    merchant_ids = {o.merchant_id for o in own.values()}
    used_ids = {o.used_part_id for o in own.values() if o.used_part_id}
    stores = {x.id: x for x in (await db.scalars(select(Store).where(Store.id.in_(store_ids)))).all()}
    merchants = {x.id: x for x in (await db.scalars(select(Merchant).where(Merchant.id.in_(merchant_ids)))).all()}
    used = {x.id: x for x in (await db.scalars(select(UsedPart).where(UsedPart.id.in_(used_ids)))).all()} if used_ids else {}
    terms = {x.seller_offer_id: x for x in (await db.scalars(select(OfferSaleTerms).where(
        OfferSaleTerms.seller_offer_id.in_(list(own)),
        OfferSaleTerms.status == 'active'))).all()}
    records = (await db.execute(select(InventoryBalance, FulfillmentNode).join(
        FulfillmentNode, InventoryBalance.node_id == FulfillmentNode.id).where(
        InventoryBalance.seller_offer_id.in_(list(own)), FulfillmentNode.active.is_(True)))).all()
    by_offer = {}
    for balance, node in records:
        by_offer.setdefault(balance.seller_offer_id, []).append((balance, node))
    results = {}
    for line in lines:
        offer = own.get(line.seller_offer_id)
        if offer is None:
            continue
        fail = {'line_id': str(line.id), 'in_stock': False}
        store = stores.get(offer.store_id)
        merchant = merchants.get(offer.merchant_id)
        part = used.get(offer.used_part_id) if offer.used_part_id else None
        if (offer.status != 'active' or not store or store.status != 'active' or
            store.merchant_id != offer.merchant_id or not merchant or
            merchant.status != 'active' or (offer.used_part_id and
            (not part or part.status != 'published')) or
            type(line.quantity) is not int or line.quantity < 1):
            results[line.id] = fail
            continue
        rule = terms.get(offer.id)
        if rule and (line.quantity < rule.minimum_units or line.quantity % rule.step_units):
            results[line.id] = fail
            continue
        choices = []
        for balance, node in by_offer.get(offer.id, ()): 
            available = balance.on_hand - balance.reserved
            if node.merchant_id != offer.merchant_id or available <= 0:
                continue
            if line.fulfillment_node_id is None:
                # Same no-pickup behavior as options_for_offer.
                choices.append({'stock': available, 'days': 0, 'fee': '0.00',
                                'node_id': str(node.id), 'route_node_ids': [str(node.id)]})
            else:
                paths = await routes_to_pickup(db, offer.merchant_id, node.id, line.fulfillment_node_id)
                for path in paths:
                    choices.append({'stock': available, 'days': path['days'],
                                    'fee': path['fee'], 'node_id': str(node.id),
                                    'route_node_ids': path['route_node_ids']})
        choices.sort(key=fulfillment_rank)
        price = checked_money(offer.price)
        result = {'line_id': str(line.id), 'seller_offer_id': str(offer.id),
                  'quantity': line.quantity, 'unit_price': str(price),
                  'merchant_id': str(offer.merchant_id),
                  'in_stock': any(x['stock'] >= line.quantity for x in choices)}
        results[line.id] = result
    return results
