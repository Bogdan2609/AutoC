"""No network; paid order state machine + supplier and stock reconciliation."""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func

from app.main import app
from app.core.db import get_session
from app.modules.commerce.models import Order, OrderLine, Procurement, Job, OrderWorkflowEvent
from app.modules.commerce.sale_lifecycle import (SaleLineExecution, record_paid_fulfillment,
                                                  permitted_transition)
from app.modules.marketplace.models import (Merchant, Store, FulfillmentNode,
                                            SellerOffer, InventoryBalance, InventoryMovement)
from app.modules.payments.models import Payment
from tests.test_marketplace_foundation_v07 import new_user
from tests.test_catalog_integration import create_product


async def funded_order(client, *, own_stock=False, snapshot=None):
    uid, auth = await new_user(client, f'funded-320-{uuid.uuid4().hex}@example.com')
    async for db in app.dependency_overrides[get_session]():
        order = Order(user_id=uuid.UUID(uid), idempotency_key='funded-320-'+uuid.uuid4().hex,
                      total=Decimal('240.00'), total_is_final=True,
                      status='paid_procurement_pending', request_details={'workflow': 'sbp_checkout'},
                      contact='Customer', phone='79999999999', shipping_address='Pickup')
        db.add(order)
        await db.flush()
        line = OrderLine(order_id=order.id, title='Test part', supplier_code='OWN-STOCK' if own_stock else 'rossko',
                         quantity=2, unit_retail=Decimal('120.00'), unit_purchase=Decimal('60.00'),
                         commercial_snapshot=snapshot if own_stock else {'source_kind': 'supplier_quote'})
        if own_stock:
            line.seller_offer_id = uuid.UUID(snapshot['offer_id'])
            line.merchant_id = uuid.UUID(snapshot['merchant_id'])
        db.add(line)
        await db.flush()
        payment = Payment(order_id=order.id, amount=Decimal('240.00'),
                          provider_id='verified-320-'+uuid.uuid4().hex, status='succeeded')
        db.add(payment)
        await record_paid_fulfillment(db, order, [line])
        await db.commit()
        return order.id, line.id, auth


@pytest.mark.asyncio
async def test_supplier_line_to_delivery_and_repeated_reconciliation(client, admin_headers, monkeypatch):
    order_id, line_id, buyer = await funded_order(client)
    endpoint = f'/api/v1/admin/sales/orders/{order_id}/lines/{line_id}/reconcile'
    buyer_api = f'/api/v1/sales/orders/{order_id}'
    assert (await client.get(buyer_api)).status_code == 401
    assert (await client.get(buyer_api, headers=buyer)).json()['payment_verified']
    assert (await client.post(endpoint,json={'target':'supplier_confirmed','evidence':'supplier order 310310', 'reference':'310310'})).status_code == 403
    assert (await client.post(endpoint,headers=admin_headers,json={
        'target':'delivered','evidence':'cannot skip ahead','reference':'shipment-1'})).status_code == 409
    # A pending procurement cannot be confirmed on operator testimony alone.
    premature = await client.post(endpoint, headers=admin_headers, json={
        'target': 'supplier_confirmed', 'evidence': 'supplier order confirmed',
        'reference': 'rossko-123'})
    assert premature.status_code == 409
    # Simulate the explicit operator-review boundary; do not relax production.
    async for db in app.dependency_overrides[get_session]():
        proc = await db.scalar(select(Procurement).where(Procurement.order_line_id == line_id))
        assert proc is not None and proc.status == 'pending'
        proc.status = 'manual_review'
        proc.error = 'test_operator_review_required'
        await db.commit()
        break
    stages=[('supplier_confirmed','supplier order confirmed','rossko-123'),
            ('fulfillment_ready','goods inspected on arrival',None),
            ('dispatched','carrier handed over shipment','carrier-shipment-432'),
            ('delivered','pickup issue documented','pickup-issue-5')]
    for stage, evidence, reference in stages:
        payload={'target':stage,'evidence':evidence,'reference':reference}
        result=await client.post(endpoint,headers=admin_headers,json=payload)
        assert result.status_code == 200, result.text
        again=await client.post(endpoint,headers=admin_headers,json=payload)
        assert again.status_code == 200 and again.json()['reused'] is True
    result=(await client.get(buyer_api,headers=buyer)).json()
    assert result['status']=='fulfilled' and result['all_delivered']
    async for db in app.dependency_overrides[get_session]():
        proc=await db.scalar(select(Procurement).where(Procurement.order_line_id==line_id))
        assert proc.status == 'accepted' and proc.supplier_order_id == 'rossko-123'
        assert await db.scalar(select(func.count()).select_from(Job).where(Job.kind=='procurement')) == 1
        assert await db.scalar(select(func.count()).select_from(SaleLineExecution)) == 1
        assert await db.scalar(select(func.count()).select_from(OrderWorkflowEvent)) >= 5
        order=await db.get(Order,order_id)
        pay=await db.scalar(select(Payment).where(Payment.order_id==order.id))
        break
    # A late or repeated provider status read must not undo a fulfilled order.
    from app.modules.payments import api as payments
    class Status:
        async def status(self, provider_id):
            return {'id':provider_id,'status':'succeeded','paid':True,
                    'metadata':{'order_id':str(order_id)},
                    'payment_method':{'type':'sbp'},
                    'amount':{'value':'240.00','currency':'RUB'}}
    monkeypatch.setattr(payments,'gateway',lambda:Status())
    checked=await client.post(f'/api/v1/payments/orders/{order_id}/refresh',headers=buyer)
    assert checked.status_code == 200, checked.text
    assert checked.json()['order_status'] == 'fulfilled'


