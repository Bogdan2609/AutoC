"""Public status endpoint is read-only. Supplier HTTP is always worker-only."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.offer_engine.discovery import status

router = APIRouter(prefix='/api/v1/discovery', tags=['supplier-discovery'])


@router.get('/status')
async def supplier_search_status(
    brand: str = Query(min_length=2, max_length=60),
    article: str = Query(min_length=4, max_length=70),
    db: AsyncSession = Depends(get_session),
):
    # Polling status never enqueues external API requests.
    return await status(db, brand, article)
