"""Regression tests for background supplier account / destination boundaries.

No external supplier HTTP requests or real purchases are made in these tests.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import utc
from app.modules.commerce.models import Job
from app.modules.offer_engine.models import CacheState
from app.modules.marketplace.account_search import account_key
from app.modules.suppliers.models import OfferSnapshot
from app.modules.offer_engine.providers import Offer
from app.workers import runner
from tests.test_marketplace_foundation_v07 import ADMIN, new_merchant


async def _scope(client, headers, monkeypatch, *, code='partkom'):
    merchant = await new_merchant(client, headers, 'Scope Safety', 'scope-safety')
    supplier = (await client.post('/api/v1/backoffice/suppliers', headers=headers,
        json={'code':code, 'name':'Partkom'})).json()['id']
    node = (await client.post(ADMIN+'/nodes', headers=headers,
        json={'merchant_id':merchant, 'code':'safe-node', 'name':'Our warehouse',
              'kind':'warehouse', 'city':'Казань', 'address':'Казань, тест 1'})).json()['id']
    account = (await client.post(ADMIN+'/supplier-accounts', headers=headers,
        json={'supplier_id':supplier, 'merchant_id':merchant, 'label':'Partkom scoped',
              'credential_profile':'SAFE'})).json()['id']
    destination = (await client.post(ADMIN+'/supplier-destinations', headers=headers,
        json={'account_id':account, 'node_id':node})).json()['id']
    assert (await client.patch(f'{ADMIN}/supplier-destinations/{destination}', headers=headers,
        json={'enabled':True,'verified':True})).status_code==200
    assert (await client.patch(f'{ADMIN}/supplier-accounts/{account}', headers=headers,
        json={'enabled':True,'read_enabled':True})).status_code==200
    monkeypatch.setenv('SUPPLIER_PROFILE_SAFE_PARTKOM_LOGIN', 'scoped')
    monkeypatch.setenv('SUPPLIER_PROFILE_SAFE_PARTKOM_PASSWORD', 'secret')
    monkeypatch.setenv('SUPPLIER_API_ENABLED','true')
    get_settings.cache_clear()
    return account, destination


async def _seed_search(db, account=None, destination=None, *, key=None):
    stamp = utc()
    if key is None:
        key = (account_key('BOSCH','0986479088',UUID(account),UUID(destination))
               if account else 'BOSCH:0986479088:default')
    state=CacheState(supplier_code='partkom', lookup_key=key, status='refreshing',
                     last_requested_at=stamp, expires_at=stamp+timedelta(minutes=5))
    job=Job(kind='search',status='pending',dedup_key='scope:'+str(uuid4()),
             payload={'supplier':'partkom','brand':'BOSCH','article':'0986479088',
                      'key':key,'requested_at':stamp.isoformat(),
                      **({'account_id':account,'destination_id':destination} if account else {})})
    db.add(state)
    db.add(job)
    await db.flush()
    return state,job


@pytest.mark.asyncio
async def test_queued_legacy_search_cannot_use_global_credentials_after_account_registration(
        client, admin_headers, monkeypatch):
    async for db in app.dependency_overrides[get_session]():
        state,job = await _seed_search(db)
        await db.commit()
    await _scope(client,admin_headers,monkeypatch)
    class NeverQuerySupplier:
        async def search(self,*a,**kw):
            pytest.fail('legacy supplier API call escaped scoped account boundary')
    monkeypatch.setitem(runner.PROVIDERS,'partkom',NeverQuerySupplier())
    async for db in app.dependency_overrides[get_session]():
        await runner.search(db,job)
        await db.commit()
        assert (await db.scalar(select(CacheState).where(CacheState.id==state.id))).status=='disabled'
        assert (await db.scalars(select(OfferSnapshot))).all()==[]


@pytest.mark.asyncio
async def test_account_job_with_another_scope_cache_key_cannot_call_supplier(
        client, admin_headers, monkeypatch):
    account, destination = await _scope(client,admin_headers,monkeypatch)
    foreign_key=account_key('BOSCH','0986479088',uuid4(),uuid4())
    class NeverQuerySupplier:
        async def search(self,*a,**kw):
            pytest.fail('job was allowed to read data from a different scope')
    monkeypatch.setitem(runner.PROVIDERS,'partkom',NeverQuerySupplier())
    async for db in app.dependency_overrides[get_session]():
        state,job = await _seed_search(db,account,destination,key=foreign_key)
        await runner.search(db,job)
        assert state.status=='refreshing'
        assert (await db.scalars(select(OfferSnapshot))).all()==[]


@pytest.mark.asyncio
async def test_account_deactivated_during_provider_request_cannot_publish_snapshots(
        client, admin_headers, monkeypatch):
    account, destination = await _scope(client,admin_headers,monkeypatch)
    from app.modules.marketplace.models import SupplierAccount
    async for db in app.dependency_overrides[get_session]():
        state,job=await _seed_search(db,account,destination)
        await db.commit()
        class DeactivateDuringRead:
            async def search(self,brand,article,config):
                assert config.partkom_login=='scoped'
                obj=await db.get(SupplierAccount,UUID(account))
                obj.enabled=False
                await db.commit()
                return [Offer('partkom','offer-id',brand,article,Decimal('1000'),7,1,
                              'token','WH')]
        monkeypatch.setitem(runner.PROVIDERS,'partkom',DeactivateDuringRead())
        await runner.search(db,job)
        await db.commit()
        assert (await db.scalar(select(CacheState).where(CacheState.id==state.id))).status=='disabled'
        assert (await db.scalars(select(OfferSnapshot))).all()==[]
