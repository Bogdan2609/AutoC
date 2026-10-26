from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class SupplierOffer:
    supplier_code: str
    external_key: str
    brand: str
    article: str
    purchase_price: Decimal
    currency: str
    tax_status: str
    quantity: int | None
    lead_time_days: int | None


class SupplierAdapter(Protocol):
    code: str

    async def search(self, brand: str, article: str) -> list[SupplierOffer]: ...