@pytest.mark.asyncio
async def test_paid_own_stock_atomic_reservation_and_dispatch(client, admin_headers):
    _, product=await create_product(client,admin_headers)
    assert (await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',
                              headers=admin_headers)).status_code == 200
    async for db in app.dependency_overrides[get_session]():
        merchant=Merchant(name='320 Merchant',slug='sale-320-merchant',status='active')
        db.add(merchant);await db.flush()
        store=Store(merchant_id=merchant.id,name='Warehouse',slug='sale-320-store')
        db.add(store);await db.flush()
        node=FulfillmentNode(merchant_id=merchant.id,store_id=store.id,
             code='sale-320-warehouse',name='Test warehouse',kind='warehouse',
             city='Samara',address='Samara test address',active=True)
        db.add(node);await db.flush()
        offer=SellerOffer(merchant_id=merchant.id,store_id=store.id,
            product_id=uuid.UUID(product['id']),sku='sale-320-offer',source_kind='own_stock',
            price=Decimal('120.00'),status='active')
        db.add(offer);await db.flush()
        balance=InventoryBalance(seller_offer_id=offer.id,node_id=node.id,on_hand=2,reserved=0)
        db.add(balance);await db.commit()
        frozen={'source_kind':'own_stock','inventory_balance_id':str(balance.id),
                'origin_node_id':str(node.id),'offer_id':str(offer.id),
                'merchant_id':str(merchant.id)}
        balance_id=balance.id
        break
    order_id,line_id,buyer=await funded_order(client,own_stock=True,snapshot=frozen)
    root=f'/api/v1/admin/sales/orders/{order_id}/lines/{line_id}'
    reserve=await client.post(root+'/reserve-own-stock',headers=admin_headers)
    assert reserve.status_code==200,reserve.text
    assert (await client.post(root+'/reserve-own-stock',headers=admin_headers)).json()['reused'] is True
    async for db in app.dependency_overrides[get_session]():
        record=await db.get(InventoryBalance,balance_id)
        assert record.on_hand==2 and record.reserved==2
        break
    response=await client.post(root+'/reconcile',headers=admin_headers,json={
        'target':'fulfillment_ready','evidence':'packed and label checked'})
    assert response.status_code==200,response.text
    dispatched=await client.post(root+'/reconcile',headers=admin_headers,json={
        'target':'dispatched','evidence':'courier confirmed pickup','reference':'waybill-42'})
    assert dispatched.status_code==200,dispatched.text
    async for db in app.dependency_overrides[get_session]():
        record=await db.get(InventoryBalance,balance_id)
        assert record.on_hand==0 and record.reserved==0
        entries=(await db.scalars(select(InventoryMovement).where(
            InventoryMovement.balance_id==balance_id))).all()
        assert len(entries)==2
        assert sum(e.delta_reserved for e in entries)==0
        break
    assert (await client.post(root+'/reserve-own-stock',headers=admin_headers)).status_code==409
    delivered=await client.post(root+'/reconcile',headers=admin_headers,json={
        'target':'delivered','evidence':'buyer received item','reference':'buyer-proof-42'})
    assert delivered.status_code==200
    assert (await client.get(f'/api/v1/sales/orders/{order_id}',headers=buyer)).json()['all_delivered']


