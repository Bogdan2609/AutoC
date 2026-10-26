"""SEO-safe catalog and local-offer product pages; no blocking supplier calls."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.config import get_settings
from app.modules.catalog.models import Brand, Product, ProductSlugHistory
from app.modules.catalog.service import find_published
from app.modules.offer_engine.cache import product_offers
from app.modules.offer_engine.service import enqueue_product
from app.modules.offer_engine.discovery import precise_query, validate_pair, enqueue_discovery
from app.presentation.browser_security import browser_user
from app.presentation.shop import html

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
@router.get("/catalog", response_class=HTMLResponse)
async def catalog(
    request: Request,
    q: str = Query(default="", max_length=110),
    brand_name: str = Query(default="", max_length=60),
    article: str = Query(default="", max_length=70),
    page: int = Query(default=1, ge=1, le=251),
    city: str = Query(default="", max_length=120),
    brand: str | None = Query(default=None, max_length=180, pattern=r"^[a-z0-9][a-z0-9-]*$"),
    session: AsyncSession = Depends(get_session),
):
    user = await browser_user(request, session)
    discovery = None
    discovery_error = None
    pair = None
    if brand_name or article:
        try:
            pair = validate_pair(brand_name, article)
            q = f'{pair[0]} {pair[1]}'
        except HTTPException:
            discovery_error = 'Укажите бренд и артикул (не менее 4 символов и с цифрой).'
    elif q.strip():
        pair = precise_query(q)
    if pair:
        try:
            # No supplier network requests here: durable DB job only.
            ip = request.client.host if request.client else 'unknown'
            discovery = await enqueue_discovery(session, pair[0], pair[1], ip,city=city or None)
        except HTTPException as exc:
            discovery_error = str(exc.detail)
    per_page = 20
    products, count = await find_published(
        session, q, limit=per_page, offset=(page - 1) * per_page, brand_slug=brand,
    )
    from app.modules.catalog_ext.models import Category
    visible_categories = (await session.scalars(select(Category).where(Category.active == True).order_by(Category.position, Category.name).limit(60))).all()
    brands = (await session.scalars(
        select(Brand).join(Product, Product.brand_id == Brand.id)
        .where(Product.status == "published").distinct().order_by(Brand.name)
    )).all()
    return html(
        request, "catalog.html", user=user, products=products, q=q, count=count,
        page=page, per_page=per_page, brand=brand or "", brands=brands, visible_categories=visible_categories,
        discovery=discovery, discovery_error=discovery_error, city=city,
        title="Каталог автозапчастей",
    )


@router.get('/category/{slug}', response_class=HTMLResponse)
async def category_page(slug: str, request: Request,
                        attr: list[str] = Query(default=[], max_length=510),
                        page: int = Query(default=1, ge=1, le=251),
                        session: AsyncSession = Depends(get_session)):
    """Server-rendered filters from the category's typed template.

    No supplier HTTP. Filter combinations remain noindex in the test environment;
    this endpoint does not generate crawlable SEO landing pages.
    """
    from urllib.parse import urlencode
    from app.modules.catalog_ext.models import Category
    from app.modules.catalog_ext.catalog_api import filter_products
    from app.modules.catalog_ext.category_facets import category_facets
    if len(attr)>8 or any(len(value)>180 for value in attr):
        raise HTTPException(status_code=422,detail='At most 8 filters, each up to 180 characters')
    category = await session.scalar(select(Category).where(Category.slug == slug, Category.active == True))
    if category is None:
        raise HTTPException(status_code=404, detail='Category not found')
    rows = await filter_products(category_id=category.id, brand_id=None, q='',
                                 attr=attr, limit=20, offset=(page-1)*20, db=session)
    facets = await category_facets(category.id,db=session)
    # Fetch the category page in one SQL statement and preserve relevance order.
    ids = [uuid.UUID(r['id']) for r in rows['items']]
    by_id = {product.id:product for product in (await session.scalars(
        select(Product).where(Product.id.in_(ids)))).all()} if ids else {}
    products = [by_id[product_id] for product_id in ids if product_id in by_id]
    base='/category/'+slug
    def page_link(n):
        return base+'?'+urlencode([('page',str(n))]+[('attr',v) for v in attr])
    return html(request,'category.html',user=await browser_user(request,session),
                category=category,products=products,facets=facets['facets'],applied=attr,
                page=page,count=rows['count'],previous_page=page_link(page-1),
                next_page=page_link(page+1),title=category.name,
                canonical_url=get_settings().public_base_url.rstrip('/')+base)


@router.get("/product/{slug}", response_class=HTMLResponse)
async def product_page(slug: str, request: Request,
                       pickup: str | None = Query(default=None,max_length=36),
                       city: str = Query(default="",max_length=120),
                       session: AsyncSession = Depends(get_session)):
    product = await session.scalar(select(Product).where(Product.slug == slug,
                                                       Product.status == "published"))
    if product is None:
        historical = await session.scalar(
            select(ProductSlugHistory).where(ProductSlugHistory.old_slug == slug))
        if historical:
            old_product = await session.get(Product, historical.product_id)
            if old_product and old_product.status == "published":
                historical_brand = await session.get(Brand, old_product.brand_id)
                if historical_brand and historical_brand.name_norm != 'CATALOG FIXTURE':
                    return RedirectResponse(f"/product/{old_product.slug}", status_code=301)
        raise HTTPException(status_code=404, detail="Product not found")
    brand = await session.get(Brand, product.brand_id)
    if brand and brand.name_norm == 'CATALOG FIXTURE':
        raise HTTPException(status_code=404, detail='Product not found')
    await enqueue_product(session, product, brand)
    # Keep the old single-supplier UI only where NO independent API account owns
    # the supplier; never combine unscoped legacy snapshots with verified accounts.
    from app.modules.suppliers.models import Supplier
    from app.modules.marketplace.models import SupplierAccount
    legacy_offers = await product_offers(session, product.id)
    configured_codes=set((await session.scalars(select(Supplier.code).join(
        SupplierAccount,SupplierAccount.supplier_id==Supplier.id))).all())
    legacy_offers=[o for o in legacy_offers if o.supplier_code not in configured_codes]
    from app.modules.marketplace.api import marketplace_offers, used_list, public_nodes
    import uuid
    selected=None
    if pickup:
        try:selected=uuid.UUID(pickup)
        except ValueError:raise HTTPException(422,'Invalid pickup ID')
    nodes=await public_nodes(city or None,session)
    market=await marketplace_offers(product.id,city=city or None,pickup_node_id=selected,db=session)
    from app.modules.marketplace.grouping import group_marketplace_offers
    groups=group_marketplace_offers(market['offers'])
    used=await used_list(product.id,db=session)
    # Site-wide noindex is intentionally preserved in the demo environment.
    # SEO metadata and direct canonical are ready for a separately approved production rollout.
    base_url = get_settings().public_base_url.rstrip('/')
    return html(request, "product.html", user=await browser_user(request, session),
                seo_title=product.seo_title, seo_description=product.seo_description,
                seo_h1=product.seo_h1, seo_noindex=product.seo_noindex,
                canonical_url=base_url + '/product/' + product.slug,
                product=product, legacy_offers=legacy_offers, marketplace_offers=market['offers'], marketplace_groups=groups,
                used_parts=used,pickup_nodes=nodes,selected_pickup=pickup or '',city=city,
                title=product.name)


@router.get("/robots.txt")
async def robots():
    return HTMLResponse("User-agent: *\nDisallow: /\n", media_type="text/plain")


@router.get('/used/{used_id}',response_class=HTMLResponse)
async def used_page(used_id:uuid.UUID, request:Request,
                    db:AsyncSession=Depends(get_session)):
    from app.modules.marketplace.models import UsedPart,SellerOffer
    from app.modules.marketplace.service import options_for_offer,offer_check
    used=await db.get(UsedPart,used_id)
    if not used or used.status!='published':raise HTTPException(404,'Used part not found')
    offers=(await db.scalars(select(SellerOffer).where(
        SellerOffer.used_part_id==used.id,SellerOffer.status=='active'))).all()
    available=[]
    for offer in offers:
        try:await offer_check(db,offer.id)
        except HTTPException:continue
        options=await options_for_offer(db,offer)
        if options:available.append({'offer':offer,'options':options})
    original=await db.get(Product,used.original_product_id) if used.original_product_id else None
    return html(request,'used.html',user=await browser_user(request,db),used=used,
                original=original,available=available,title=used.title)
