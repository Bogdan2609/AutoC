"""v0.27 regression coverage. All I/O is local; no supplier or payment HTTP calls."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import func, select
from app.main import app
from app.core.db import get_session
from app.modules.catalog.models import Product, Brand
from app.modules.catalog_bulk.models import CatalogBulkRun, CatalogCandidate, CatalogCandidateBatch
from app.modules.catalog_bulk.service import scan_batch, approve_safe,materialize_approved,relink_batch,execute_run
from app.modules.supplier_feeds.models import FeedBatch,FeedOffer,FeedSource


async def session_from_fixture():
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    return gen,db


async def seed_feed(db, supplier, offers):
    if not await db.get(FeedSource,supplier):
        db.add(FeedSource(code=supplier,commercial_verified=False,price_kind='unknown',
                          currency='RUB',tax_status='unknown',max_age_hours=30))
    batch=FeedBatch(id=uuid.uuid4(),supplier_code=supplier,status='staged',
                    original_name='dummy.csv',stored_name=f'{uuid.uuid4().hex}.csv',
                    sha256=uuid.uuid4().hex*2,rows_total=len(offers),rows_valid=len(offers),
                    rows_rejected=0,rows_matched=0,rows_duplicates=0,errors_sample='')
    db.add(batch)
    await db.flush()
    for i,(brand,article,description,sku) in enumerate(offers,1):
        db.add(FeedOffer(id=uuid.uuid4(),batch_id=batch.id,identity_hash=uuid.uuid4().hex,
            source_line=i,supplier_sku=sku,brand=brand,brand_norm=brand.upper(),
            article=article,article_norm=article.upper(),description=description,
            warehouse_code=f'W-{i}',purchase_price=Decimal('12.50'),stock_raw='2',
            stock_min=2,is_lower_bound=False,minimum=1,multiple=1))
    await db.commit()
    return batch.id


@pytest.mark.asyncio
async def test_scan_is_idempotent_and_cross_supplier_drafts_are_shared(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        rossko=await seed_feed(db,'rossko',[
            ('ZETA','Z101','Шарнир','sku101'),
            ('ZETA','Z101','Шарнир','sku101'),
            ('BETA','B101','Фильтр','sku102')])
        partkom=await seed_feed(db,'partkom',[
            ('ZETA','Z101','Шарнир',''),
            ('GAMMA','G1','Насос','')])
        first=await scan_batch(db,rossko)
        again=await scan_batch(db,rossko)
        assert first[0]==again[0]==2
        assert await db.scalar(select(func.count()).select_from(CatalogCandidate))==2
        assert (await scan_batch(db,partkom))[0]==2
        assert await db.scalar(select(func.count()).select_from(CatalogCandidate))==3
        assert await db.scalar(select(func.count()).select_from(CatalogCandidateBatch))==4
        before=await db.scalar(select(func.count()).select_from(Product))
        assert before==0
        approve=await approve_safe(db,rossko,limit=100)
        assert approve['approved']==2
        result=await materialize_approved(db,limit=100,batch_id=rossko)
        assert result['created_drafts']==2 and result['published']==0
        products=(await db.scalars(select(Product))).all()
        assert len(products)==2 and all(p.status=='draft' and p.seo_noindex for p in products)
        records=(await db.scalars(select(FeedOffer).where(FeedOffer.batch_id==rossko))).all()
        assert all(rec.product_id for rec in records)
        # Candidate shared across vendors, but later file isn't linked until explicit relink.
        assert await relink_batch(db,partkom)==1
        zeta_rows=(await db.scalars(select(FeedOffer).where(FeedOffer.article_norm=='Z101'))).all()
        assert len({row.product_id for row in zeta_rows})==1
        assert await db.scalar(select(func.count()).select_from(Product))==2
        # New supplier item has not automatically been turned into a product.
        gamma=await db.scalar(select(CatalogCandidate).where(CatalogCandidate.article_norm=='G1'))
        assert gamma.status=='new'
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_strict_review_on_ambiguous_identity_and_mojibake(client):
    gen,db=await session_from_fixture()
    try:
        batch=await seed_feed(db,'rossko',[
            ('ZETA','X101','Разные комплекты','sku1'),
            ('ZETA','X101','Разные исполнения','sku2'),
            ('ÐÐÇ','TEST','Разбитая кодировка','sku3')])
        assert (await scan_batch(db,batch))[0]==2
        output=await approve_safe(db,batch,limit=10)
        assert output['approved']==0 and output['review']==2
        created=await materialize_approved(db,batch_id=batch,limit=10)
        assert created['created_drafts']==0
        assert await db.scalar(select(func.count()).select_from(Product))==0
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_review_and_run_api_does_not_publish(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        batch=await seed_feed(db,'partkom', [('TESTBRAND','TB101','Brake parts','')])
        url='/api/v1/admin/catalog-bulk'
        r=await client.post(url+'/scan',headers=admin_headers,json={'batch_id':str(batch)})
        assert r.status_code==202,r.text
        run_id=uuid.UUID(r.json()['id'])
        await execute_run(db,run_id)
        detail=await client.get(url+f'/runs/{run_id}',headers=admin_headers)
        assert detail.json()['status']=='completed' and detail.json()['affected']==1
        r=await client.get(url+'/candidates',headers=admin_headers)
        candidate_id=r.json()['items'][0]['id']
        r=await client.post(url+f'/candidates/{candidate_id}/decision',headers=admin_headers,
            json={'decision':'approved','note':'Проверено вручную'})
        assert r.status_code==200 and r.json()['status']=='approved'
        r=await client.post(url+'/materialize',headers=admin_headers,
            json={'batch_id':str(batch),'limit':10})
        assert r.status_code==202
        await execute_run(db,uuid.UUID(r.json()['id']))
        product=await db.scalar(select(Product))
        assert product is not None and product.status=='draft' and product.seo_noindex is True
        assert (await db.get(CatalogCandidate,uuid.UUID(candidate_id))).product_id==product.id
        assert (await client.get(url+'/statistics',headers=admin_headers)).status_code==200
        assert (await client.get(url+'/runs',headers=admin_headers)).status_code==200
        assert (await client.get(url+'/candidates',headers={ })).status_code in (401,403)
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_scan_refuses_unprocessed_batch(client):
    gen,db=await session_from_fixture()
    try:
        bid=await seed_feed(db,'partkom',[('A','X','Title','')])
        batch=await db.get(FeedBatch,bid)
        batch.status='processing'
        await db.commit()
        with pytest.raises(ValueError,match='Only staged/published'):
            await scan_batch(db,bid)
    finally:await gen.aclose()

@pytest.mark.asyncio
async def test_manual_audit_and_resumable_safe_approval_job(client,admin_headers):
    gen,db=await session_from_fixture()
    try:
        bid=await seed_feed(db,'partkom',[
            ('SAMPLE','AA12','Safe item',''),('ANOTHER','BB15','Another item','')])
        api='/api/v1/admin/catalog-bulk'
        r=await client.post(api+'/scan',headers=admin_headers,json={'batch_id':str(bid)})
        await execute_run(db,uuid.UUID(r.json()['id']))
        r=await client.post(api+'/approve-safe/queue',headers=admin_headers,
            json={'batch_id':str(bid),'limit':1000})
        rid=uuid.UUID(r.json()['id'])
        await execute_run(db,rid)
        run=await db.get(CatalogBulkRun,rid)
        assert run.status=='completed' and run.linked==2
        # Restarting a completed run cannot approve more records.
        await execute_run(db,rid)
        assert (await db.get(CatalogBulkRun,rid)).linked==2
        first=await db.scalar(select(CatalogCandidate).where(CatalogCandidate.brand_norm=='SAMPLE'))
        r=await client.post(api+f'/candidates/{first.id}/decision',headers=admin_headers,
            json={'decision':'review','note':'Needs inspection'})
        assert r.status_code==200 and r.json()['status']=='review'
        hist=await client.get(api+f'/candidates/{first.id}/decisions',headers=admin_headers)
        assert hist.json()['items'][0]['old_status']=='approved'
        assert hist.json()['items'][0]['new_status']=='review'
        assert hist.json()['items'][0]['note']=='Needs inspection'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_existing_published_product_is_reused_without_overwriting_feed_link(client):
    gen,db=await session_from_fixture()
    try:
        brand=Brand(name='KNOWN',name_norm='KNOWN',slug='known')
        db.add(brand);await db.flush()
        product=Product(brand_id=brand.id,article='K55',article_norm='K55',
                        name='Existing verified',slug='known-k55',status='published')
        db.add(product);await db.commit()
        bid=await seed_feed(db,'partkom',[('KNOWN','K55','Correct part','')])
        await scan_batch(db,bid)
        assert (await approve_safe(db,bid,limit=10))['approved']==1
        result=await materialize_approved(db,batch_id=bid)
        assert result['created_drafts']==0 and result['linked_existing']==1
        assert await db.scalar(select(func.count()).select_from(Product))==1
        assert (await db.scalar(select(FeedOffer).where(FeedOffer.batch_id==bid))).product_id==product.id
        assert (await db.get(Product,product.id)).status=='published'
    finally:await gen.aclose()


@pytest.mark.asyncio
async def test_postgres_scan_generates_set_based_conflict_safe_sql(monkeypatch):
    from sqlalchemy.dialects import postgresql
    import app.modules.catalog_bulk.service as svc
    bid=uuid.uuid4()
    class PseudoDB:
        statements=[]
        async def get(self,model,key):
            return FeedBatch(id=bid,supplier_code='rossko',status='staged',
                original_name='file.zip',stored_name='foo.zip',sha256='a'*64,
                rows_total=5,rows_valid=5,rows_rejected=0,rows_matched=0,
                rows_duplicates=0,errors_sample='')
        async def execute(self,statement):
            self.statements.append(str(statement.compile(dialect=postgresql.dialect())))
        async def scalar(self,statement):return 0
        async def commit(self):pass
    monkeypatch.setattr(svc,'_dialect',lambda db:'postgresql')
    db=PseudoDB()
    await svc.scan_batch(db,bid)
    assert len(db.statements)==2
    assert 'gen_random_uuid()' in db.statements[0].lower()
    assert 'ON CONFLICT (brand_norm, article_norm) DO NOTHING' in db.statements[0]
    assert 'ON CONFLICT (candidate_id, batch_id) DO NOTHING' in db.statements[1]

@pytest.mark.asyncio
async def test_conflicting_descriptions_across_suppliers_require_review(client):
    gen,db=await session_from_fixture()
    try:
        first=await seed_feed(db,'rossko',[('ORION','O888','Фильтр воздушный','R1')])
        second=await seed_feed(db,'partkom',[('ORION','O888','Набор крепежа','')])
        await scan_batch(db,first)
        await scan_batch(db,second)
        result=await approve_safe(db,first,limit=10)
        assert result['approved']==0 and result['review']==1
        c=await db.scalar(select(CatalogCandidate).where(CatalogCandidate.article_norm=='O888'))
        assert c.status=='review'
    finally:await gen.aclose()
