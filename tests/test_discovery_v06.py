"""No live supplier network used in tests. Test exact discovery and abuse limits."""
from decimal import Decimal
from sqlalchemy import select, func
import pytest
from fastapi import HTTPException

from app.core.db import get_session
from app.core.config import get_settings
from app.main import app
from app.modules.catalog.models import Brand,Product
from app.modules.offer_engine.models import CacheState
from app.modules.commerce.models import Job
from app.modules.offer_engine.providers import Offer
from app.modules.offer_engine.discovery import precise_query, validate_pair
import app.modules.offer_engine.discovery as discovery
import app.workers.runner as runner


async def db_session():
    async for db in app.dependency_overrides[get_session]():
        yield db


@pytest.fixture
def enabled_supplier(monkeypatch):
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    get_settings.cache_clear()
    monkeypatch.setattr(discovery,'credentials_ready',lambda code: True)


@pytest.mark.asyncio
async def test_free_text_never_floods_apis(client, enabled_supplier):
    for query in ['тормозной диск BOSCH', 'BOSCH', 'BOSCH фильтр']:
        r = await client.get('/catalog',params={'q':query})
        assert r.status_code == 200
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(Job))) == 0


@pytest.mark.asyncio
async def test_exact_search_enqueues_and_deduplicates(client, enabled_supplier):
    resp = await client.get('/catalog?q=BOSCH+0986479088')
    assert resp.status_code == 200
    assert 'Проверка предложений' in resp.text
    status = await client.get('/api/v1/discovery/status?brand=BOSCH&article=0986479088')
    assert status.status_code == 200
    assert status.json()['pending'] is True
    assert status.json()['product_url'] is None
    await client.get('/catalog?q=BOSCH+0986479088')
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(Job))) == 4
        assert (await db.scalar(select(func.count()).select_from(Product))) == 0
        assert (await db.scalar(select(func.count()).select_from(CacheState))) == 4


@pytest.mark.asyncio
async def test_real_response_imports_only_exact_verified_part(client, enabled_supplier, monkeypatch):
    await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    class MockSupplier:
        async def search(self, brand, article):
            return [Offer('partkom', 'mk:0986479088:provider:0', 'BOSCH', '0986479088',
                          Decimal('1200'), 3, 2, 'provider-id', 'WH01')]
    monkeypatch.setitem(runner.PROVIDERS,'partkom',MockSupplier())
    async for db in db_session():
        job = await db.scalar(select(Job).where(Job.kind=='search'))
        jobs = (await db.scalars(select(Job))).all()
        job = next(j for j in jobs if j.payload['supplier']=='partkom')
        await runner.search(db, job)
        await db.commit()
        product = await db.scalar(select(Product))
        assert product and product.status=='draft' and product.seo_noindex
        assert product.article_norm=='0986479088'
        state = await db.scalar(select(CacheState).where(CacheState.supplier_code=='partkom'))
        assert state.status=='ready' and state.product_id==product.id
        product_id = str(product.id)
    status = await client.get('/api/v1/discovery/status?brand=BOSCH&article=0986479088')
    assert status.json()['product_url'] is None  # Only manual publication can expose a Product URL.
    offers = await client.get('/api/v1/products/'+product_id+'/offers')
    assert offers.status_code in (403,404)  # Private draft cannot be sold publicly.


@pytest.mark.asyncio
async def test_mismatched_supplier_response_never_creates_product(client, enabled_supplier, monkeypatch):
    await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    class Wrong:
        async def search(self, brand, article):
            return [Offer('partkom','wrong:code','OTHER','0986479088',Decimal('3'),1,1,'t')]
    monkeypatch.setitem(runner.PROVIDERS,'partkom',Wrong())
    async for db in db_session():
        jobs = (await db.scalars(select(Job))).all()
        job = next(j for j in jobs if j.payload['supplier']=='partkom')
        await runner.search(db,job)
        await db.commit()
        assert (await db.scalar(select(func.count()).select_from(Product)))==0
        state=await db.scalar(select(CacheState).where(CacheState.supplier_code=='partkom'))
        assert state.status=='review'


@pytest.mark.asyncio
async def test_negative_cache_prevents_repeat_search(client, enabled_supplier, monkeypatch):
    await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    class Empty:
        async def search(self,brand,article):return []
    monkeypatch.setitem(runner.PROVIDERS,'partkom',Empty())
    async for db in db_session():
        jobs=(await db.scalars(select(Job))).all()
        await runner.search(db,next(j for j in jobs if j.payload['supplier']=='partkom'))
        await db.commit()
        state=await db.scalar(select(CacheState).where(CacheState.supplier_code=='partkom'))
        assert state.status=='empty'
    await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(Job)))==4


@pytest.mark.asyncio
async def test_budget_exhaustion_prevents_jobs(client,enabled_supplier,monkeypatch):
    async def deny(_):
        raise HTTPException(429,'Слишком много новых поисков')
    monkeypatch.setattr(discovery,'claim_public_budget',deny)
    page=await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    assert page.status_code==200
    assert 'Слишком много новых поисков' in page.text
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(Job))) == 0


def test_exact_input_parser_rejects_natural_language():
    assert precise_query('BOSCH 0 986 479 088')==('BOSCH','0 986 479 088')
    assert precise_query('тормозной диск') is None
    assert precise_query('BOSCH масляный фильтр') is None
    with pytest.raises(HTTPException):
        validate_pair('BOSCH','%___')


@pytest.mark.asyncio
async def test_queue_backpressure_halts_new_discovery(client,enabled_supplier,monkeypatch):
    monkeypatch.setenv('DISCOVERY_MAX_PENDING_JOBS','4')
    get_settings.cache_clear()
    first=await client.get('/catalog?brand_name=BOSCH&article=0986479088')
    assert 'Проверка предложений' in first.text
    second=await client.get('/catalog?brand_name=BOSCH&article=0986479089')
    assert 'Очередь поиска перегружена' in second.text
    async for db in db_session():
        assert (await db.scalar(select(func.count()).select_from(Job)))==4
