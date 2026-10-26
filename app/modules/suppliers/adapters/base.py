"""Single read contract used by the live offer_engine.providers registry.

External supplier WRITES are NOT part of a read adapter. They stay behind the
marketplace procurement boundary (paid sale, courier destination, audit and
reconciliation). Extending this protocol does not authorize supplier orders.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.offer_engine.contracts import Offer
from app.modules.offer_engine.supplier_errors import UpstreamBusinessError


@runtime_checkable
class SupplierAdapter(Protocol):
    code: str

    async def search(self, brand: str, article: str, config=None) -> list[Offer]: ...

    async def check_stock(
        self, key: str, brand: str, article: str, quantity: int, config=None
    ) -> Offer: ...


class ReadAdapter:
    """Shared check_stock behaviour: fresh READ, exact SKU, packaging and stock.

    No supplier write and no price reservation is performed. Final checkout
    still has to revalidate the account/destination and quote under its lock.
    """
    code: str

    async def check_stock(
        self, key: str, brand: str, article: str, quantity: int, config=None
    ) -> Offer:
        if type(quantity) is not int or quantity < 1:
            raise UpstreamBusinessError('Invalid requested quantity')
        if not key or not brand or not article:
            raise UpstreamBusinessError('Incomplete supplier quote reference')
        rows = await self.search(brand, article, config)
        selected = next((item for item in rows if (
            item.code == self.code and item.key == key
            and normalize_brand(item.brand) == normalize_brand(brand)
            and normalize_article(item.article) == normalize_article(article)
        )), None)
        if (selected is None or selected.qty is None or selected.qty < quantity
                or quantity < selected.minimum or quantity % selected.multiple):
            raise UpstreamBusinessError('Selected supplier offer is no longer available')
        return selected


class SupplierPurchaseAdapter(Protocol):
    """Optional, separately verified WRITE interface; not implemented by reads.

    Calls MUST originate from durable marketplace procurement, never directly
    from the offer engine, and must not be retried on ambiguous timeouts.
    """
    async def create_order(self, purchase_attempt_id: str) -> str: ...
    async def order_status(self, external_order_id: str) -> str: ...
    async def cancel_order(self, external_order_id: str) -> str: ...
