"""ROSSKO integration boundary, intentionally disabled until API contract verified.

Prices from the user's GetSearch account are purchase prices with VAT included.
The VAT rate, availability meaning, request fields, and automatic ordering support
cannot be inferred from that fact. Never fake a successful supplier reply.
"""

from app.modules.suppliers.adapters.base import SupplierOffer


class RosskoAdapter:
    code = "rossko"

    async def search(self, brand: str, article: str) -> list[SupplierOffer]:
        raise NotImplementedError(
            "ROSSKO GetSearch transport requires verified API schema and credentials"
        )
