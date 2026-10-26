from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pricing.models import PricePolicy
from app.modules.finance.pricing_engine import simple_markup_quote


class PricingConfigurationError(Exception):
    pass


def calculate_retail(purchase_price: Decimal, markup_percent: Decimal) -> Decimal:
    """Backward-compatible UNVERIFIED percent-only preview; delegates to F2.

    Never interpret this as a checkout-certified landed-cost calculation.
    """
    return simple_markup_quote(purchase_price, markup_percent)


async def get_active_policy(session: AsyncSession) -> PricePolicy | None:
    policies = (await session.scalars(select(PricePolicy).where(PricePolicy.active.is_(True)))).all()
    if len(policies) > 1:
        raise PricingConfigurationError("Multiple active price policies")
    return policies[0] if policies else None
