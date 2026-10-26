"""Offline regression tests: no real payment, refund or supplier side effects."""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func

from app.core.db import get_session
from app.main import app
from app.modules.commerce.models import Job, Order, OrderLine, Replacement, Procurement
from app.modules.payments.models import Payment, Refund
from app.modules.payments.gateway import GatewayError
from app.modules.payments import api as payment_api
from app.workers import runner
from tests.test_marketplace_foundation_v07 import new_user


async def create_order(db, user_id, *, provisional=False):
    order = Order(user_id=user_id, idempotency_key=str(uuid.uuid4()),
                  status='request_awaiting_operator_review' if provisional else 'awaiting_sbp',
                  total=Decimal('100.00'), total_is_final=not provisional,
                  request_details={'workflow':'v017','quote_only':True} if provisional else {},
                  contact='Test Customer', phone='79000000000', shipping_address='Pickup')
    db.add(order)
    await db.flush()
    return order


@pytest.mark.asyncio
async def test_provisional_request_cannot_be_paid_by_forged_or_real_sbp_callback(client,monkeypatch):
    uid, headers = await new_user(client,'v0318-quote-guard@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=await create_order(db,uuid.UUID(uid),provisional=True)
        pay=Payment(order_id=order.id,provider_id='mock-provisional-v0318',
                    amount=Decimal('100.00'),status='pending')
        db.add(pay)
        await db.commit()
        order_id=order.id
        break

    class FakeGateway:
        async def status(self,pid):
            return {'id':pid,'status':'succeeded','paid':True,
                    'metadata':{'order_id':str(order_id)},
                    'amount':{'value':'100.00','currency':'RUB'},
                    'payment_method':{'type':'sbp'}}

    monkeypatch.setattr(payment_api,'gateway',lambda:FakeGateway())
    result=await client.post(f'/api/v1/payments/orders/{order_id}/refresh',headers=headers)
    assert result.status_code==409,result.text
    assert 'Provisional' in result.text
    async for db in app.dependency_overrides[get_session]():
        assert (await db.get(Order,order_id)).status=='request_awaiting_operator_review'
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
        assert (await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='procurement')))==0
        assert (await db.scalar(select(Payment))).status=='pending'
        break


