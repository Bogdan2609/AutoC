"""Operator workspace: bounded, read-optimized tables and auditable stock edits.

Every route is protected by the existing administrator token. Credentials, raw
supplier payloads and customer contact details are intentionally excluded.
"""
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Brand, Product
from app.modules.commerce.models import Order
from app.modules.marketplace.models import (
    Category, DeliveryRoute, FulfillmentNode, InventoryBalance, InventoryMovement,
    Merchant, SellerOffer, Store, SupplierAccount, SupplierDestination, UsedPart,
)
from app.modules.offer_engine.models import CacheState, SupplierLog
from app.modules.offer_engine.cache import invalidate
from app.modules.suppliers.models import Supplier

router = APIRouter(prefix='/api/v1/backoffice/workspace', tags=['operator-workspace'],
                   dependencies=[Depends(require_admin)])


def as_id(value):
    return str(value) if value is not None else None


async def qty(db, model):
    return int((await db.scalar(select(func.count()).select_from(model))) or 0)


@router.get('/summary')
async def summary(db: AsyncSession = Depends(get_session)):
    result = {}
    for name, model in (
        ('products', Product), ('merchants', Merchant), ('stores', Store),
        ('nodes', FulfillmentNode), ('offers', SellerOffer),
        ('supplier_accounts', SupplierAccount), ('orders', Order),
        ('used_parts', UsedPart), ('routes', DeliveryRoute),
    ):
        result[name] = await qty(db, model)
    result['review_products'] = int((await db.scalar(select(func.count()).select_from(Product).where(
        Product.status.in_(['draft','review'])))) or 0)
    result['active_offers'] = int((await db.scalar(select(func.count()).select_from(SellerOffer).where(
        SellerOffer.status == 'active'))) or 0)
    result['api_errors'] = int((await db.scalar(select(func.count()).select_from(SupplierLog).where(
        SupplierLog.outcome == 'error'))) or 0)
    result['cache_errors'] = int((await db.scalar(select(func.count()).select_from(CacheState).where(
        CacheState.status == 'error'))) or 0)
    return result


@router.get('/catalog')
async def catalog(q: str = Query('', max_length=110), status: str = Query('', max_length=24),
                  page: int = Query(1, ge=1, le=10000), size: int = Query(30, ge=1, le=100),
                  db: AsyncSession = Depends(get_session)):
    query = select(Product, Brand).join(Brand, Product.brand_id == Brand.id)
    if q.strip():
        term = f'%{q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")}%'
        query = query.where(or_(Product.article.ilike(term, escape='\\'),
                                Product.name.ilike(term, escape='\\'),
                                Brand.name.ilike(term, escape='\\')))
    if status:
        if status not in ('draft','review','published','archived'):
            raise HTTPException(422, 'Invalid product status')
        query = query.where(Product.status == status)
    total = int((await db.scalar(select(func.count()).select_from(query.subquery()))) or 0)
    records = (await db.execute(query.order_by(Product.created_at.desc(), Product.id)
                                .offset((page - 1) * size).limit(size))).all()
    return {'total': total, 'page': page, 'size': size, 'items': [
        {'id': as_id(p.id), 'brand': b.name, 'article': p.article, 'name': p.name,
         'status': p.status, 'slug': p.slug, 'created_at': p.created_at.isoformat()}
        for p, b in records]}


@router.get('/brands')
async def brands(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Brand).order_by(Brand.name).limit(500))).all()
    return [{'id': as_id(x.id), 'name': x.name, 'slug': x.slug} for x in rows]


@router.get('/merchants')
async def merchants(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Merchant).order_by(Merchant.name).limit(500))).all()
    return [{'id': as_id(x.id), 'name': x.name, 'slug': x.slug,
             'status': x.status, 'platform_owner': x.is_platform_owner} for x in rows]


@router.get('/stores')
async def stores(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Store, Merchant.name).join(Merchant, Store.merchant_id == Merchant.id)
                             .order_by(Store.name).limit(500))).all()
    return [{'id': as_id(s.id), 'merchant_id': as_id(s.merchant_id), 'merchant': name,
             'name': s.name, 'slug': s.slug, 'status': s.status} for s, name in rows]


