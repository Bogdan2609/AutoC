"""Ensure admin forced refresh reaches exact selected supplier account, despite cached EMPTY."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from app.modules.accounts.models import utc
import pytest


@pytest.mark.asyncio
async def test_service_forwards_manual_force_and_supplier_scope(monkeypatch):
    from app.modules.offer_engine import service
    calls=[]
    async def configured(_db,_code): return ([],True)
    async def capture(_db,brand,article,product_id,*,force=False,codes=None):
        calls.append((brand,article,product_id,force,codes))
        return []
    monkeypatch.setattr('app.modules.marketplace.account_search.available_destinations',configured)
    monkeypatch.setattr('app.modules.marketplace.account_search.enqueue_accounts',capture)
    class Db:
        async def commit(self): pass
    product=SimpleNamespace(id=uuid4(),article='CF705')
    brand=SimpleNamespace(name='TRIALLI')
    await service.enqueue_product(Db(),product,brand,codes=('rossko',),force=True)
    assert calls==[('TRIALLI','CF705',product.id,True,('rossko',))]


@pytest.mark.asyncio
async def test_account_forced_refresh_bypasses_unexpired_negative_cache(monkeypatch):
    from app.modules.marketplace import account_search
    now=utc()
    state=SimpleNamespace(status='empty',expires_at=now+timedelta(minutes=8),
                          last_requested_at=now-timedelta(minutes=2),product_id=uuid4())
    account=SimpleNamespace(id=uuid4())
    destination=SimpleNamespace(id=uuid4())
    node=SimpleNamespace(city='Novokuybyshevsk')
    requested_codes=[]
    async def available(_db,code,*,city=None):
        requested_codes.append(code)
        return ([(account,destination,node)],True)
    monkeypatch.setattr(account_search,'available_destinations',available)
    class Db:
        def __init__(self):self.jobs=[]
        async def scalar(self,_q):return state
        def add(self,job):self.jobs.append(job)
    db=Db()
    skipped=await account_search.enqueue_accounts(db,'Trialli','CF705',state.product_id,codes=('rossko',),force=False)
    assert skipped==[]
    assert db.jobs==[]
    forced=await account_search.enqueue_accounts(db,'Trialli','CF705',state.product_id,codes=('rossko',),force=True)
    assert len(forced)==1
    assert forced[0]['queued'] is True
    assert len(db.jobs)==1
    assert db.jobs[0].payload['supplier']=='rossko'
    assert db.jobs[0].payload['account_id']==str(account.id)
    assert db.jobs[0].payload['destination_id']==str(destination.id)
    assert state.status=='refreshing'
    assert requested_codes==['rossko','rossko']
