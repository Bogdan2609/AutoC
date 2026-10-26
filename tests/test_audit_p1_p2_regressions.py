"""Risk-driven tests for the 2026-10-21 audit remediation.
All tests are offline; no real supplier order, charge, or refund is possible.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.modules.accounts.models import User
from app.modules.commerce.models import Cart,CartLine
from app.modules.marketplace import procurement as purchase
from app.core import rate_limit


@pytest.mark.asyncio
async def test_reject_excess_category_filters_before_db(client):
    response=await client.get('/category/category-does-not-exist',
        params=[('attr',f'x{i}:foo') for i in range(9)])
    assert response.status_code==422, response.text


@pytest.mark.asyncio
async def test_cart_read_does_not_write(client):
    body={'email':'read-only-cart@example.org','password':'ReadOnlyCartPassword123!'}
    assert (await client.post('/api/v1/accounts/register',json=body)).status_code==201
    token=(await client.post('/api/v1/accounts/login',json=body)).json()['access_token']
    h={'Authorization':'Bearer '+token}
    read=await client.get('/api/v1/cart',headers=h)
    assert read.status_code==200
    assert read.json()['id'] is None
    assert read.json()['items']==[]
    again=await client.get('/api/v1/cart',headers=h)
    assert again.json()['id'] is None


def test_cart_partial_unique_indexes_enforced_in_sqlite():
    engine=create_engine('sqlite+pysqlite:///:memory:',poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            uid=uuid.uuid4()
            db.add(User(id=uid,email='test-unique-cart@example.org',password_hash='unused'))
            db.flush()
            db.add(Cart(user_id=uid,status='open'))
            db.commit()
            db.add(Cart(user_id=uid,status='open'))
            with pytest.raises(IntegrityError):db.commit()
            db.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_predispatch_quota_failure_cannot_be_uncertain(monkeypatch):
    attempt=SimpleNamespace(id=uuid.uuid4(),client_ref='audit-test-ref',
        order_line_id=uuid.uuid4(),status='prepared',quantity=1,approved_unit_price=100,
        snapshot_id=uuid.uuid4(),supplier_destination_id=uuid.uuid4(),
        supplier_account_id=uuid.uuid4(),buyer_order_id=None,sent_at=None,error_code=None)
    seller_id=uuid.uuid4();node_id=uuid.uuid4();item_id=uuid.uuid4()
    account=SimpleNamespace(id=attempt.supplier_account_id,merchant_id=seller_id,requests_per_hour=60)
    dest=SimpleNamespace(node_id=node_id)
    item=SimpleNamespace(id=item_id)
    line=SimpleNamespace(merchant_id=seller_id,supplier_item_id=item_id,fulfillment_node_id=node_id)
    async def ctx(*args):return account,dest,SimpleNamespace(),item,SimpleNamespace()
    async def paid_ctx(*args):return SimpleNamespace(id=uuid.uuid4()),line,SimpleNamespace(),SimpleNamespace()
    monkeypatch.setattr(purchase,'paid_line_context',paid_ctx)
    from app.modules.marketplace.receiving_contact import ReceivingContact
    async def valid_site(*args):return ReceivingContact('Складов Иван','+79991234567')
    monkeypatch.setattr(purchase,'site_contact',valid_site)
    class DB:
        async def scalar(self,stmt):return attempt
        async def get(self,model,pk):return attempt
        async def commit(self):pass
        async def rollback(self):pass
    monkeypatch.setattr(purchase,'get_settings',lambda:SimpleNamespace(
        supplier_procurement_enabled=True,real_supplier_checkout_enabled=True))
    monkeypatch.setattr(purchase,'context',ctx)
    from app.modules.offer_engine import discovery
    async def deny(*args):return False
    monkeypatch.setattr(discovery,'claim_supplier_budget',deny)
    async def must_not_call(*args,**kwargs):raise AssertionError('Supplier checkout cannot run')
    monkeypatch.setattr(purchase,'checkout_rossko',must_not_call)
    result=await purchase.confirm(attempt.id, purchase.ConfirmPurchase(client_ref='audit-test-ref',confirmation='PLACE_REAL_SUPPLIER_ORDER'), DB())
    assert result['status']=='prepared'
    assert result['order_sent'] is False
    assert attempt.sent_at is None
    assert attempt.error_code=='pre_dispatch_validation_failed'


@pytest.mark.asyncio
async def test_budget_denial_and_error_without_external_calls(monkeypatch):
    monkeypatch.setattr(rate_limit,'get_settings',lambda:SimpleNamespace(app_env='production'))
    class Client:
        async def eval(self,*args):return 0
    from app.modules.offer_engine import cache
    async def get_redis():return Client()
    monkeypatch.setattr(cache,'redis_client',get_redis)
    with pytest.raises(HTTPException) as err:
        await rate_limit.enforce_budget('login','example',limit=5,period=60)
    assert err.value.status_code==429
    async def bad():raise ConnectionError('unreachable')
    monkeypatch.setattr(cache,'redis_client',bad)
    with pytest.raises(HTTPException) as err:
        await rate_limit.enforce_budget('login','example',limit=5,period=60)
    assert err.value.status_code==503


@pytest.mark.asyncio
async def test_facets_count_all_products_even_when_options_truncated(client,admin_headers):
    """Sixty unique published values, but the UI deliberately displays fifty."""
    h=admin_headers
    a='/api/v1/backoffice/catalog-v021'
    brand=(await client.post('/api/v1/backoffice/brands',headers=h,
        json={'name':'AUDITFACET','slug':'auditfacet'})).json()['id']
    cat=(await client.post(a+'/categories',headers=h,
        json={'name':'Audit facet products','slug':'audit-facets-60'})).json()['id']
    attribute=(await client.post(a+'/attributes',headers=h,
        json={'name':'Audit unique value','code':'audit_value60','data_type':'text',
              'filterable':True})).json()['id']
    response=await client.put(f'/api/v1/backoffice/catalog-editor/categories/{cat}/template/{attribute}',
        headers=h,json={'required':False,'show_in_filters':True})
    assert response.status_code==200,response.text
    for n in range(60):
        response=await client.post('/api/v1/backoffice/products',headers=h,
            json={'brand_id':brand,'article':f'AF{n:04}','name':f'Audit Product {n}'})
        assert response.status_code==201,response.text
        pid=response.json()['id']
        response=await client.post(f'{a}/products/{pid}/categories',headers=h,
            json={'category_id':cat,'is_primary':True})
        assert response.status_code==201,response.text
        response=await client.put(f'/api/v1/backoffice/catalog-editor/products/{pid}/attribute-values',
            headers=h,json={'items':[{'attribute_id':attribute,'value':f'code{n:03}'}]})
        assert response.status_code==200,response.text
        response=await client.post(f'/api/v1/backoffice/products/{pid}/publish',headers=h)
        assert response.status_code==200,response.text
    result=await client.get(f'/api/v1/catalog-v021/categories/{cat}/facets')
    assert result.status_code==200,result.text
    facet=result.json()['facets'][0]
    assert facet['products']==60
    assert len(facet['options'])==50
    assert facet['truncated'] is True


@pytest.mark.asyncio
async def test_search_huge_offset_is_rejected_before_query(client):
    result = await client.get('/api/v1/search', params={'q':'trialli','offset':2000000})
    assert result.status_code == 422
    page = await client.get('/catalog', params={'page':40000})
    assert page.status_code == 422
