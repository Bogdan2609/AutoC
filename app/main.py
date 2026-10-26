from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from app.core.rate_limit import enforce_budget,peer_ip
from app.modules.admin_staff.api import router as admin_staff_router
from app.modules.analytics.api import router as analytics_a1_router
from app.modules.analytics.a2 import router as analytics_a2_router
from app.modules.analytics.a3 import router as analytics_a3_router
from app.modules.finance.api import router as finance_f1_router
from app.modules.finance.pricing_api import router as finance_f2_router
from app.modules.finance.ledger_api import router as finance_f3_router
from app.modules.finance.payment_sim_api import router as finance_f4_router
from app.modules.finance.register_api import router as finance_f5_router
from app.modules.marketplace.procurement import router as real_supplier_purchase_router
from app.modules.commerce.sale_lifecycle import admin as sales_admin, buyer as sales_buyer
from app.modules.commerce.quote_workflow import admin as operator_quote_admin, buyer as operator_quote_buyer
from app.modules.commerce.operations_integrity import router as commerce_integrity_router
from app.modules.marketplace.account_probe import router as supplier_probe_router
from app.modules.marketplace.api import admin as marketplace_admin, seller as marketplace_seller, public as marketplace_public
from app.modules.marketplace.cart import router as marketplace_cart
from app.modules.marketplace.demo_orders import router as marketplace_demo
from app.modules.marketplace.checkout_v017 import router as marketplace_checkout_v017,admin as marketplace_request_admin_v017
from app.presentation.seller_cabinet import router as seller_cabinet_router
from sqlalchemy.exc import SQLAlchemyError
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core.db import get_session_factory
from app.modules.catalog.router import router as catalog_router
from app.modules.suppliers.router import router as suppliers_router
from app.presentation.backoffice import router as backoffice_router
from app.presentation.operations import router as operations_router, ui_router as operator_ui_router
from app.presentation.admin_workspace import router as operator_workspace_router
from app.presentation.operator_monitor import router as operator_monitor_router
from app.presentation.catalog_editor import router as catalog_editor_router
from app.presentation.admin_spa import router as admin_spa_router, DIST as ADMIN_DIST
from app.modules.accounts.api import router as accounts_router
from app.modules.commerce.api import router as commerce_router
from app.modules.payments.api import router as payments_router
from app.modules.offer_engine.api import router as cache_router
from app.modules.offer_engine.discovery_api import router as discovery_router
from app.modules.offer_engine.operations import router as offer_ops_router
from app.presentation.storefront import router as storefront_router
from app.presentation.shop import router as shop_router
from app.modules.supplier_feeds.api import router as supplier_feeds_router
from app.modules.supplier_feeds.control_api import router as supplier_feed_control_router
from app.modules.catalog_bulk.api import router as catalog_bulk_router
from app.modules.catalog_enrichment.api import router as catalog_enrichment_router
from app.modules.multi_enrichment.api import router as multi_enrichment_router
from app.modules.multi_enrichment.taxonomy_api import router as classification_taxonomy_router
from app.modules.draft_materialization.api import router as draft_materialization_router
from app.modules.draft_materialization.quality_api import router as semantic_quality_router
from app.modules.draft_materialization.finalization_api import router as catalog_quality_finalization_router
from app.modules.draft_materialization.automation_api import router as catalog_auto_pipeline_router

from app.modules.master_catalog.api import admin as master_catalog_admin, public as master_catalog_public
from app.modules.catalog_ext.catalog_api import admin as catalog_ext_admin, public as catalog_ext_public
from app.modules.catalog_ext.media import admin as media_admin, public as media_public
from app.modules.catalog_ext.category_facets import admin as category_template_admin, public as category_facets_public

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.modules.offer_engine.secret_redaction import install_redaction
    install_redaction()
    try:
        yield
    finally:
        from app.modules.offer_engine.cache import close_client
        await close_client()
        from app.modules.offer_engine.http_pool import close_clients
        await close_clients()
        from app.modules.offer_engine.soap_client import close_soap_clients
        await close_soap_clients()

from importlib.metadata import PackageNotFoundError, version as package_version

