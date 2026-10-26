"""Catalog identity and published-product discovery.

Search intentionally uses portable SQLAlchemy queries.  Exact SKU matches are
ranked before prefixes, followed by combined brand+SKU and text matches.
Supplier network operations do not occur on the read path.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Brand, Product, ProductSlugHistory, ProductChangeLog
from app.modules.catalog.normalize import NORMALIZER_VERSION, make_slug, normalize_article, normalize_brand
from app.modules.catalog.schemas import BrandCreate, ProductCreate


async def create_brand(session: AsyncSession, payload: BrandCreate) -> Brand:
    # Prevent new canonical brands from shadowing already verified aliases.
    from app.modules.catalog_ext.models import BrandAlias
    normalized = normalize_brand(payload.name)
    collision = await session.scalar(select(BrandAlias.id).where(BrandAlias.alias_norm == normalized, BrandAlias.verified == True))
    if collision is not None:
        raise HTTPException(status_code=409, detail='Brand name matches a verified alias; review brand governance')
    brand = Brand(name=payload.name.strip(), name_norm=normalize_brand(payload.name), slug=payload.slug)
    session.add(brand)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Brand name or slug already exists") from exc
    await session.refresh(brand)
    return brand


async def create_product(session: AsyncSession, payload: ProductCreate) -> Product:
    brand = await session.get(Brand, payload.brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found")
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Product name cannot be blank")
    normalized = normalize_article(payload.article)
    if not normalized:
        raise HTTPException(status_code=422, detail="Article cannot be empty after normalization")
    product = Product(
        brand_id=brand.id, article=payload.article.strip(), article_norm=normalized,
        normalizer_version=NORMALIZER_VERSION, name=payload.name.strip(),
        description=payload.description,
        slug=make_slug(brand.slug, normalized), status="draft",
    )
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Product identity or slug already exists") from exc
    await session.refresh(product)
    return product


async def publish_product(session: AsyncSession, product_id: uuid.UUID) -> Product:
    from app.core.config import get_settings
    from app.modules.catalog.publication_policy import publication_issues
    from app.modules.master_catalog.models import ProductComposition
    from app.modules.catalog_ext.catalog_api import required_missing

    # Lock Product before validating category/attribute prerequisites in the same
    # transaction. All R7 category membership writes acquire the same lock.
    product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(status_code=404, detail='Product not found')
    if product.status == 'archived':
        raise HTTPException(status_code=409, detail='Archived product cannot be published')
    brand = await session.get(Brand, product.brand_id)
    if brand and brand.name_norm == 'CATALOG FIXTURE':
        raise HTTPException(status_code=409, detail='Synthetic QA products cannot be published')
    comp = await session.get(ProductComposition, product_id)
    if comp is not None and comp.kind == 'assembled_plan':
        raise HTTPException(status_code=409, detail='Assembled plan cannot be published before atomic component reservation is implemented')
    missing = await required_missing(session, product_id)
    if missing:
        raise HTTPException(status_code=409, detail='Required catalog attributes are missing: ' + ', '.join(missing))
    # Legacy disposable-test fixtures may publish identity-only products to test
    # unrelated modules. Development/staging/production always enforce readiness.
    if get_settings().app_env != 'test':
        issues = await publication_issues(session, product)
        errors = [x for x in issues if x['severity'] == 'error']
        if errors:
            raise HTTPException(status_code=409, detail={
                'code': 'PRODUCT_NOT_READY', 'issues': errors})
    if product.status != 'published':
        old_status = product.status
        product.status = 'published'
        session.add(ProductChangeLog(product_id=product.id,action='publish_manual',
            changes={'status':{'old':old_status,'new':'published'},
                     'origin':'single_product_admin_endpoint',
                     'actor':'shared_admin_token; individual actor identity not available'}))
    await session.commit()
    await session.refresh(product)
    return product


async def change_product_slug(session: AsyncSession, product_id: uuid.UUID, new_slug: str) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if new_slug == product.slug:
        return product
    old_slug = await session.scalar(select(ProductSlugHistory.id).where(ProductSlugHistory.old_slug == new_slug))
    if old_slug is not None:
        raise HTTPException(status_code=409, detail="Slug is reserved by redirect history")
    old = product.slug
    product.slug = new_slug
    session.add(ProductSlugHistory(product_id=product.id, old_slug=old))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Slug is already reserved") from exc
    await session.refresh(product)
    return product


async def get_published(session: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await session.get(Product, product_id)
    if product is None or product.status != "published":
        raise HTTPException(status_code=404, detail="Product not found")
    brand = await session.get(Brand, product.brand_id)
    if brand and brand.name_norm == 'CATALOG FIXTURE':
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def escape_like(value: str) -> str:
    """Treat user-supplied percent and underscore characters as literal text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_filter(query: str):
    """Build a brand-aware search without conflating brand/product identities."""
    normalized = normalize_article(query)
    # Brand plus article together ("BOSCH 0 986 479 088") is a common shop query.
    brand_and_article = func.replace(Brand.name_norm, " ", "") + Product.article_norm
    matches = []
    if normalized:
        key = escape_like(normalized)
        matches.extend([
            Product.article_norm.like(f"{key}%", escape="\\"),
            brand_and_article.like(f"{key}%", escape="\\"),
        ])
    # Support mixed queries ("BOSCH диск") and word-order-independent names.
    tokens = query.split()[:12]
    if tokens:
        matches.append(and_(*[
            or_(
                Brand.name_norm.ilike(f"%{escape_like(token)}%", escape="\\"),
                Product.name.ilike(f"%{escape_like(token)}%", escape="\\"),
                Product.article_norm.like(f"%{escape_like(normalize_article(token))}%", escape="\\")
                if normalize_article(token) else False,
            ) for token in tokens
        ]))
    if not matches:
        return None
    return or_(*matches)


def _search_ranking(query: str):
    normalized = normalize_article(query)
    name = normalize_brand(query)
    joined = func.replace(Brand.name_norm, " ", "") + Product.article_norm
    prefix = escape_like(normalized)
    return case(
        (Product.article_norm == normalized, 0),
        (joined == normalized, 1),
        (Product.article_norm.like(f"{prefix}%", escape="\\"), 2),
        (joined.like(f"{prefix}%", escape="\\"), 3),
        (Brand.name_norm == name, 4),
        (Product.name.ilike(f"{escape_like(query)}%", escape="\\"), 5),
        else_=6,
    )


async def find_published(
    session: AsyncSession, query: str, limit: int, offset: int,
    *, brand_slug: str | None = None,
) -> tuple[list[Product], int]:
    """Return stable paginated published results ordered by relevance.

    No supplier API calls and no hidden mutation. The optional brand filter uses
    the canonical brand slug; the SKU is never considered globally unique.
    """
    if offset < 0 or offset > 5000:
        raise HTTPException(422, "Search offset exceeds 5000; narrow the search query")
    q = query.strip()
    stmt = select(Product).join(Brand, Brand.id == Product.brand_id).where(
        Product.status == "published", Brand.name_norm != 'CATALOG FIXTURE')
    if brand_slug:
        stmt = stmt.where(Brand.slug == brand_slug)
    if q:
        condition = _search_filter(q)
        if condition is not None:
            stmt = stmt.where(condition)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    order = (_search_ranking(q), Product.name, Product.id) if q else (Product.name, Product.id)
    products = (await session.scalars(stmt.order_by(*order).limit(limit).offset(offset))).all()
    return list(products), int(total or 0)
