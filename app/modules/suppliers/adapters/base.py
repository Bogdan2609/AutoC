"""Compatibility names for the canonical live offer_engine.providers registry.

External supplier WRITES stay behind marketplace.procurement and require
paid-order + verified courier account/destination. No auto-dispatch here.
"""
from typing import Protocol
from app.modules.offer_engine.providers import Offer as SupplierOffer

class SupplierAdapter(Protocol):
    code: str
    async def search(self, brand: str, article: str, config=None) -> list[SupplierOffer]: ...

class SupplierPurchaseAdapter(SupplierAdapter, Protocol):
    # Separate explicit interface until account-specific create/status/cancel
    # contracts are verified and integrated into the durable dispatch state machine.
    async def create_order(self, purchase_attempt_id: str) -> str: ...
    async def order_status(self, external_order_id: str) -> str: ...
    async def cancel_order(self, external_order_id: str) -> str: ...
