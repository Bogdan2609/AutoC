"""Pure redacted capability/readiness views: never leak supplier credentials."""
from __future__ import annotations
from .capabilities import capabilities_matrix
from .factory import SupplierBindingError, SupplierFactory
from .registry import SUPPLIER_CODES


def matrix() -> dict:
    return {'version': '0.41.2', 'suppliers': [dict(feature, documented_order_readback=True,
             manual_readback_endpoint_available=True, auto_supplier_confirmation=False)
             for feature in capabilities_matrix()],
            'read_only': True, 'outbound_supplier_writes_allowed': False}


def readiness(code, *, supplier, account, destination, node) -> dict:
    try:
        bound = SupplierFactory().for_search(code, supplier=supplier, account=account,
                                             destination=destination, node=node)
    except (SupplierBindingError, ValueError):
        return {'supplier': code, 'account_id': str(account.id),
                'destination_id': str(destination.id), 'node_id': str(node.id),
                'ready': False, 'read_only': True, 'status': 'not_ready'}
    return {'supplier': code, 'account_id': str(bound.account_id),
            'destination_id': str(bound.destination_id), 'node_id': str(bound.node_id),
            'ready': True, 'read_only': True, 'status': 'ready'}
