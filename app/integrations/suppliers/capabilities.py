"""Read/purchase capability view sourced from the existing procurement contracts.

live_create means a supplier-specific implementation exists (ROSSKO only);
it NEVER means the gateway may dispatch, or that production is authorized.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from .registry import SUPPLIER_CODES, UnsupportedSupplier


@dataclass(frozen=True)
class SupplierFeatures:
    code: str
    search: bool
    stock_check: bool
    purchase_preview: bool
    contract_live_create: bool
    contract_order_readback: bool
    contract_cancel: bool
    gateway_dispatch: bool
    reason: str

    def public(self) -> dict:
        return asdict(self)


def features(code: str) -> SupplierFeatures:
    if code not in SUPPLIER_CODES:
        raise UnsupportedSupplier('Unsupported supplier')
    # Import on call to avoid import cycles with the existing marketplace.
    from app.modules.marketplace.purchase_lifecycle import capabilities
    c = capabilities(code)
    return SupplierFeatures(code, True, True, bool(c.preview), bool(c.live_create),
                            bool(c.live_order_lookup), bool(c.live_cancel), False, c.reason)


def capabilities_matrix() -> list[dict]:
    return [features(code).public() for code in SUPPLIER_CODES]
