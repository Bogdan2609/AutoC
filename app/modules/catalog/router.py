import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.catalog.schemas import ProductOut, SearchOut
from app.modules.catalog.service import find_published, get_published

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get("/search", response_model=SearchOut)
async def search_products(
    q: str = Query(default="", max_length=110),
    limit: int = Query(default=20, ge=1, le=100),
    brand: str | None = Query(default=None, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    offset: int = Query(default=0, ge=0, le=5000),
    session: AsyncSession = Depends(get_session),
) -> SearchOut:
    products, count = await find_published(session, q, limit, offset, brand_slug=brand)
    return SearchOut(items=[ProductOut.model_validate(x) for x in products], count=count, limit=limit, offset=offset)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ProductOut:
    return ProductOut.model_validate(await get_published(session, product_id))
