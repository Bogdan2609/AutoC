"""Explicit merchant routes and supplier -> receiving -> pickup estimates.

A supplier's warehouse is NOT an owned fulfillment node. Supplier lead days
come from a quote; merchant delivery uses only configured directed edges.
Never fabricate a buyer ETA or a fee for the supplier's leg.
"""
from decimal import Decimal
from sqlalchemy import select, and_
from app.modules.marketplace.models import DeliveryRoute, FulfillmentNode
from app.modules.marketplace.offer_rank import fulfillment_rank


def describe_supplier_route(snapshot, detail, receiving, merchant_route=None, *, pickup_selected=False):
    """Build an auditable route without treating a warehouse as the customer's PVZ."""
    estimated_supplier_days = snapshot.lead_time_days
    if estimated_supplier_days is not None and estimated_supplier_days < 0:
        estimated_supplier_days = None
    own_route = merchant_route or {
        'city': receiving.city, 'days': 0, 'fee': '0.00',
        'pickup_node_id': str(receiving.id) if receiving.kind in ('pickup','warehouse_pickup') else None,
        'route_node_ids': [str(receiving.id)], 'legs': [],
    }
    merchant_days = own_route['days']
    legs = [{
        'kind': 'supplier_to_receiving', 'from_warehouse': detail.warehouse if detail else '',
        'to_node_id': str(receiving.id), 'to_node': receiving.name,
        'days': estimated_supplier_days, 'fee': None,
        'fee_known': False, 'estimate_source': 'supplier_quote',
    }]
    legs.extend(own_route.get('legs', []))
    pickup_node_id = own_route.get('pickup_node_id')
    # A warehouse is a receiving station, never silently the buyer's pickup.
    if not pickup_selected and receiving.kind not in ('pickup','warehouse_pickup'):
        pickup_node_id = None
    return {
        'supplier_warehouse': detail.warehouse if detail else '',
        'receiving_node_id': str(receiving.id), 'receiving_node': receiving.name,
        'receiving_city': receiving.city, 'route_legs': legs,
        'supplier_days': estimated_supplier_days, 'merchant_days': merchant_days,
        'days': None if estimated_supplier_days is None else estimated_supplier_days + merchant_days,
        'fee': str(own_route['fee']), 'supplier_fee_known': False,
        'pickup_node_id': pickup_node_id,
        'route_node_ids': own_route['route_node_ids'],
        'city': own_route['city'],
        'delivery_scope': ('pickup' if pickup_node_id else 'receiving_only'),
        'delivery_note': ('Ориентировочный срок до выбранного ПВЗ' if pickup_node_id
                          else 'Ориентировочный срок до нашего пункта приёмки; доставка покупателю не рассчитана'),
    }


async def routes_to_pickup(db, merchant_id, source_id, pickup_id, max_legs=2):
    if not pickup_id:
        return []
    # Use a graph scoped to this merchant, never load all merchants' routes.
    cache=getattr(db, 'info', {}).setdefault('merchant_route_graph', {})
    if merchant_id in cache:
        by_id,graph=cache[merchant_id]
    else:
        nodes=(await db.scalars(select(FulfillmentNode).where(
            FulfillmentNode.merchant_id==merchant_id,
            FulfillmentNode.active.is_(True)))).all()
        by_id={n.id:n for n in nodes}
        rows=(await db.scalars(select(DeliveryRoute).where(
            DeliveryRoute.active.is_(True),
            DeliveryRoute.source_node_id.in_(list(by_id)),
            DeliveryRoute.target_node_id.in_(list(by_id))))).all() if by_id else []
        graph={}
        for route in rows:
            graph.setdefault(route.source_node_id,[]).append(route)
        cache[merchant_id]=(by_id,graph)
    source=by_id.get(source_id)
    target=by_id.get(pickup_id)
    if not source or not target or target.kind not in ('pickup','warehouse_pickup'):
        return []
    if source.id==target.id:
        return [{'node_id':str(source.id),'pickup_node_id':str(target.id),'city':target.city,
                 'days':0,'fee':'0.00','route_node_ids':[str(source.id)],'legs':[]}]
    if max_legs<1 or max_legs>3:
        raise ValueError('max_legs outside tested range')
    candidates=[]
    frontier=[(source.id,[source.id],0,Decimal('0.00'),[])]
    for _ in range(max_legs):
        nxt=[]
        for node_id,path,days,fee,legs in frontier:
            for edge in graph.get(node_id,[]):
                to=edge.target_node_id
                if to in path:continue
                new_path=path+[to]
                edge_days=edge.transit_days+edge.handling_days
                new_days=days+edge_days
                new_fee=fee+edge.fee
                new_legs=legs+[{
                    'kind':'receiving_to_pickup', 'from_node_id':str(node_id),
                    'from_node':by_id[node_id].name, 'to_node_id':str(to),
                    'to_node':by_id[to].name, 'days':edge_days,
                    'fee':str(edge.fee), 'fee_known':True,
                    'estimate_source':'configured_merchant_route',
                }]
                if to==target.id:
                    candidates.append({'node_id':str(source.id),
                        'pickup_node_id':str(target.id),'city':target.city,
                        'days':new_days,'fee':str(new_fee),
                        'route_node_ids':[str(x) for x in new_path], 'legs':new_legs})
                elif len(new_path)<max_legs+1:
                    nxt.append((to,new_path,new_days,new_fee,new_legs))
        frontier=nxt
    return sorted(candidates,key=fulfillment_rank)[:5]
