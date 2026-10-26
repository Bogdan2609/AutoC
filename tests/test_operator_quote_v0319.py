"""Local-only contract: consent to operator quote must never produce a payment."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.main import app
from app.core.db import get_session
from app.modules.commerce.models import Order, OrderLine, Procurement, Job, OrderWorkflowEvent
from app.modules.payments.models import Payment
from app.modules.marketplace.models import SellerOrder
from tests.test_marketplace_foundation_v07 import new_user


async def seed(client):
    uid, auth = await new_user(client, f'q319-{uuid.uuid4().hex}@example.com')
    async for db in app.dependency_overrides[get_session]():
        order = Order(user_id=uuid.UUID(uid), idempotency_key='q319-'+uuid.uuid4().hex,
                      total=Decimal('155.00'), status='request_awaiting_operator_review',
                      total_is_final=False, request_details={'workflow':'v017', 'quote_only':True},
                      contact='Sample Customer',phone='+79999999999',shipping_address='Pickup')
        db.add(order)
        await db.flush()
        line=OrderLine(order_id=order.id,title='Brake pads',supplier_code='rossko',
                       quantity=2,unit_retail=Decimal('70.00'),unit_purchase=Decimal('50.00'),
                       status=order.status,
                       commercial_snapshot={'source_kind':'supplier_quote', 'supplier_account_id':str(uuid.uuid4()),
                                            'supplier_destination_id':str(uuid.uuid4())})
        db.add(line)
        await db.commit()
        return str(order.id),str(line.id),auth


def data(lineid,price='99.50',fee='25.00',reference='operator-quote-1'):
    return {'lines':[{'line_id':lineid,'unit_price':price,'delivery_fee':fee,
                      'cost_evidence':'supplier-call-reference-2026'}],
            'operator_reference':reference,'valid_for_minutes':30}


@pytest.mark.asyncio
async def test_quote_complete_buyer_consent_no_financial_side_effects(client,admin_headers):
    orderid,lineid,auth=await seed(client)
    url=f'/api/v1/admin/marketplace/order-requests/{orderid}/quote'
    assert (await client.post(url,json=data(lineid))).status_code==403
    assert (await client.post(url,headers=admin_headers,
                              json=data(str(uuid.uuid4())))).status_code==409
    assert (await client.post(url,headers=admin_headers,
                              json=data(lineid,price='45.00'))).status_code==409
    proposed=await client.post(url,headers=admin_headers,json=data(lineid))
    assert proposed.status_code==200, proposed.text
    q=proposed.json()['quote']
    assert q['total']=='224.00' and q['goods_total']=='199.00'
    assert q['delivery_total']=='25.00' and q['nonbinding']
    assert not proposed.json()['payment_enabled']
    retry=await client.post(url,headers=admin_headers,json=data(lineid))
    assert retry.status_code==200 and retry.json()['quote']==q
    seen=await client.get(f'/api/v1/marketplace/order-requests/{orderid}/quote',headers=auth)
    assert seen.status_code==200 and seen.json()['quote']['digest']==q['digest']
    assert (await client.get(f'/api/v1/marketplace/order-requests/{orderid}/quote')).status_code==401
    decision_url=f'/api/v1/marketplace/order-requests/{orderid}/decision'
    wrong=await client.post(decision_url,headers=auth,json={
        'decision':'accept','revision':q['revision']+1,'quote_digest':q['digest']})
    assert wrong.status_code==409
    agreed=await client.post(decision_url,headers=auth,json={
        'decision':'accept','revision':q['revision'],'quote_digest':q['digest']})
    assert agreed.status_code==200 and agreed.json()['status']=='request_quote_accepted'
    assert agreed.json()['quote']['buyer_accepted_at']
    assert (await client.post(decision_url,headers=auth,json={
        'decision':'accept','revision':q['revision'],'quote_digest':q['digest']})).status_code==409
    async for db in app.dependency_overrides[get_session]():
        order=await db.get(Order,uuid.UUID(orderid))
        assert not order.total_is_final and order.total==Decimal('155.00')
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0
        assert (await db.scalar(select(func.count()).select_from(Procurement)))==0
        assert (await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='procurement')))==0
        break
    assert (await client.post(f'/api/v1/marketplace/cart/v017/orders/{orderid}/cancel',headers=auth)).status_code==200
    assert (await client.post(url,headers=admin_headers,json=data(lineid))).status_code==409
    audit=await client.get(f'/api/v1/admin/marketplace/order-requests/{orderid}/audit',headers=admin_headers)
    assert audit.status_code==200 and [e['type'] for e in audit.json()]==[
        'operator_quote_offered','customer_quote_accepted','request_cancelled']
    assert (await client.get(f'/api/v1/admin/marketplace/order-requests/{orderid}/audit')).status_code==403


@pytest.mark.asyncio
async def test_reoffer_invalidates_prior_digest_and_expired_quote(client,admin_headers):
    oid,lid,auth=await seed(client)
    url=f'/api/v1/admin/marketplace/order-requests/{oid}/quote'
    first=(await client.post(url,headers=admin_headers,json=data(lid))).json()['quote']
    declined=await client.post(f'/api/v1/marketplace/order-requests/{oid}/decision',headers=auth,
                                json={'revision':first['revision'],'quote_digest':first['digest'],'decision':'decline'})
    assert declined.status_code==200 and declined.json()['status']=='request_quote_declined'
    second=(await client.post(url,headers=admin_headers,
                              json=data(lid,price='90.00',reference='operator-quote-2'))).json()['quote']
    assert second['revision']==first['revision']+1 and second['digest']!=first['digest']
    old=await client.post(f'/api/v1/marketplace/order-requests/{oid}/decision',headers=auth,
                          json={'revision':first['revision'],'quote_digest':first['digest'],'decision':'accept'})
    assert old.status_code==409
    async for db in app.dependency_overrides[get_session]():
        o=await db.get(Order,uuid.UUID(oid))
        d=dict(o.request_details)
        proposal=dict(d['operator_quote'])
        proposal['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
        d['operator_quote']=proposal
        o.request_details=d
        await db.commit()
        break
    expired=await client.post(f'/api/v1/marketplace/order-requests/{oid}/decision',headers=auth,
                              json={'revision':second['revision'],'quote_digest':second['digest'],'decision':'accept'})
    assert expired.status_code==409
    assert (await client.post(url,headers=admin_headers,json=data(lid,price='90.00',reference='operator-quote-2'))).status_code==200
    # Exact retry must not renew an expired quote.
    still_expired=await client.post(f'/api/v1/marketplace/order-requests/{oid}/decision',headers=auth,
                                    json={'revision':second['revision'],'quote_digest':second['digest'],'decision':'accept'})
    assert still_expired.status_code==409


@pytest.mark.asyncio
async def test_browser_customer_can_read_and_decide_quote_without_payment(client,admin_headers):
    from tests.test_storefront_v04 import csrf
    oid,lid,_=await seed(client)
    async for db in app.dependency_overrides[get_session]():
        order=await db.get(Order,uuid.UUID(oid))
        from app.modules.accounts.models import User
        customer=await db.get(User,order.user_id)
        email=customer.email
        break
    proposal=(await client.post(f'/api/v1/admin/marketplace/order-requests/{oid}/quote',
                                headers=admin_headers,json=data(lid))).json()['quote']
    login=await client.get('/login')
    result=await client.post('/login', data={'csrf':csrf(login.text),
        'email':email,'password':'StrongPasswordXYZ123'}, follow_redirects=False)
    assert result.status_code==303
    page=await client.get(f'/orders/{oid}')
    assert page.status_code==200 and 'Предложение оператора' in page.text
    assert '224.00' in page.text and 'Сумма заказа' not in page.text
    assert 'Согласиться с предложением' in page.text
    bad=await client.post(f'/orders/{oid}/quote/accept',data={
        'csrf':'0'*64,'revision':proposal['revision'], 'quote_digest':proposal['digest']})
    assert bad.status_code==403
    accept=await client.post(f'/orders/{oid}/quote/accept',data={
        'csrf':csrf(page.text),'revision':proposal['revision'], 'quote_digest':proposal['digest']},
        follow_redirects=False)
    assert accept.status_code==303
    accepted=await client.get(f'/orders/{oid}')
    assert 'Предложение принято' in accepted.text
    assert 'Согласиться с предложением' not in accepted.text
    async for db in app.dependency_overrides[get_session]():
        assert (await db.scalar(select(func.count()).select_from(Payment)))==0
        break