@router.get('/nodes')
async def nodes(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(FulfillmentNode, Merchant.name)
                             .join(Merchant, FulfillmentNode.merchant_id == Merchant.id)
                             .order_by(FulfillmentNode.city, FulfillmentNode.name).limit(1000))).all()
    return [{'id': as_id(n.id), 'merchant_id': as_id(n.merchant_id),
             'merchant': merchant, 'store_id': as_id(n.store_id), 'code': n.code,
             'name': n.name, 'kind': n.kind, 'city': n.city, 'address': n.address,
             'timezone': n.timezone_name, 'active': n.active,
             'receiving_contact_configured': bool(n.receiving_contact_name and n.receiving_contact_phone)} for n, merchant in rows]


@router.get('/routes')
async def routes(db: AsyncSession = Depends(get_session)):
    records = (await db.execute(select(DeliveryRoute, FulfillmentNode.name, FulfillmentNode.city)
                                .join(FulfillmentNode, DeliveryRoute.source_node_id == FulfillmentNode.id)
                                .limit(500))).all()
    names = {n.id: n for n in (await db.scalars(select(FulfillmentNode))).all()}
    return [{'id': as_id(r.id), 'source_node_id': as_id(r.source_node_id),
             'source': f'{city} · {source}', 'target_node_id': as_id(r.target_node_id),
             'target': f'{names[r.target_node_id].city} · {names[r.target_node_id].name}',
             'transit_days': r.transit_days, 'handling_days': r.handling_days,
             'fee': str(r.fee), 'active': r.active} for r, source, city in records]


@router.get('/suppliers')
async def suppliers(db: AsyncSession = Depends(get_session)):
    rows = (await db.scalars(select(Supplier).order_by(Supplier.name).limit(100))).all()
    return [{'id': as_id(s.id), 'name': s.name, 'code': s.code} for s in rows]


@router.get('/accounts')
async def accounts(db: AsyncSession = Depends(get_session)):
    records = (await db.execute(select(SupplierAccount, Supplier.name, Merchant.name, Supplier.code)
                                .join(Supplier, SupplierAccount.supplier_id == Supplier.id)
                                .join(Merchant, SupplierAccount.merchant_id == Merchant.id)
                                .order_by(SupplierAccount.label).limit(500))).all()
    return [{'id': as_id(a.id), 'label': a.label, 'supplier': supplier,
             'supplier_id': as_id(a.supplier_id), 'supplier_code': supplier_code, 'merchant': merchant,
             'merchant_id': as_id(a.merchant_id), 'credential_profile': a.credential_profile,
             'enabled': a.enabled, 'read_enabled': a.read_enabled, 'procurement_enabled': a.procurement_enabled,
             'requests_per_hour': a.requests_per_hour, 'timeout_seconds': a.timeout_seconds}
            for a, supplier, merchant, supplier_code in records]


@router.get('/destinations')
async def destinations(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(SupplierDestination, SupplierAccount.label, FulfillmentNode.name, FulfillmentNode.city)
                             .join(SupplierAccount, SupplierDestination.account_id == SupplierAccount.id)
                             .join(FulfillmentNode, SupplierDestination.node_id == FulfillmentNode.id)
                             .order_by(SupplierAccount.label).limit(1000))).all()
    return [{'id': as_id(d.id), 'account_id': as_id(d.account_id), 'account': account,
             'node_id': as_id(d.node_id), 'node': node, 'city': city,
             'external_address_id': d.external_address_id, 'external_delivery_id': d.external_delivery_id,
             'external_requisite_id': d.external_requisite_id,
             'verified': d.verified, 'enabled': d.enabled} for d, account, node, city in rows]


