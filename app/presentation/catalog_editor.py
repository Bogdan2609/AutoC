"""v0.22 operator-only catalog editing projections.

Existing catalog v0.18/v0.21 mutation endpoints remain the source of truth.
All functions are local DB/file operations; no supplier networking or purchases.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Brand, Product, ProductSlugHistory, ProductChangeLog, utcnow
from app.modules.catalog.normalize import normalize_brand
from app.modules.catalog_ext.models import (
    AttributeDefinition, BrandAlias, BrandProfile, Category, CategoryAttribute,
    ProductAttribute, ProductCategory, ProductMedia,
)
from app.modules.catalog_ext.media import info as media_info, path_for
from app.modules.catalog_ext.catalog_api import required_missing
from app.modules.marketplace.models import SellerOffer, InventoryBalance, FulfillmentNode
from app.modules.master_catalog.models import ProductFamily, FamilyMember, OfferSaleTerms
from app.modules.suppliers.models import SupplierItem, OfferSnapshot, Supplier
from app.modules.offer_engine.models import OfferDetail

router = APIRouter(prefix='/api/v1/backoffice/catalog-editor', tags=['catalog-editor'],
                   dependencies=[Depends(require_admin)])


def ident(value):
    return str(value) if value is not None else None


def fail(code: int, detail: str):
    raise HTTPException(status_code=code, detail=detail)


async def save(db):
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        fail(409, 'Catalog constraint violation or duplicate')


class BrandRename(BaseModel):
    name: str = Field(min_length=2, max_length=160)


@router.patch('/brands/{brand_id}')
async def rename_brand(brand_id: uuid.UUID, body: BrandRename, db: AsyncSession = Depends(get_session)):
    brand = await db.get(Brand, brand_id)
    if not brand: fail(404, 'Brand not found')
    norm = normalize_brand(body.name)
    if not norm or len(norm) > 160: fail(422, 'Invalid normalized brand name')
    if norm != brand.name_norm:
        duplicate = await db.scalar(select(Brand.id).where(Brand.name_norm == norm))
        collision = await db.scalar(select(BrandAlias.id).where(
            BrandAlias.alias_norm == norm, BrandAlias.verified.is_(True)))
        if duplicate or collision: fail(409, 'Canonical brand or verified alias collision')
    brand.name = body.name.strip()
    brand.name_norm = norm
    await save(db)
    return {'id': ident(brand.id), 'name': brand.name, 'slug': brand.slug}


@router.get('/brands/{brand_id}/profile')
async def brand_profile(brand_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    brand = await db.get(Brand, brand_id)
    if not brand: fail(404, 'Brand not found')
    row = await db.get(BrandProfile, brand_id)
    return {'id': ident(brand.id), 'name': brand.name, 'slug': brand.slug,
            'profile': {k: getattr(row, k) for k in ('legal_name', 'website', 'country_code', 'description', 'verified')}
            if row else {'legal_name': None, 'website': None, 'country_code': None,
                         'description': None, 'verified': False}}


@router.get('/brands/{brand_id}/media')
async def brand_media(brand_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    if not await db.get(Brand, brand_id): fail(404, 'Brand not found')
    rows = (await db.scalars(select(ProductMedia).where(ProductMedia.brand_id == brand_id)
                              .order_by(ProductMedia.position, ProductMedia.created_at))).all()
    return {'items': [media_info(m) for m in rows]}


@router.get('/categories/{category_id}/bindings')
async def category_bindings(category_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    from app.modules.catalog_ext.category_facets import effective_template
    if not await db.get(Category, category_id): fail(404, 'Category not found')
    items = await effective_template(db, category_id)
    return {'category_id': ident(category_id), 'items': [
        {**x, 'category_id': x['source_category_id']} for x in items]}


class AttributeChange(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    unit: str | None = Field(default=None, max_length=24, pattern=r'^[a-zA-Z0-9/%°._-]+$')
    enum_values: list[str] | None = Field(default=None, max_length=80)
    filterable: bool
    active: bool


@router.patch('/attributes/{attribute_id}')
async def edit_attribute(attribute_id: uuid.UUID, body: AttributeChange,
                         db: AsyncSession = Depends(get_session)):
    row = await db.get(AttributeDefinition, attribute_id)
    if not row: fail(404, 'Attribute not found')
    if body.unit and row.data_type not in ('decimal', 'integer'): fail(422, 'Unit needs numeric type')
    if row.data_type == 'enum':
        vals = body.enum_values
        if not vals or len(vals) != len(set(vals)) or any(not x or len(x) > 160 for x in vals):
            fail(422, 'Enum must contain unique nonempty values')
        used = (await db.scalars(select(ProductAttribute.value_enum).where(
            ProductAttribute.attribute_id == attribute_id))).all()
        if any(v not in vals for v in used): fail(409, 'Existing product values are not in new enum')
        row.enum_values = vals
    elif body.enum_values is not None: fail(422, 'Non-enum attribute cannot have options')
    row.name, row.unit, row.filterable, row.active = body.name, body.unit, body.filterable, body.active
    await save(db)
    return {'id': ident(row.id), 'name': row.name, 'data_type': row.data_type}


class BindingChange(BaseModel):
    required: bool


@router.put('/categories/{category_id}/attributes/{attribute_id}')
async def update_binding(category_id: uuid.UUID, attribute_id: uuid.UUID, body: BindingChange,
                         db: AsyncSession = Depends(get_session)):
    row = await db.scalar(select(CategoryAttribute).where(CategoryAttribute.category_id == category_id,
                                                       CategoryAttribute.attribute_id == attribute_id))
    if not row: fail(404, 'Category attribute binding not found')
    row.required = body.required
    await save(db)
    return {'category_id': ident(category_id), 'attribute_id': ident(attribute_id), 'required': row.required}


@router.get('/products/{product_id}/identity')
async def product_identity(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    product = await db.get(Product, product_id)
    if not product: fail(404, 'Product not found')
    brand = await db.get(Brand, product.brand_id)
    cats = (await db.scalars(select(ProductCategory).where(ProductCategory.product_id == product_id))).all()
    attrs = (await db.scalars(select(ProductAttribute).where(ProductAttribute.product_id == product_id))).all()
    values = []
    for x in attrs:
        definition = await db.get(AttributeDefinition, x.attribute_id)
        if definition:
            value = getattr(x, 'value_' + {'text': 'text', 'integer': 'integer', 'decimal': 'decimal',
                        'boolean': 'boolean', 'enum': 'enum'}[definition.data_type])
            values.append({'attribute_id': ident(x.attribute_id), 'code': definition.code,
                           'data_type': definition.data_type,
                           'value': str(value) if definition.data_type == 'decimal' else value})
    return {'id': ident(product.id), 'brand_id': ident(product.brand_id),
            'brand': brand.name, 'article': product.article, 'article_norm': product.article_norm,
            'slug': product.slug, 'name': product.name, 'description': product.description,
            'status': product.status, 'updated_at': product.updated_at.isoformat() if product.updated_at else None,
            'seo_title': product.seo_title, 'seo_description': product.seo_description,
            'seo_h1': product.seo_h1, 'seo_noindex': product.seo_noindex,
            'categories': [{'category_id': ident(x.category_id), 'is_primary': x.is_primary} for x in cats],
            'attributes': values, 'missing_required': await required_missing(db, product_id)}


class ProductContent(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=10000)


@router.patch('/products/{product_id}/content')
async def edit_product_content(product_id: uuid.UUID, body: ProductContent,
                               db: AsyncSession = Depends(get_session)):
    row = await db.get(Product, product_id)
    if not row: fail(404, 'Product not found')
    row.name, row.description = body.name.strip(), body.description
    await save(db)
    return {'id': ident(row.id), 'name': row.name, 'status': row.status}




class ProductWorkspaceChange(BaseModel):
    """Single atomic save for identity presentation and SEO, never a supplier edit."""
    name: str = Field(min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    slug: str = Field(min_length=3, max_length=199, pattern=r'^[a-z0-9][a-z0-9-]*$')
    seo_title: str | None = Field(default=None, max_length=250)
    seo_description: str | None = Field(default=None, max_length=500)
    seo_h1: str | None = Field(default=None, max_length=300)
    seo_noindex: bool = False
    expected_updated_at: str


def clean_optional(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


@router.get('/products/{product_id}/slug-history')
async def slug_history(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    product = await db.get(Product, product_id)
    if product is None: fail(404, 'Product not found')
    history = (await db.scalars(select(ProductSlugHistory).where(
        ProductSlugHistory.product_id == product_id).order_by(
        ProductSlugHistory.changed_at.desc(), ProductSlugHistory.id.desc()))).all()
    return {'product_id': str(product_id), 'current_slug': product.slug,
            'items': [{'slug': h.old_slug, 'changed_at': h.changed_at.isoformat(),
                       'redirect_to': '/product/' + product.slug, 'status_code': 301} for h in history]}


@router.get('/products/{product_id}/change-history')
async def change_history(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    if not await db.get(Product, product_id): fail(404, 'Product not found')
    rows = (await db.scalars(select(ProductChangeLog).where(
        ProductChangeLog.product_id == product_id).order_by(ProductChangeLog.created_at.desc()).limit(100))).all()
    return {'items': [{'action': x.action, 'changes': x.changes, 'changed_at': x.created_at.isoformat()}
                      for x in rows]}


@router.get('/products/{product_id}/slug-availability')
async def slug_available(product_id: uuid.UUID, slug: str = Query(min_length=3, max_length=199,
                                       pattern=r'^[a-z0-9][a-z0-9-]*$'),
                         db: AsyncSession = Depends(get_session)):
    product = await db.get(Product, product_id)
    if product is None: fail(404, 'Product not found')
    if slug == product.slug:
        return {'slug': slug, 'available': True, 'reason': 'current'}
    collision = await db.scalar(select(Product.id).where(Product.slug == slug))
    reserved = await db.scalar(select(ProductSlugHistory.id).where(ProductSlugHistory.old_slug == slug))
    return {'slug': slug, 'available': collision is None and reserved is None,
            'reason': 'in_use' if collision else ('history' if reserved else 'available')}


@router.patch('/products/{product_id}/workspace')
async def save_product_workspace(product_id: uuid.UUID, body: ProductWorkspaceChange,
                                 db: AsyncSession = Depends(get_session)):
    # SELECT FOR UPDATE avoids losing concurrent operator changes to product metadata.
    product = await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None: fail(404, 'Product not found')
    if product.updated_at is None or body.expected_updated_at != product.updated_at.isoformat():
        fail(409, 'Product changed in another session. Reload before saving.')
    fields=('name','description','slug','seo_title','seo_description','seo_h1','seo_noindex')
    requested={**body.model_dump(include=set(fields))}
    requested['name']=body.name.strip()
    for field in ('description','seo_title','seo_description','seo_h1'):
        requested[field]=clean_optional(requested[field])
    changes={field:{'old':getattr(product,field),'new':requested[field]}
             for field in fields if getattr(product,field)!=requested[field]}
    if product.slug != body.slug:
        reserved = await db.scalar(select(ProductSlugHistory.id).where(
            ProductSlugHistory.old_slug == body.slug))
        existing = await db.scalar(select(Product.id).where(Product.slug == body.slug))
        if reserved is not None or existing is not None:
            fail(409, 'Slug is already in use or reserved by redirect history')
        db.add(ProductSlugHistory(product_id=product.id, old_slug=product.slug))
        product.slug = body.slug
    product.name = body.name.strip()
    product.description = clean_optional(body.description)
    product.seo_title = clean_optional(body.seo_title)
    product.seo_description = clean_optional(body.seo_description)
    product.seo_h1 = clean_optional(body.seo_h1)
    product.seo_noindex = body.seo_noindex
    if changes:
        db.add(ProductChangeLog(product_id=product.id, action='workspace_save', changes=changes))
        product.updated_at = utcnow()
    await save(db)
    return {'id': ident(product.id), 'slug': product.slug,
            'status': product.status, 'updated_at': product.updated_at.isoformat()}


@router.post('/products/{product_id}/unpublish')
async def unpublish_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    product = await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None: fail(404, 'Product not found')
    if product.status not in ('published', 'review'):
        fail(409, 'Only published/review Products may be moved to draft')
    old_status=product.status
    product.status = 'draft'
    db.add(ProductChangeLog(product_id=product.id, action='unpublish', changes={'status':{'old':old_status,'new':'draft'}}))
    product.updated_at = utcnow()
    await save(db)
    return {'id': ident(product.id), 'status': product.status}


@router.get('/families')
async def families(q: str = Query('', max_length=120), offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_session)):
    stmt = select(ProductFamily)
    if q.strip():
        term = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        stmt = stmt.where(ProductFamily.name.ilike(term, escape='\\'))
    count = int((await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0)
    rows = (await db.scalars(stmt.order_by(ProductFamily.name).offset(offset).limit(limit))).all()
    return {'total': count, 'items': [{'id': ident(x.id), 'name': x.name, 'slug': x.slug,
            'kind': x.kind, 'axes': x.axes} for x in rows]}


@router.get('/families/{family_id}')
async def family_detail(family_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    row = await db.get(ProductFamily, family_id)
    if not row: fail(404, 'Family not found')
    members = (await db.scalars(select(FamilyMember).where(FamilyMember.family_id == family_id))).all()
    return {'id': ident(row.id), 'name': row.name, 'axes': row.axes, 'kind': row.kind, 'members': [
        {'product_id': ident(m.product_id), 'axis_values': m.axis_values,
         'name': (await db.get(Product, m.product_id)).name} for m in members]}


@router.get('/media')
async def operator_media(product_id: uuid.UUID | None = None, brand_id: uuid.UUID | None = None,
                         db: AsyncSession = Depends(get_session)):
    if (product_id is None) == (brand_id is None): fail(422, 'Select one media owner')
    if product_id and not await db.get(Product, product_id): fail(404, 'Product not found')
    if brand_id and not await db.get(Brand, brand_id): fail(404, 'Brand not found')
    stmt = select(ProductMedia).where(ProductMedia.product_id == product_id) if product_id else \
        select(ProductMedia).where(ProductMedia.brand_id == brand_id)
    rows = (await db.scalars(stmt.order_by(ProductMedia.position, ProductMedia.created_at))).all()
    return {'items': [media_info(x) for x in rows]}


@router.get('/media/{media_id}/preview')
async def media_preview(media_id: uuid.UUID, variant: Literal['original', 'thumb'] = 'thumb',
                        db: AsyncSession = Depends(get_session)):
    row = await db.get(ProductMedia, media_id)
    if not row: fail(404, 'Media not found')
    if row.kind == 'document' and variant == 'thumb': fail(404, 'Preview not available')
    filepath = path_for(row.storage_key, variant)
    if not filepath.is_file(): fail(404, 'Media bytes unavailable')
    response = FileResponse(filepath, media_type=row.mime_type,
                            filename=row.original_filename if row.kind == 'document' else None,
                            content_disposition_type='attachment' if row.kind == 'document' else 'inline')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@router.get('/products/{product_id}/sale-terms')
async def product_sale_terms(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    if not await db.get(Product, product_id): fail(404, 'Product not found')
    offers = (await db.scalars(select(SellerOffer).where(SellerOffer.product_id == product_id).limit(200))).all()
    supplier_count = int((await db.scalar(select(func.count()).select_from(SupplierItem).where(
        SupplierItem.product_id == product_id))) or 0)
    snapshots = (await db.execute(select(OfferSnapshot, SupplierItem, Supplier, OfferDetail)
        .join(SupplierItem, SupplierItem.id == OfferSnapshot.supplier_item_id)
        .join(Supplier, Supplier.id == SupplierItem.supplier_id)
        .outerjoin(OfferDetail, OfferDetail.snapshot_id == OfferSnapshot.id)
        .where(SupplierItem.product_id == product_id)
        .order_by(OfferSnapshot.observed_at.desc(), OfferSnapshot.id.desc()).limit(100))).all()
    observed = [{'snapshot_id': ident(s.id), 'supplier': sup.name, 'supplier_code': sup.code,
                 'supplier_item_id': ident(item.id), 'raw_article': item.raw_article,
                 'matching': item.match_status,
                 'account_id': ident(s.supplier_account_id), 'destination_id': ident(s.supplier_destination_id),
                 'warehouse': detail.warehouse if detail else None,
                 'purchase_price': str(s.purchase_price), 'currency': s.currency,
                 'tax_status': s.tax_status, 'quantity': s.quantity,
                 'observed_at': s.observed_at.isoformat(), 'lead_time_days': s.lead_time_days,
                 'verified_live_at_observation': detail.verified_live if detail else False}
                for s, item, sup, detail in snapshots]
    result = []
    for offer in offers:
        scope = 'seller:' + str(offer.id)
        terms = await db.scalar(select(OfferSaleTerms).where(OfferSaleTerms.scope_key == scope))
        balances = (await db.execute(select(InventoryBalance, FulfillmentNode)
                    .join(FulfillmentNode, FulfillmentNode.id == InventoryBalance.node_id)
                    .where(InventoryBalance.seller_offer_id == offer.id)
                    .order_by(FulfillmentNode.city, FulfillmentNode.name))).all()
        stocks = [{'node_id': ident(n.id), 'node': n.name, 'city': n.city,
                   'on_hand': b.on_hand, 'reserved': b.reserved,
                   'available': b.on_hand - b.reserved} for b, n in balances]
        result.append({'offer_id': ident(offer.id), 'sku': offer.sku, 'status': offer.status,
                       'price': str(offer.price), 'stock_by_node': stocks, 'terms': {
                'minimum_units': terms.minimum_units, 'step_units': terms.step_units,
                'units_per_package': terms.units_per_package, 'order_unit': terms.order_unit,
                'status': terms.status} if terms else None})
    return {'seller_offers': result, 'supplier_item_count': supplier_count,
            'supplier_offer_snapshots': observed,
            'note': 'Historical supplier snapshots are not live availability. Supplier accounts, destinations, warehouses and costs are distinct.'}

# v0.23 R4: guarded attribute mutations share the existing operator auth dependency.
from app.presentation.attribute_management_v0234 import router as attribute_management_router
router.include_router(attribute_management_router)