try:
    api_version = package_version('autoparts-platform-v2')
except PackageNotFoundError:
    api_version = '0.33.2'  # local source checkout without installed wheel

app = FastAPI(title="Autoparts Platform v2", version=api_version, lifespan=lifespan)
@app.middleware('http')
async def bound_public_requests(request:Request,call_next):
    path=request.url.path
    is_expensive=(path.startswith('/api/v1/marketplace/products/') and
        ('/offers' in path)) or (path.startswith('/api/v1/catalog-v021/categories/') and
        path.endswith('/facets'))
    is_webhook=(path=='/api/v1/payments/yookassa/webhook' and request.method=='POST')
    if is_expensive or is_webhook:
        # Ingress must also reject chunked/oversized bodies; this is a local guard.
        length=request.headers.get('content-length')
        limit_bytes = 16384 if is_webhook else 65536
        if length is not None and (not length.isdecimal() or int(length)>limit_bytes):
            return JSONResponse({'detail':'Request body too large'},status_code=413)
        # Content-Length is not authoritative for chunked/streaming requests.
        # Bound memory BEFORE parsing any untrusted JSON, even without a header.
        collected = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit_bytes:
                return JSONResponse({'detail':'Request body too large'},status_code=413)
            collected.append(chunk)
        request._body = b''.join(collected)
        try:
            await enforce_budget('webhook' if is_webhook else 'public_costly',peer_ip(request),
                limit=60 if is_webhook else 20,period=60)
        except HTTPException as exc:
            return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
    return await call_next(request)

app.mount("/static", StaticFiles(directory="static"), name="static")
# New SPA is optional for source-only unit tests; Docker production build includes it.
if (ADMIN_DIST / "assets").is_dir():
    app.mount("/admin/assets", StaticFiles(directory=str(ADMIN_DIST / "assets")), name="admin-assets")
app.include_router(admin_staff_router)
app.include_router(finance_f1_router)
app.include_router(finance_f2_router)
app.include_router(finance_f3_router)
app.include_router(finance_f4_router)
app.include_router(finance_f5_router)
app.include_router(analytics_a1_router)
app.include_router(analytics_a2_router)
app.include_router(analytics_a3_router)
app.include_router(catalog_router)
app.include_router(master_catalog_admin)
app.include_router(master_catalog_public)
app.include_router(catalog_ext_admin)
app.include_router(category_template_admin)
app.include_router(category_facets_public)
app.include_router(catalog_ext_public)
app.include_router(media_admin)
app.include_router(media_public)
app.include_router(suppliers_router)
app.include_router(backoffice_router)
app.include_router(operations_router)
app.include_router(operator_workspace_router)
app.include_router(operator_monitor_router)
app.include_router(catalog_editor_router)
app.include_router(operator_ui_router)
app.include_router(accounts_router)
app.include_router(commerce_router)
app.include_router(payments_router)
app.include_router(cache_router)
app.include_router(discovery_router)
app.include_router(offer_ops_router)
app.include_router(storefront_router)
app.include_router(shop_router)
app.include_router(supplier_feeds_router)
app.include_router(supplier_feed_control_router)
app.include_router(catalog_bulk_router)
app.include_router(catalog_enrichment_router)
app.include_router(multi_enrichment_router)
app.include_router(classification_taxonomy_router)
app.include_router(draft_materialization_router)
app.include_router(semantic_quality_router)
app.include_router(catalog_quality_finalization_router)
app.include_router(catalog_auto_pipeline_router)
app.include_router(marketplace_admin)
app.include_router(marketplace_seller)
app.include_router(marketplace_public)
app.include_router(marketplace_cart)
app.include_router(marketplace_demo)
app.include_router(marketplace_checkout_v017)
app.include_router(marketplace_request_admin_v017)
app.include_router(sales_admin)
app.include_router(sales_buyer)
app.include_router(operator_quote_admin)
app.include_router(operator_quote_buyer)
app.include_router(commerce_integrity_router)
app.include_router(real_supplier_purchase_router)
app.include_router(supplier_probe_router)
app.include_router(seller_cabinet_router)
app.include_router(admin_spa_router)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    return {"status": "ok"}
