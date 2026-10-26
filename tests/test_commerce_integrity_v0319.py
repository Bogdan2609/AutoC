import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func
from app.main import app
from app.core.db import get_session
from app.modules.commerce.models import Order, OrderLine, Procurement, Replacement, Job
from app.modules.payments.models import Payment, Refund
from app.workers.runner import procurement
from tests.test_marketplace_foundation_v07 import new_user


@pytest.mark.asyncio
async def test_refund_budget_serializes_all_replacements(client,admin_headers):
    uid,auth=await new_user(client,'refund-budget-319@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=Order(user_id=uuid.UUID(uid),idempotency_key='budget-'+str(uuid.uuid4()),
                    total=Decimal('100'),total_is_final=True,status='paid_procurement_pending',
                    request_details={'workflow':'sbp_checkout'},contact='Buyer',
                    phone='7000000000',shipping_address='Pickup')
        db.add(order);await db.flush()
        reps=[]
        for i in range(2):
            line=OrderLine(order_id=order.id,title='Item',supplier_code='partkom',
                           quantity=1,unit_retail=Decimal('60.00'),unit_purchase=Decimal('30.00'))
            db.add(line);await db.flush()
            rep=Replacement(order_line_id=line.id,status='awaiting_customer')
            db.add(rep);await db.flush()
            reps.append(rep.id)
        db.add(Payment(order_id=order.id,provider_id='319-paid',
                       amount=Decimal('100.00'),status='succeeded'))
        await db.commit()
        oid=order.id
        break
    url=lambda rep:f'/api/v1/orders/{oid}/replacements/{rep}/decline'
    accepted=await client.post(url(reps[0]),headers=auth)
    assert accepted.status_code==200,accepted.text
    duplicate=await client.post(url(reps[0]),headers=auth)
    assert duplicate.status_code==409
    exceeds=await client.post(url(reps[1]),headers=auth)
    assert exceeds.status_code==409 and 'exceed' in exceeds.text
    assert (await client.get('/api/v1/admin/commerce/readiness')).status_code==403
    readiness=await client.get('/api/v1/admin/commerce/readiness',headers=admin_headers)
    assert readiness.status_code==200 and not readiness.json()['live_sales_ready']
    integrity=await client.get(f'/api/v1/admin/commerce/orders/{oid}/integrity',headers=admin_headers)
    assert integrity.status_code==200 and integrity.json()['refunds_reserved_total']=='60.00'
    async for db in app.dependency_overrides[get_session]():
        assert (await db.scalar(select(func.count()).select_from(Refund)))==1
        assert (await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='refund')))==1
        break


@pytest.mark.asyncio
async def test_unfunded_procurement_is_never_attempted(client,admin_headers):
    uid,_=await new_user(client,'unfunded-procurement-319@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=Order(user_id=uuid.UUID(uid),idempotency_key='unfunded-'+str(uuid.uuid4()),
                    total=Decimal('100'),total_is_final=True,status='paid_procurement_pending',
                    request_details={'workflow':'sbp_checkout'},contact='Buyer',
                    phone='7000000000',shipping_address='Pickup')
        db.add(order);await db.flush()
        line=OrderLine(order_id=order.id,title='Item',supplier_code='rossko',
                       quantity=1,unit_retail=Decimal('100'),unit_purchase=Decimal('80'))
        db.add(line);await db.flush()
        proc=Procurement(order_line_id=line.id,supplier_code='rossko')
        db.add(proc);await db.flush()
        await procurement(db,SimpleNamespace(payload={'id':str(proc.id)}))
        await db.commit()
        assert proc.status=='manual_review' and 'Unverified' in proc.error
        oid=order.id
        break
    result=await client.get(f'/api/v1/admin/commerce/orders/{oid}/integrity',headers=admin_headers)
    assert 'procurement_without_successful_payment' in result.json()['blocking_reasons']


@pytest.mark.asyncio
async def test_unrecognized_workflow_cannot_be_settled(client,monkeypatch):
    from app.modules.payments import api as payment_api
    uid,auth=await new_user(client,'workflow-guard-319@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=Order(user_id=uuid.UUID(uid),idempotency_key='unknown-'+str(uuid.uuid4()),
                    total=Decimal('100'),total_is_final=True,status='awaiting_sbp',
                    request_details={'workflow':'manual_placeholder'},contact='Buyer',
                    phone='7000000000',shipping_address='Pickup')
        db.add(order);await db.flush()
        db.add(OrderLine(order_id=order.id,title='Item',supplier_code='rossko',
                         quantity=1,unit_retail=Decimal('100'),unit_purchase=Decimal('70')))
        db.add(Payment(order_id=order.id,provider_id='319-workflow',
                       amount=Decimal('100'),status='pending'))
        await db.commit()
        oid=order.id
        break
    class Gateway:
        async def status(self,pid):
            return {'id':pid,'metadata':{'order_id':str(oid)},'status':'succeeded',
                    'paid':True,'payment_method':{'type':'sbp'},
                    'amount':{'value':'100.00','currency':'RUB'}}
    monkeypatch.setattr(payment_api,'gateway',lambda:Gateway())
    result=await client.post(f'/api/v1/payments/orders/{oid}/refresh',headers=auth)
    assert result.status_code==409 and 'not a verified SBP' in result.text