@router.get('/offers')
async def offers(q: str = Query('', max_length=120), db: AsyncSession = Depends(get_session)):
    query = (select(SellerOffer, Store.name, Merchant.name)
             .join(Store, SellerOffer.store_id == Store.id)
             .join(Merchant, SellerOffer.merchant_id == Merchant.id))
    if q.strip():
        term = f'%{q.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")}%'
        query = query.where(SellerOffer.sku.ilike(term, escape='\\'))
    rows = (await db.execute(query.order_by(SellerOffer.created_at.desc()).limit(200))).all()
    offer_ids = [o.id for o, _, _ in rows]
    stock = (await db.execute(select(InventoryBalance.seller_offer_id,
                                     func.sum(InventoryBalance.on_hand), func.sum(InventoryBalance.reserved))
                              .where(InventoryBalance.seller_offer_id.in_(offer_ids))
                              .group_by(InventoryBalance.seller_offer_id))).all() if offer_ids else []
    levels = {oid: (int(h or 0), int(r or 0)) for oid, h, r in stock}
    return [{'id': as_id(o.id), 'merchant': merchant, 'store': store, 'sku': o.sku,
             'product_id': as_id(o.product_id), 'used_part_id': as_id(o.used_part_id),
             'source_kind': o.source_kind, 'price': str(o.price), 'status': o.status,
             'on_hand': levels.get(o.id, (0,0))[0], 'reserved': levels.get(o.id, (0,0))[1],
             'merchant_id': as_id(o.merchant_id), 'store_id': as_id(o.store_id)} for o, store, merchant in rows]


@router.get('/inventory')
async def inventory(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(InventoryBalance, SellerOffer, FulfillmentNode)
                             .join(SellerOffer, InventoryBalance.seller_offer_id == SellerOffer.id)
                             .join(FulfillmentNode, InventoryBalance.node_id == FulfillmentNode.id)
                             .order_by(FulfillmentNode.city, SellerOffer.sku).limit(500))).all()
    return [{'id': as_id(b.id), 'offer_id': as_id(o.id), 'sku': o.sku,
             'node_id': as_id(n.id), 'node': n.name, 'city': n.city,
             'on_hand': b.on_hand, 'reserved': b.reserved,
             'available': b.on_hand - b.reserved} for b, o, n in rows]


@router.get('/used')
async def used(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(UsedPart, Merchant.name).join(Merchant, UsedPart.merchant_id == Merchant.id)
                             .order_by(UsedPart.created_at.desc()).limit(200))).all()
    return [{'id': as_id(u.id), 'merchant': merchant, 'title': u.title,
             'original_product_id': as_id(u.original_product_id), 'condition': u.condition,
             'mileage_km': u.mileage_km, 'status': u.status,
             'defects': u.defects, 'photo_count': len(u.photos or [])} for u, merchant in rows]


class NewUsedPart(BaseModel):
    store_id: uuid.UUID
    original_product_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=250)
    condition: Literal['used_good','used_fair','damaged','for_parts']
    description: str = Field(default='', max_length=8000)
    defects: str = Field(default='', max_length=8000)
    mileage_km: int | None = Field(default=None, ge=0, le=3000000)


@router.post('/used', status_code=201)
async def create_used_part(body: NewUsedPart, db: AsyncSession = Depends(get_session)):
    store = await db.get(Store, body.store_id)
    if store is None:
        raise HTTPException(404, 'Store not found')
    if body.original_product_id is not None and not await db.get(Product, body.original_product_id):
        raise HTTPException(404, 'Original product not found')
    used = UsedPart(merchant_id=store.merchant_id, status='draft', photos=[],
                    **body.model_dump(exclude={'store_id'}))
    db.add(used)
    await db.commit()
    await db.refresh(used)
    return {'id': as_id(used.id), 'status': used.status}


class NewOwnOffer(BaseModel):
    store_id: uuid.UUID
    sku: str = Field(min_length=2, max_length=120)
    product_id: uuid.UUID | None = None
    used_part_id: uuid.UUID | None = None
    price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @model_validator(mode='after')
    def one_product(self):
        if (self.product_id is None) == (self.used_part_id is None):
            raise ValueError('Exactly one product or used part is required')
        return self


