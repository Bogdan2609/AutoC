"""Local-only regression for v0.31.4 feed refresh and safe DRAFT linking.

No supplier network, real purchasing or public product publication.
"""
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Brand, Product
from app.modules.catalog_bulk.models import CatalogCandidate
from app.modules.supplier_feeds.models import FeedOffer, FeedBatch
from app.modules.supplier_feeds.service import import_batch
from tests.test_supplier_feeds import small_csv


@pytest.mark.asyncio
async def test_new_price_feed_links_existing_canonical_draft(client, admin_headers, tmp_path, monkeypatch):
    from app.modules.supplier_feeds import api, service
    monkeypatch.setattr(api, 'STORAGE', tmp_path)
    monkeypatch.setattr(service, 'STORAGE', tmp_path)

    session_gen = app.dependency_overrides[get_session]()
    db = await anext(session_gen)
    try:
        brand = Brand(name='BOSCH', name_norm='BOSCH', slug='bosch')
        db.add(brand)
        await db.flush()
        product = Product(brand_id=brand.id, article='0986424707', article_norm='0986424707',
                          name='Тормозные колодки BOSCH 0986424707',
                          slug='bosch-0986424707', status='draft', seo_noindex=True)
        db.add(product)
        await db.flush()
        candidate = CatalogCandidate(brand_norm='BOSCH', article_norm='0986424707',
                                     sample_brand='BOSCH', sample_article='0986424707',
                                     sample_description='Колодки', status='materialized',
                                     product_id=product.id)
        db.add(candidate)
        await db.commit()
        product_id = product.id
    finally:
        await session_gen.aclose()

    payload = small_csv('0986424707;BOSCH;Колодки;2870.22;>5;1;7484')
    response = await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',
                                 headers=admin_headers, files={'file': ('updated.csv', payload)})
    assert response.status_code == 201, response.text
    batch_id = UUID(response.json()['id'])
    queued = await client.post(f'/api/v1/admin/supplier-feeds/imports/{batch_id}/process',
                               headers=admin_headers)
    assert queued.status_code == 202, queued.text

    session_gen = app.dependency_overrides[get_session]()
    db = await anext(session_gen)
    try:
        status = await import_batch(db, batch_id)
        assert status == 'staged'
        batch = await db.get(FeedBatch, batch_id)
        offer = await db.scalar(select(FeedOffer).where(FeedOffer.batch_id == batch_id))
        unchanged = await db.get(Product, product_id)
        assert offer.product_id == product_id
        assert str(offer.purchase_price) == '2870.22'
        assert batch.rows_matched == 1
        assert unchanged.status == 'draft' and unchanged.seo_noindex is True
        assert batch.status == 'staged' and batch.published_at is None
    finally:
        await session_gen.aclose()


@pytest.mark.asyncio
async def test_import_does_not_link_stale_or_wrong_product_identity(client, admin_headers, tmp_path, monkeypatch):
    from app.modules.supplier_feeds import api, service
    monkeypatch.setattr(api, 'STORAGE', tmp_path)
    monkeypatch.setattr(service, 'STORAGE', tmp_path)
    session_gen = app.dependency_overrides[get_session]()
    db = await anext(session_gen)
    try:
        brand = Brand(name='BOSCH', name_norm='BOSCH', slug='bosch')
        db.add(brand)
        await db.flush()
        product = Product(brand_id=brand.id, article='OTHER', article_norm='OTHER',
                          name='Другой товар', slug='bosch-other', status='draft', seo_noindex=True)
        db.add(product)
        await db.flush()
        db.add(CatalogCandidate(brand_norm='BOSCH', article_norm='0986424707',
                                sample_brand='BOSCH', sample_article='0986424707',
                                sample_description='Фильтр', status='materialized',
                                product_id=product.id))
        await db.commit()
    finally:
        await session_gen.aclose()
    payload = small_csv('0986424707;BOSCH;Фильтр;500.50;1;1;7484')
    r = await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',
                          headers=admin_headers, files={'file': ('new.csv', payload)})
    assert r.status_code == 201
    batch_id = UUID(r.json()['id'])
    await client.post(f'/api/v1/admin/supplier-feeds/imports/{batch_id}/process', headers=admin_headers)
    session_gen = app.dependency_overrides[get_session]()
    db = await anext(session_gen)
    try:
        assert await import_batch(db, batch_id) == 'staged'
        offer = await db.scalar(select(FeedOffer).where(FeedOffer.batch_id == batch_id))
        assert offer.product_id is None
    finally:
        await session_gen.aclose()
