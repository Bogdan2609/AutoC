"""A public refresh *request* only queues read-only supplier searches."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product,Brand
from app.modules.offer_engine.service import enqueue_product, CODES
router=APIRouter(prefix='/api/v1/offer-cache',tags=['cache'],dependencies=[Depends(require_admin)])

@router.post('/products/{product_id}/refresh',status_code=202)
async def request_refresh(product_id:UUID,db:AsyncSession=Depends(get_session)):
    product=await db.get(Product,product_id)
    if not product or product.status!='published':
        raise HTTPException(404,'Product not found')
    brand=await db.get(Brand,product.brand_id)
    return {'refresh':await enqueue_product(db,product,brand),'source':'local-cache'}