@pytest.mark.asyncio
async def test_refund_uncertainty_never_retries_external_write(client,monkeypatch):
    uid, _ = await new_user(client,'v0318-refund-guard@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=await create_order(db,uuid.UUID(uid))
        line=OrderLine(order_id=order.id,title='Test',supplier_code='partkom',
                       quantity=1,unit_retail=Decimal('100.00'),unit_purchase=Decimal('80.00'))
        db.add(line);await db.flush()
        rep=Replacement(order_line_id=line.id,reason='Not available')
        db.add(rep);await db.flush()
        pay=Payment(order_id=order.id,provider_id='mock-payment-v0318',
                    status='succeeded',amount=Decimal('100.00'))
        db.add(pay);await db.flush()
        ref=Refund(replacement_id=rep.id,payment_id=pay.id,amount=Decimal('100.00'),status='pending')
        db.add(ref);await db.flush()
        refid=ref.id
        await db.commit()
        break
    calls=[]
    class UncertainGateway:
        async def refund(self,*args):
            calls.append(args)
            raise GatewayError('Unknown external state')
    monkeypatch.setattr(runner,'gateway',lambda:UncertainGateway())
    job=SimpleNamespace(payload={'id':str(refid)})
    async for db in app.dependency_overrides[get_session]():
        with pytest.raises(GatewayError):
            await runner.refund(db,job)
        await db.commit()
        assert (await db.get(Refund,refid)).status=='uncertain'
        with pytest.raises(ValueError,match='manual provider reconciliation'):
            await runner.refund(db,job)
        assert len(calls)==1
        break
    queue=await client.get('/api/v1/payments/admin/refunds/needs-reconciliation',headers={'X-Admin-Token':'test-token'})
    assert queue.status_code==200,queue.text
    assert any(x['refund_id']==str(refid) and x['requires_manual_review'] for x in queue.json())
    assert (await client.post(f'/api/v1/payments/admin/refunds/{refid}/reconcile',
            headers={'X-Admin-Token':'test-token'})).status_code==409
    assert (await client.get('/api/v1/payments/admin/refunds/needs-reconciliation')).status_code==403


@pytest.mark.asyncio
async def test_refund_reconciliation_verifies_payment_identity_and_amount(client,monkeypatch):
    uid, _ = await new_user(client,'v0318-refund-reconcile@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=await create_order(db,uuid.UUID(uid))
        line=OrderLine(order_id=order.id,title='Test',supplier_code='partkom',
                       quantity=1,unit_retail=Decimal('100.00'),unit_purchase=Decimal('80.00'))
        db.add(line);await db.flush()
        rep=Replacement(order_line_id=line.id)
        db.add(rep);await db.flush()
        pay=Payment(order_id=order.id,provider_id='pay-for-refund-v0318',
                    status='succeeded',amount=Decimal('100.00'))
        db.add(pay);await db.flush()
        ref=Refund(replacement_id=rep.id,payment_id=pay.id,
                   provider_id='refund-for-reconcile-v0318',amount=Decimal('100.00'),status='uncertain')
        db.add(ref);await db.commit()
        refid=ref.id
        break
    class FakeGateway:
        async def refund_status(self,pid):
            return {'id':pid,'payment_id':'WRONG', 'amount':{'value':'100.00','currency':'RUB'},
                    'status':'succeeded'}
    fake=FakeGateway()
    monkeypatch.setattr(payment_api,'gateway',lambda:fake)
    url=f'/api/v1/payments/admin/refunds/{refid}/reconcile'
    auth={'X-Admin-Token':'test-token'}
    bad=await client.post(url,headers=auth)
    assert bad.status_code==409,bad.text
    async for db in app.dependency_overrides[get_session]():
        assert (await db.get(Refund,refid)).status=='uncertain'
        break
    async def correct(pid):
        return {'id':pid,'payment_id':'pay-for-refund-v0318',
                'amount':{'value':'100.00','currency':'RUB'},'status':'succeeded'}
    fake.refund_status=correct
    result=await client.post(url,headers=auth)
    assert result.status_code==200 and result.json()['status']=='succeeded'
    assert (await client.post(url,headers=auth)).json()['status']=='succeeded'


@pytest.mark.asyncio
async def test_cross_merchant_destination_never_enters_account_search(client,admin_headers,monkeypatch):
    """Even a corrupted/imported old mapping cannot leak another merchant's destination."""
    from app.modules.marketplace import account_search
    from app.modules.marketplace.models import SupplierDestination
    from tests.test_marketplace_foundation_v07 import new_merchant
    from tests.test_v014_integration import new_node
    from tests.test_supplier_offers_v015 import setup_account
    from uuid import UUID
    owner=await new_merchant(client,admin_headers,'Owner Guard','owner-v0318')
    other=await new_merchant(client,admin_headers,'Other Guard','other-v0318')
    own_node=await new_node(client,admin_headers,owner,'own-v0318','warehouse','Самара')
    wrong_node=await new_node(client,admin_headers,other,'other-v0318','warehouse','Самара')
    supplier=(await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,
         json={'code':'rossko','name':'ROSSKO'})).json()['id']
    account,dest=await setup_account(client,admin_headers,supplier,owner,own_node,'SCOPE')
    monkeypatch.setattr(account_search,'get_settings',lambda:SimpleNamespace(supplier_api_enabled=True))
    monkeypatch.setattr(account_search,'ready',lambda *a:True)
    async for db in app.dependency_overrides[get_session]():
        valid,configured=await account_search.available_destinations(db,'rossko')
        assert configured and len(valid)==1
        link=await db.get(SupplierDestination,UUID(dest))
        link.node_id=UUID(wrong_node)  # emulate a legacy/manual data corruption
        await db.commit()
        invalid,configured=await account_search.available_destinations(db,'rossko')
        assert configured and invalid==[]
        break
