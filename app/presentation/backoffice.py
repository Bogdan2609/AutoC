import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.catalog.schemas import BrandCreate, BrandOut, ProductCreate, ProductOut
from app.modules.catalog.service import change_product_slug, create_brand, create_product, publish_product
from app.modules.pricing.models import PricePolicy
from app.modules.suppliers.models import OfferSnapshot, Supplier, SupplierItem
from app.modules.suppliers.schemas import SnapshotCreate, SupplierCreate, SupplierItemCreate

router = APIRouter(prefix="/api/v1/backoffice", tags=["backoffice"], dependencies=[Depends(require_admin)])


class SlugChange(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,198}$")


class PolicyCreate(BaseModel):
    name: str = Field(default="default", max_length=100, min_length=3)
    markup_percent: Decimal = Field(default=Decimal("25.00"), ge=0, le=1000, max_digits=7, decimal_places=2)


@router.post("/brands", response_model=BrandOut, status_code=201)
async def add_brand(payload: BrandCreate, session: AsyncSession = Depends(get_session)) -> Brand:
    return await create_brand(session, payload)


@router.post("/products", response_model=ProductOut, status_code=201)
async def add_product(payload: ProductCreate, session: AsyncSession = Depends(get_session)) -> Product:
    return await create_product(session, payload)


@router.post("/products/{product_id}/publish", response_model=ProductOut)
async def publish(product_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Product:
    return await publish_product(session, product_id)


@router.patch("/products/{product_id}/slug", response_model=ProductOut)
async def rename_slug(product_id: uuid.UUID, body: SlugChange, session: AsyncSession = Depends(get_session)) -> Product:
    return await change_product_slug(session, product_id, body.slug)


@router.post("/suppliers", status_code=201)
async def add_supplier(payload: SupplierCreate, session: AsyncSession = Depends(get_session)) -> dict:
    supplier = Supplier(code=payload.code, name=payload.name)
    session.add(supplier)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Supplier already exists") from exc
    return {"id": str(supplier.id), "code": supplier.code}


@router.post("/supplier-items", status_code=201)
async def add_supplier_item(payload: SupplierItemCreate, session: AsyncSession = Depends(get_session)) -> dict:
    supplier = await session.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    status = "pending"
    if payload.product_id:
        product = await session.get(Product, payload.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        brand = await session.get(Brand, product.brand_id)
        if normalize_article(payload.raw_article) != product.article_norm or normalize_brand(payload.raw_brand) != brand.name_norm:
            raise HTTPException(status_code=409, detail="Supplier identity mismatch; manual reconciliation required")
        # The authenticated operator explicitly selected the matched product.
        status = "confirmed"
    item = SupplierItem(
        supplier_id=payload.supplier_id, product_id=payload.product_id, external_key=payload.external_key,
        raw_brand=payload.raw_brand, raw_article=payload.raw_article, match_status=status,
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Supplier item already exists") from exc
    return {"id": str(item.id), "match_status": item.match_status}


@router.post("/offer-snapshots", status_code=201)
async def add_offer_snapshot(payload: SnapshotCreate, session: AsyncSession = Depends(get_session)) -> dict:
    item = await session.get(SupplierItem, payload.supplier_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Supplier item not found")
    supplier = await session.get(Supplier, item.supplier_id)
    if supplier.code == "rossko" and payload.tax_status != "included":
        raise HTTPException(status_code=422, detail="ROSSKO GetSearch purchase prices include VAT; use included")
    date = payload.observed_at or datetime.now(timezone.utc)
    if date.tzinfo is None or date > datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="observed_at must be timezone-aware and not in future")
    snapshot = OfferSnapshot(
        supplier_item_id=item.id, purchase_price=payload.purchase_price, currency=payload.currency,
        tax_status=payload.tax_status, quantity=payload.quantity, lead_time_days=payload.lead_time_days,
        observed_at=date,
    )
    session.add(snapshot)
    await session.commit()
    from app.modules.offer_engine.cache import invalidate
    await invalidate(item.product_id)
    return {"id": str(snapshot.id), "observed_at": snapshot.observed_at.isoformat()}


@router.post("/price-policy", status_code=201)
async def create_price_policy(payload: PolicyCreate, session: AsyncSession = Depends(get_session)) -> dict:
    # Only one active policy: serialized configuration transaction for PostgreSQL.
    # When changing policies concurrently a DB partial unique index will fail safely.
    current = (await session.scalars(select(PricePolicy).where(PricePolicy.active.is_(True)))).all()
    for policy in current:
        policy.active = False
    await session.flush()
    policy = await session.scalar(select(PricePolicy).where(PricePolicy.name == payload.name))
    if policy is None:
        policy = PricePolicy(name=payload.name, markup_percent=payload.markup_percent, active=True, version=1)
        session.add(policy)
    else:
        policy.markup_percent = payload.markup_percent
        policy.version += 1
        policy.active = True
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Price policy update conflict") from exc
    from app.modules.offer_engine.cache import invalidate_all_offers
    await invalidate_all_offers()
    return {"id": policy.id, "version": policy.version, "markup_percent": str(policy.markup_percent)}