@pytest.mark.asyncio
async def test_nonfinal_operator_request_never_enters_paid_fulfillment(client,admin_headers):
    uid,_=await new_user(client,'nonfinal320@example.com')
    async for db in app.dependency_overrides[get_session]():
        order=Order(user_id=uuid.UUID(uid),idempotency_key='quote-only-320',
                    total=Decimal('15.00'),total_is_final=False, status='request_quote_accepted',
                    request_details={'workflow':'v017','quote_only':True},
                    contact='Buyer',phone='79999999999',shipping_address='Pickup')
        db.add(order);await db.flush()
        line=OrderLine(order_id=order.id,title='Quote-only',quantity=1,
                       unit_retail=Decimal('15.00'),unit_purchase=Decimal('11.00'),
                       supplier_code='rossko',status='request_quote_accepted')
        db.add(line);await db.commit(); oid=order.id;lid=line.id;break
    response=await client.post(f'/api/v1/admin/sales/orders/{oid}/lines/{lid}/reconcile',
         headers=admin_headers,json={'target':'delivered','evidence':'attempt to forge delivery','reference':'none'})
    assert response.status_code==409
    detail=(await client.get(f'/api/v1/admin/sales/orders/{oid}',headers=admin_headers)).json()
    assert not detail['payment_verified'] and detail['total'] is None


def test_state_machine_denies_skips_and_provisional_purchase():
    assert not permitted_transition('supplier','procurement_pending','delivered')
    assert not permitted_transition('own_stock','inventory_review','dispatched')
    assert permitted_transition('supplier','supplier_confirmed','fulfillment_ready')

@pytest.mark.asyncio
async def test_successful_payment_unique_per_order_in_database(client):
    from sqlalchemy.exc import IntegrityError
    order_id,_,_=await funded_order(client)
    async for db in app.dependency_overrides[get_session]():
        db.add(Payment(order_id=order_id,amount=Decimal('240.00'),
                       provider_id='second-fake-provider',status='succeeded'))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        break


@pytest.mark.asyncio
async def test_unlinked_purchase_cannot_be_prepared_even_when_config_enabled(client,admin_headers,monkeypatch):
    from app.core.config import get_settings
    from app.modules.marketplace import procurement as purchasing
    monkeypatch.setattr(purchasing,'get_settings',lambda: SimpleNamespace(
        supplier_procurement_enabled=True, real_supplier_checkout_enabled=True))
    random=str(uuid.uuid4())
    response=await client.post('/api/v1/admin/supplier-purchases/prepare',headers=admin_headers,json={
        'snapshot_id':random,'destination_id':random,'quantity':1,
        'approved_unit_price':'100.00','client_ref':'unlinked-purchase-320',
        'contact_name':'Buyer Test','contact_phone':'79001234567','payment_id':'1'})
    assert response.status_code==409 and 'mandatory' in response.text
