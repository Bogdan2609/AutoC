"""No network, supplier orders or payment API in these tests."""
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from app.core.db import get_session
from app.main import app
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import normalize_article
from app.modules.marketplace.models import (Merchant, SupplierAccount, SupplierDestination,
                                             FulfillmentNode)
from app.modules.pricing.models import PricePolicy
from app.modules.suppliers.models import Supplier
from app.modules.supplier_feeds.models import FeedBatch, FeedOffer
from app.modules.supplier_feeds.parsers import (FeedFormatError,parse_stock,read_feed)
from app.modules.supplier_feeds.service import import_batch
from sqlalchemy import func,select
from decimal import Decimal


def small_csv(*rows):
    prefix='Артикул;Бренд;Описание;Цена;Количество;Мин.Партия;Склад\n'
    return (prefix+'\n'.join(rows)+'\n').encode('cp1251')


def test_stock_bounds_and_bad_stock():
    assert parse_stock('>5')==('>5',6,True)
    assert parse_stock('10-100')==('10-100',10,True)
    assert parse_stock('2')==('2',2,False)
    for bad in ('-1','неизвестно','>','100-10','nan'):
        with pytest.raises(ValueError):parse_stock(bad)


def test_realistic_partkom_file(tmp_path):
    path=tmp_path/'a.csv'
    path.write_bytes(small_csv('0986424707;BOSCH;Колодки;2870.00;>5;2;7484'))
    records=list(read_feed('partkom',path))
    assert len(records)==1
    line,row,err=records[0]
    assert not err and row.stock_min==6 and row.stock_is_lower_bound
    assert row.minimum==2 and row.multiple==2 and row.warehouse=='7484'


def test_rossko_zip_xlsx(tmp_path):
    import html
    strings=['Номенклатура','Бренд','Артикул','Описание','Вес/Объем',
        'Кратность отгрузки','Цена, руб.','Базовая цена, руб.','Наличие',
        'Срок поставки, дн.','Каталожный номер','OEМ Номер','Применимость',
        'Вендор-код','ТНВЭД','ОКПД 2','Подключен к ЧЗ','Склад',
        'NSII001','TRIALLI','CF705','Фильтр','10-100','HST123']
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ss=f'<sst xmlns="{ns}">'+''.join(f'<si><t>{html.escape(x)}</t></si>' for x in strings)+'</sst>'
    a='ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    header=''.join(f'<c r="{a[i]}1" t="s"><v>{i}</v></c>' for i in range(18))
    record={0:18,1:19,2:20,3:21,5:'1',6:'492.2700000000000',8:22,9:'0',17:23}
    values=''.join((f'<c r="{a[col]}2" t="s"><v>{val}</v></c>' if isinstance(val,int) and col in (0,1,2,3,8,17)
                    else f'<c r="{a[col]}2"><v>{val}</v></c>') for col,val in record.items())
    sheet=f'<worksheet xmlns="{ns}"><sheetData><row r="1">{header}</row><row r="2">{values}</row></sheetData></worksheet>'
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('xl/sharedStrings.xml',ss)
        z.writestr('xl/worksheets/sheet1.xml',sheet)
    path=tmp_path/'samara.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('price.xlsx',raw.getvalue())
    rec=list(read_feed('rossko',path))
    assert rec[0][2] is None
    assert rec[0][1].stock_min==10
    assert rec[0][1].purchase_price==Decimal('492.27')


@pytest.mark.asyncio
async def test_upload_process_publish_and_duplicate(client,admin_headers,tmp_path,monkeypatch):
    from app.modules.supplier_feeds import service,api
    monkeypatch.setattr(service,'STORAGE',tmp_path)
    monkeypatch.setattr(api,'STORAGE',tmp_path)
    data=small_csv('0986424707;BOSCH;Колодки;2870;>5;1;7484',
                   'HU71210X;MANN;Фильтр;1148.12;2;1;7484')
    r=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',headers=admin_headers,
        files={'file':('sample.csv',data,'text/csv')})
    assert r.status_code==201,r.text
    batch_id=UUID(r.json()['id'])
    second=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',headers=admin_headers,
        files={'file':('sample.csv',data,'text/csv')})
    assert second.json()['already_uploaded'] is True
    resp=await client.post(f'/api/v1/admin/supplier-feeds/imports/{batch_id}/process',headers=admin_headers)
    assert resp.status_code==202,resp.text
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    try:
        status=await import_batch(db,batch_id)
        assert status=='staged'
        batch=await db.get(FeedBatch,batch_id)
        assert batch.rows_valid==2 and batch.rows_rejected==0
        assert await db.scalar(select(func.count()).select_from(FeedOffer))==2
    finally:await gen.aclose()
    r=await client.post(f'/api/v1/admin/supplier-feeds/imports/{batch_id}/publish',
        headers=admin_headers,json={'generated_at':datetime.now(timezone.utc).isoformat()})
    assert r.status_code==200,r.text
    assert r.json()['status']=='published'
    r=await client.post(f'/api/v1/admin/supplier-feeds/imports/{batch_id}/publish',
        headers=admin_headers,json={'generated_at':datetime.now(timezone.utc).isoformat()})
    assert r.status_code==409


