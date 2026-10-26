"""v0.11 diagnostics are read-only; HTTP tests never reach real supplier/payment hosts."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User
from app.modules.commerce.models import Order
from app.modules.marketplace.procurement import SupplierPurchaseAttempt

MON='/api/v1/backoffice/monitor'


@pytest.mark.asyncio
async def test_purchase_diagnostics_scoped_to_exact_order(client, admin_headers):
    # Test database only. Deliberately no supplier API call or procurement write endpoint.
    async for db in app.dependency_overrides[get_session]():
        user=User(email='v11-purchases@example.test', password_hash='not-for-login',
                  display_name='Test', is_active=True)
        db.add(user)
        await db.flush()
        orders=[]
        for i in range(2):
            o=Order(user_id=user.id, idempotency_key=f'v11-order-{i}',
                    total=Decimal('125.00'),status='fulfillment_review',
                    contact='Test customer', phone='+79990000000',shipping_address='Redacted')
            db.add(o)
            orders.append(o)
        await db.flush()
        for i,o in enumerate(orders):
            db.add(SupplierPurchaseAttempt(
                client_ref=f'v11-diagnostic-attempt-{i}', buyer_order_id=o.id,
                supplier_account_id=uuid.uuid4(),supplier_destination_id=uuid.uuid4(),
                snapshot_id=uuid.uuid4(),quantity=1,approved_unit_price=Decimal('100.00'),
                contact_name='Warehouse',contact_phone='+79990000000',payment_id='invoice',
                status='manual_review',remote_order_ids=[],error_code=f'TEST_{i}'))
        await db.commit()
        ids=[str(o.id) for o in orders]
        break
    for i,oid in enumerate(ids):
        r=await client.get(f'{MON}/purchases',params={'order_id':oid,'size':1},headers=admin_headers)
        assert r.status_code==200,r.text
        assert r.json()['total']==1
        assert len(r.json()['items'])==1
        assert r.json()['items'][0]['order_id']==oid
        assert r.json()['items'][0]['error_code']==f'TEST_{i}'
        assert 'Test customer' not in r.text
    all_items=await client.get(f'{MON}/purchases',headers=admin_headers)
    assert all_items.status_code==200 and all_items.json()['total']==2


@pytest.mark.asyncio
async def test_purchase_filter_validates_uuid_and_requires_admin(client,admin_headers):
    identifier=str(uuid.uuid4())
    assert (await client.get(f'{MON}/purchases',params={'order_id':identifier})).status_code==403
    assert (await client.get(f'{MON}/purchases',params={'order_id':'invalid'},headers=admin_headers)).status_code==422
    r=await client.get(f'{MON}/purchases',params={'order_id':identifier},headers=admin_headers)
    assert r.status_code==200 and r.json()['total']==0
