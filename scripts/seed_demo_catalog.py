"""Explicit opt-in, labelled DEMO records for development-only UI testing.

Never reads provider APIs, never creates real supplier records, never makes orders.
Usage: docker compose exec -T web python -m scripts.seed_demo_catalog
"""
import asyncio
from decimal import Decimal
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session_factory
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import normalize_article, normalize_brand, make_slug
from app.modules.pricing.models import PricePolicy
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot
from app.modules.offer_engine.models import OfferDetail


async def main():
    settings = get_settings()
    if settings.app_env != 'development' or settings.supplier_api_enabled:
        raise SystemExit('DEMO seeding only permitted with APP_ENV=development and SUPPLIER_API_ENABLED=false')
    async with get_session_factory()() as db:
        brand_name, article = 'DEMO-PARTS', 'T123456'
        brand = await db.scalar(select(Brand).where(Brand.name_norm == normalize_brand(brand_name)))
        if brand is None:
            brand = Brand(name=brand_name,name_norm=normalize_brand(brand_name),slug='demo-parts')
            db.add(brand);await db.flush()
        product = await db.scalar(select(Product).where(Product.brand_id == brand.id,Product.article_norm == normalize_article(article)))
        if product is None:
            product = Product(brand_id=brand.id,article=article,article_norm=normalize_article(article),
                              slug=make_slug(brand.slug,normalize_article(article)),
                              name='DEMO — тестовая деталь (не настоящий товар)',status='published',
                              description='Тестовая запись: цены и наличие вымышленные. Не заказывать.')
            db.add(product);await db.flush()
        supplier = await db.scalar(select(Supplier).where(Supplier.code=='demo-only'))
        if supplier is None:
            supplier=Supplier(code='demo-only',name='DEMO: имитация, не поставщик')
            db.add(supplier);await db.flush()
        item=await db.scalar(select(SupplierItem).where(SupplierItem.supplier_id==supplier.id,SupplierItem.external_key=='demo-t123456'))
        if item is None:
            item=SupplierItem(supplier_id=supplier.id,product_id=product.id,external_key='demo-t123456',
                              raw_brand=brand_name,raw_article=article,match_status='confirmed')
            db.add(item);await db.flush()
        snap=await db.scalar(select(OfferSnapshot).where(OfferSnapshot.supplier_item_id==item.id))
        if snap is None:
            snap=OfferSnapshot(supplier_item_id=item.id,purchase_price=Decimal('1500.00'),
                               currency='RUB',quantity=5,lead_time_days=2,tax_status='unknown')
            db.add(snap);await db.flush()
            db.add(OfferDetail(snapshot_id=snap.id,external_token='DEMO-NOT-FOR-ORDER',
                               warehouse='DEMO',verified_live=False))
        if await db.scalar(select(PricePolicy).where(PricePolicy.active.is_(True))) is None:
            db.add(PricePolicy(name='demo-default',markup_percent=Decimal('25.00'),active=True))
        await db.commit()
        print('DEMO catalog entry:',f'/product/{product.slug}','(not a live supplier product)')


if __name__ == '__main__':
    asyncio.run(main())
