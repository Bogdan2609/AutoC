import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.catalog.service import get_published
from app.modules.suppliers.schemas import PublicOfferOut
from app.modules.suppliers.service import list_product_offers
from app.modules.offer_engine.cache import product_offers

router = APIRouter(prefix="/api/v1", tags=["suppliers"])


@router.get("/products/{product_id}/offers", response_model=list[PublicOfferOut])
async def get_product_offers(product_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> list[PublicOfferOut]:
    await get_published(session, product_id)
    return await product_offers(session, product_id)