@pytest.mark.asyncio
async def test_no_auto_publish_when_bad_file(client,admin_headers,tmp_path,monkeypatch):
    from app.modules.supplier_feeds import service,api
    monkeypatch.setattr(service,'STORAGE',tmp_path)
    monkeypatch.setattr(api,'STORAGE',tmp_path)
    bad=small_csv('X;BRAND;item;0;1;1;12', 'Y;BRAND;item;inf;2;1;12')
    r=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',
        headers=admin_headers,files={'file':('bad.csv',bad)})
    assert r.status_code==201,r.text
    bid=UUID(r.json()['id'])
    await client.post(f'/api/v1/admin/supplier-feeds/imports/{bid}/process',headers=admin_headers)
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    try:
        assert await import_batch(db,bid)=='rejected'
    finally:await gen.aclose()
    r=await client.post(f'/api/v1/admin/supplier-feeds/imports/{bid}/publish',
        headers=admin_headers,json={'generated_at':datetime.now(timezone.utc).isoformat()})
    assert r.status_code==409


@pytest.mark.asyncio
async def test_feed_requires_verified_warehouse_and_published_product(client,admin_headers,tmp_path,monkeypatch):
    from app.modules.supplier_feeds import service,api
    monkeypatch.setattr(service,'STORAGE',tmp_path)
    monkeypatch.setattr(api,'STORAGE',tmp_path)
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    try:
        brand=Brand(name='BOSCH',name_norm='BOSCH',slug='bosch');db.add(brand)
        await db.flush()
        product=Product(brand_id=brand.id,article='0986424707',article_norm=normalize_article('0986424707'),
                        name='Колодки',slug='bosch-0986424707',status='published')
        merchant=Merchant(name='Shop',slug='shop',status='active')
        supplier=Supplier(code='partkom',name='Part Kom')
        db.add_all([product,merchant,supplier])
        await db.flush()
        node=FulfillmentNode(merchant_id=merchant.id,code='our-a',name='Наш склад',kind='warehouse',
                             city='Самара',address='test',active=True)
        account=SupplierAccount(supplier_id=supplier.id,merchant_id=merchant.id,label='test',
                                credential_profile='test',enabled=True,read_enabled=True)
        db.add_all([node,account,PricePolicy(id=1,name='Default',markup_percent=Decimal('20'),active=True)])
        await db.flush()
        destination=SupplierDestination(account_id=account.id,node_id=node.id,enabled=True,verified=True)
        db.add(destination)
        await db.commit()
        prodid=product.id;accountid=account.id;destid=destination.id
    finally:await gen.aclose()
    data=small_csv('0986424707;BOSCH;Колодки;2870;>5;1;7484')
    r=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',headers=admin_headers,
        files={'file':('file.csv',data)})
    bid=UUID(r.json()['id'])
    await client.post(f'/api/v1/admin/supplier-feeds/imports/{bid}/process',headers=admin_headers)
    gen=app.dependency_overrides[get_session]()
    db=await anext(gen)
    try:
        assert await import_batch(db,bid)=='staged'
        record=await db.scalar(select(FeedOffer).where(FeedOffer.batch_id==bid))
        assert record.product_id==prodid
    finally:await gen.aclose()
    url=f'/api/v1/marketplace/products/{prodid}/offers'
    before=await client.get(url)
    assert before.status_code==200 and before.json()['offers']==[]
    await client.post(f'/api/v1/admin/supplier-feeds/imports/{bid}/publish',headers=admin_headers,
        json={'generated_at':datetime.now(timezone.utc).isoformat()})
    no_map=await client.get(url)
    assert no_map.json()['offers']==[]
    r=await client.post('/api/v1/admin/supplier-feeds/warehouses/map',headers=admin_headers,
        json={'supplier':'partkom','warehouse_code':'7484','account_id':str(accountid),
              'destination_id':str(destid),'approved':True})
    assert r.status_code==201,r.text
    # Price meaning and VAT for the FILE must be approved independently of the API.
    no_commercial=await client.get(url)
    assert no_commercial.json()['offers']==[]
    r=await client.post('/api/v1/admin/supplier-feeds/sources/partkom/commercial-scope',
        headers=admin_headers,json={'price_kind':'purchase','currency':'RUB',
                                    'tax_status':'unknown','verified':True})
    assert r.status_code==200,r.text
    after=await client.get(url)
    assert after.status_code==200 and after.json()['offers']==[], 'Unconfirmed VAT and costs must suppress indicative price'
    verified=await client.post('/api/v1/admin/supplier-feeds/sources/partkom/commercial-scope',
        headers=admin_headers,json={'price_kind':'purchase','currency':'RUB',
                                    'tax_status':'included','verified':True})
    assert verified.status_code==200
    policy=await client.put('/api/v1/admin/supplier-feeds/pricing/policies',headers=admin_headers,
        json={'supplier_code':'partkom','account_id':str(accountid),'destination_id':str(destid),
              'warehouse_code':'7484','markup_percent':'20','supplier_delivery':'0',
              'packaging':'0','other_costs':'0','tax_reserve':'0',
              'sbp_percent':'0','sbp_fixed':'0','min_contribution':'0',
              'evidence_reference':'Contract price 2026-10-21'})
    assert policy.status_code==200,policy.text
    assert (await client.get(url)).json()['offers']==[]
    approved=await client.post('/api/v1/admin/supplier-feeds/pricing/policies/'+policy.json()['id']+'/approve',
         headers=admin_headers,json={'expected_version':1,'confirm_contract_costs':True})
    assert approved.status_code==200,approved.text
    after=await client.get(url)
    assert after.status_code==200,after.text
    offers=after.json()['offers']
    assert len(offers)==1 and offers[0]['source_kind']=='feed_indicative'
    assert offers[0]['price']=='3444.00' and offers[0]['snapshot_id'] is None
    assert offers[0]['fulfillment'][0]['stock_display']=='>5'
    assert offers[0]['cart_enabled'] is False and offers[0]['payment_enabled'] is False
    page=await client.get('/product/bosch-0986424707')
    assert page.status_code==200,page.text[:300]
    assert 'По прайсу — требуется подтверждение API. Заказ недоступен.' in page.text


