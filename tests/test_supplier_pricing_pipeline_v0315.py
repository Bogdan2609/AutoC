"""Live integration test: no external supplier calls, no product publication."""
import uuid
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import select,func
import pytest
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Brand,Product
from app.modules.supplier_feeds.models import FeedBatch,FeedCatalogSync,FeedOffer
from app.modules.supplier_feeds.pricing import quote_unit,usable_rule
from app.modules.supplier_feeds.pipeline import run_sync
from app.modules.supplier_feeds.service import import_batch
from app.modules.catalog_ext.models import ProductCategory
from app.modules.supplier_feeds.models import FeedSource
from tests.test_supplier_feeds import small_csv


def rule(**changes):
    v=dict(id=uuid.uuid4(),version=3,approved=True,tax_status_verified='included',
        markup_percent=Decimal('25'),supplier_delivery=Decimal('30'),packaging=Decimal('10'),
        other_costs=Decimal('5'),tax_reserve=Decimal('0'),sbp_percent=Decimal('0.7'),
        sbp_fixed=Decimal('1'),min_contribution=Decimal('100'))
    v.update(changes)
    return SimpleNamespace(**v)


def test_pricing_covers_fee_on_final_customer_price():
    r=rule()
    d=quote_unit(Decimal('1000'),r)
    amount=Decimal(d['price']);fee=Decimal(d['sbp_fee'])
    assert Decimal(d['contribution']) >= Decimal('250')
    assert amount-Decimal('1000')-Decimal('30')-Decimal('10')-Decimal('5')-fee==Decimal(d['contribution'])
    assert d['cart_enabled'] is False and d['payment_enabled'] is False
    assert quote_unit(Decimal('1400'),r)['price']>d['price']
    with pytest.raises(ValueError):quote_unit(Decimal('1000'),rule(sbp_percent=Decimal('100')))
    with pytest.raises(ValueError):quote_unit(Decimal('-100'),r)
    source=SimpleNamespace(commercial_verified=True,price_kind='purchase',currency='RUB',tax_status='included')
    assert usable_rule(r,source)
    source.tax_status='unknown';assert not usable_rule(r,source)
    r.approved=False;source.tax_status='included';assert not usable_rule(r,source)


@pytest.mark.asyncio
async def test_import_automatically_creates_drafts_for_new_scoped_candidates(client,admin_headers,tmp_path,monkeypatch):
    from app.modules.supplier_feeds import service,api
    monkeypatch.setattr(service,'STORAGE',tmp_path)
    monkeypatch.setattr(api,'STORAGE',tmp_path)
    response=await client.post('/api/v1/admin/multi-enrichment/taxonomy/bootstrap',headers=admin_headers)
    assert response.status_code==200,response.text
    gen=app.dependency_overrides[get_session]();db=await anext(gen)
    try:
        db.add(Brand(name='BOSCH',name_norm='BOSCH',slug='bosch'))
        await db.commit()
    finally:await gen.aclose()
    contents=small_csv('192837465;BOSCH;Фильтр масляный;300;10;1;A1')
    upload=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',headers=admin_headers,files={'file':('new.csv',contents)})
    assert upload.status_code==201,upload.text
    batchid=uuid.UUID(upload.json()['id'])
    response=await client.post(f'/api/v1/admin/supplier-feeds/imports/{batchid}/process',headers=admin_headers)
    assert response.status_code==202
    gen=app.dependency_overrides[get_session]();db=await anext(gen)
    try:
        assert await import_batch(db,batchid)=='staged'
        sync=await db.scalar(select(FeedCatalogSync).where(FeedCatalogSync.batch_id==batchid))
        assert sync.status=='queued'
        await run_sync(db,sync.id,0)
        await db.refresh(sync)
        for _ in range(4):
            if sync.status=='completed':break
            await run_sync(db,sync.id,sync.step)
            await db.refresh(sync)
        assert sync.status=='completed',sync.message
        assert sync.created_drafts==1,(sync.created_drafts,sync.reviewed)
        p=(await db.scalars(select(Product))).all()
        assert len(p)==1 and p[0].status=='draft' and p[0].seo_noindex is True
        assert await db.scalar(select(func.count()).select_from(ProductCategory).where(ProductCategory.product_id==p[0].id))==1
        offer=await db.scalar(select(FeedOffer).where(FeedOffer.batch_id==batchid))
        assert offer.product_id==p[0].id and offer.purchase_price==Decimal('300')
        assert (await db.get(FeedBatch,batchid)).status=='staged'
        assert (await db.get(FeedSource,'partkom')).active_batch_id is None
        await run_sync(db,sync.id,0)   # duplicate stale message is a no-op
        assert await db.scalar(select(func.count()).select_from(Product))==1
    finally:await gen.aclose()
