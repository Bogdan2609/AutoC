"""Presentation grouping only; never merges procurement identifiers or quantities.

The flat marketplace API remains the source of per-warehouse truth. Grouping is
computed deterministically from those rows, with all unique variants preserved.
"""
from decimal import Decimal
from app.modules.marketplace.offer_rank import fulfillment_rank


def _variant_rank(item):
    option=(item.get('fulfillment') or [{}])[0]
    return (Decimal(str(item['price']))+Decimal(str(option.get('fee','0'))),
            fulfillment_rank(option),
            str(item.get('warehouse_id') or ''), str(item.get('snapshot_id') or ''))


def group_marketplace_offers(rows):
    # SellerOffer rows can contain several configured pickup paths. Keep each
    # path inspectable without changing the flat API's original shape.
    expanded=[]
    for row in rows:
        fulfillment=row.get('fulfillment') or []
        if row.get('source_kind')!='supplier_quote' and len(fulfillment)>1:
            expanded.extend({**row,'fulfillment':[route]} for route in fulfillment)
        else:
            expanded.append(row)
    rows=expanded
    grouped={}
    for item in rows:
        if item.get('source_kind') in ('supplier_quote','feed_indicative'):
            key=(item.get('source_kind'),item.get('supplier_code'),item.get('merchant_id'))
        else:
            # Every seller's SKU is its own commercial offer. Never combine
            # two SellerOffers just because they happen to have the same store.
            key=(item.get('source_kind'),item.get('offer_id'))
        if key not in grouped:
            grouped[key]={
                'source_kind':item['source_kind'], 'supplier_code':item.get('supplier_code'),
                'merchant':item.get('merchant'), 'merchant_id':item.get('merchant_id'),
                'store':item.get('store'), 'offer_id':item.get('offer_id'),
                'requires_live_validation':bool(item.get('requires_live_validation')),
                'variants':[],
            }
        grouped[key]['variants'].append(item)
    result=[]
    for group in grouped.values():
        variants=group['variants']
        # Deduplicate an identical snapshot+path only; not separate warehouses,
        # accounts, or routes with different supplier prices or timings.
        seen=set();uniques=[]
        for row in variants:
            fulfillment=(row.get('fulfillment') or [{}])[0]
            identity=(row.get('feed_offer_id') or row.get('snapshot_id') or row.get('offer_id'),
                      row.get('supplier_account_id'), row.get('supplier_destination_id'),
                      tuple(fulfillment.get('route_node_ids',[])),
                      row.get('warehouse_id'),row.get('price'))
            if identity in seen:continue
            seen.add(identity);uniques.append(row)
        uniques.sort(key=_variant_rank)
        prices=[Decimal(str(r['price'])) for r in uniques]
        times=[o['days'] for r in uniques for o in r.get('fulfillment',[]) if o.get('days') is not None]
        group.update(variants=uniques,variant_count=len(uniques),
                     warehouse_count=len({(r.get('supplier_account_id'),r.get('warehouse_id')) for r in uniques})
                        if group['source_kind'] in ('supplier_quote','feed_indicative') else len(uniques),
                     account_count=len({r.get('supplier_account_id') for r in uniques if r.get('supplier_account_id')}),
                     min_price=str(min(prices)),max_price=str(max(prices)),
                     min_estimated_days=min(times) if times else None,
                     best_variant=uniques[0])
        result.append(group)
    result.sort(key=lambda g:(Decimal(g['min_price']),g['supplier_code'] or '',g.get('offer_id') or ''))
    return result