@router.post('/offers', status_code=201)
async def create_own_offer(body: NewOwnOffer, db: AsyncSession = Depends(get_session)):
    store = await db.get(Store, body.store_id)
    if not store or store.status != 'active':
        raise HTTPException(409, 'Store not active')
    if body.product_id:
        product = await db.get(Product, body.product_id)
        if not product or product.status != 'published':
            raise HTTPException(409, 'Product must be published')
    if body.used_part_id:
        part = await db.get(UsedPart, body.used_part_id)
        if not part or part.status != 'published' or part.merchant_id != store.merchant_id:
            raise HTTPException(409, 'Used part must be published and owned by the store merchant')
    obj = SellerOffer(merchant_id=store.merchant_id, store_id=store.id,
                      sku=body.sku, source_kind='own_stock', price=body.price,
                      product_id=body.product_id, used_part_id=body.used_part_id,
                      status='draft')
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Duplicate store SKU') from exc
    return {'id': as_id(obj.id), 'status': obj.status}


class EditOffer(BaseModel):
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    status: Literal['draft','active','paused','archived'] | None = None


@router.patch('/offers/{offer_id}')
async def edit_offer(offer_id: uuid.UUID, patch: EditOffer, db: AsyncSession = Depends(get_session)):
    offer = await db.get(SellerOffer, offer_id)
    if offer is None:
        raise HTTPException(404, 'Offer not found')
    if patch.status == 'active':
        if offer.product_id:
            p = await db.get(Product, offer.product_id)
            if not p or p.status != 'published':
                raise HTTPException(409, 'Product not published')
        if offer.used_part_id:
            u = await db.get(UsedPart, offer.used_part_id)
            if not u or u.status != 'published':
                raise HTTPException(409, 'Used part not published')
    if patch.price is not None:
        offer.price = patch.price
    if patch.status is not None:
        offer.status = patch.status
    await db.commit()
    if offer.product_id:
        await invalidate(offer.product_id)
    return {'id': as_id(offer.id), 'status': offer.status, 'price': str(offer.price)}


class StockChange(BaseModel):
    node_id: uuid.UUID
    on_hand: int = Field(ge=0, le=1_000_000)


@router.put('/offers/{offer_id}/stock')
async def update_stock(offer_id: uuid.UUID, body: StockChange, db: AsyncSession = Depends(get_session)):
    offer = await db.get(SellerOffer, offer_id)
    node = await db.get(FulfillmentNode, body.node_id)
    if not offer or not node:
        raise HTTPException(404, 'Offer or node missing')
    if offer.source_kind != 'own_stock' or offer.merchant_id != node.merchant_id or not node.active:
        raise HTTPException(409, 'Only merchant-owned warehouse stock can be edited')
    if node.kind not in ('warehouse', 'warehouse_pickup'):
        raise HTTPException(409, 'Pickup-only nodes cannot store stock')
    balance = await db.scalar(select(InventoryBalance).where(
        InventoryBalance.seller_offer_id == offer.id, InventoryBalance.node_id == node.id).with_for_update())
    if balance is None:
        balance = InventoryBalance(seller_offer_id=offer.id, node_id=node.id, on_hand=0, reserved=0)
        db.add(balance)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, 'Stock was created concurrently, please retry') from exc
    if body.on_hand < balance.reserved:
        raise HTTPException(409, 'Quantity cannot fall below existing reservations')
    delta = body.on_hand - balance.on_hand
    balance.on_hand = body.on_hand
    if delta:
        db.add(InventoryMovement(balance_id=balance.id, delta_on_hand=delta, delta_reserved=0,
                                 reason='admin_stock_adjustment',
                                 idempotency_key='admin-stock:' + str(uuid.uuid4())))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Stock changed concurrently, please retry') from exc
    if offer.product_id:
        await invalidate(offer.product_id)
    return {'offer_id': as_id(offer.id), 'node_id': as_id(node.id),
            'available': balance.on_hand - balance.reserved}