@pytest.mark.asyncio
async def test_version_diff_and_safe_rollback(client,admin_headers,tmp_path,monkeypatch):
    from app.modules.supplier_feeds import service,api
    monkeypatch.setattr(service,'STORAGE',tmp_path)
    monkeypatch.setattr(api,'STORAGE',tmp_path)
    async def add_version(name,price):
        csvbytes=small_csv(f'X1;BRAND;Item;{price};2;1;11',
                           'X2;BRAND;Other;20;>5;1;11')
        r=await client.post('/api/v1/admin/supplier-feeds/partkom/uploads',headers=admin_headers,
           files={'file':(name,csvbytes)})
        assert r.status_code==201,r.text
        b=UUID(r.json()['id'])
        await client.post(f'/api/v1/admin/supplier-feeds/imports/{b}/process',headers=admin_headers)
        gen=app.dependency_overrides[get_session]()
        db=await anext(gen)
        try:assert await import_batch(db,b)=='staged'
        finally:await gen.aclose()
        return b
    old=await add_version('old.csv','100')
    r=await client.post(f'/api/v1/admin/supplier-feeds/imports/{old}/publish',headers=admin_headers,
        json={'generated_at':datetime.now(timezone.utc).isoformat()})
    assert r.status_code==200,r.text
    new=await add_version('new.csv','110')
    diff=await client.get(f'/api/v1/admin/supplier-feeds/imports/{new}/diff',headers=admin_headers)
    assert diff.status_code==200,diff.text
    assert diff.json()['price_changed']==1 and diff.json()['stock_changed']==0
    assert diff.json()['added']==0 and diff.json()['removed']==0
    r=await client.post(f'/api/v1/admin/supplier-feeds/imports/{new}/publish',headers=admin_headers,
        json={'generated_at':datetime.now(timezone.utc).isoformat()})
    assert r.status_code==200,r.text
    active=await client.get('/api/v1/admin/supplier-feeds/sources',headers=admin_headers)
    assert active.json()['items'][0]['active_batch_id']==str(new)
    r=await client.post('/api/v1/admin/supplier-feeds/sources/partkom/rollback',headers=admin_headers)
    assert r.status_code==200,r.text
    assert r.json()['active_batch_id']==str(old)


def test_reject_unsafe_archive(tmp_path):
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('../evil.xlsx',b'hi')
    with pytest.raises(FeedFormatError):list(read_feed('rossko',path))


@pytest.mark.asyncio
async def test_postgres_copy_path_uses_temporary_staging():
    from app.modules.supplier_feeds.service import _copy_postgres,COPY_FIELDS
    class AsyncContext:
        def __init__(self, value):self.value=value
        async def __aenter__(self):return self.value
        async def __aexit__(self,*args):return False
    class Copy:
        def __init__(self):self.rows=[]
        async def write_row(self,values):self.rows.append(values)
    class Cursor:
        def __init__(self):self.job=Copy();self.query=None
        def copy(self,sql):
            self.query=sql
            return AsyncContext(self.job)
    class Driver:
        def __init__(self):self.cur=Cursor()
        def cursor(self):return AsyncContext(self.cur)
    class DB:
        def __init__(self):self.commands=[];self.driver=Driver()
        async def execute(self,sql):self.commands.append(str(sql))
        async def connection(self):return self
        async def get_raw_connection(self):return type('Proxy',(),{'driver_connection':self.driver})()
    db=DB()
    row={name:'v' for name in COPY_FIELDS}
    await _copy_postgres(db,[row,row])
    assert len(db.driver.cur.job.rows)==2
    assert 'COPY feed_chunk_tmp' in db.driver.cur.query
    assert 'ON CONFLICT (batch_id, identity_hash) DO NOTHING' in db.commands[1]
    assert 'TRUNCATE TABLE feed_chunk_tmp' in db.commands[2]
