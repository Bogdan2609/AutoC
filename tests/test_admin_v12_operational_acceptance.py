"""v0.12 RBAC and exact read-only diagnostics; never dispatch purchases or payments."""
import uuid
from decimal import Decimal

import pytest

from app.core.db import get_session
from app.main import app
from app.modules.accounts.models import User
from app.modules.commerce.models import Order
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.admin_staff.policy import resource_scope, role_allowed
from app.modules.admin_staff.route_audit import route_audit

MON = '/api/v1/backoffice/monitor'


def test_operations_scope_matches_order_workflow_and_denies_viewer_mutations():
    assert resource_scope('/api/v1/backoffice/operations/orders/00000000-0000-4000-8000-000000000001') == 'orders'
    assert resource_scope('/api/v1/backoffice/operations/lines/00000000-0000-4000-8000-000000000001/propose-replacement') == 'orders'
    assert role_allowed('operator', 'orders', 'POST')
    assert role_allowed('finance_manager', 'orders', 'GET')
    assert not role_allowed('finance_manager', 'orders', 'POST')
    assert not role_allowed('viewer', 'orders', 'GET')
    assert resource_scope(f'{MON}/orders/00000000-0000-4000-8000-000000000001/events') == 'orders'
    assert resource_scope(f'{MON}/purchases/00000000-0000-4000-8000-000000000002') == 'purchases'
    assert not role_allowed('viewer', 'purchases', 'GET')
    assert not role_allowed('supplier_manager', 'purchases', 'POST')
    assert not role_allowed('operator', 'purchases', 'POST')


def test_route_policy_remains_complete_after_new_endpoint():
    report = route_audit(app)
    assert report['policy_complete'], report
    assert report['protected_routes'] >= 310
    assert report['functional_parity_verified'] is False


@pytest.mark.asyncio
async def test_exact_purchase_attempt_lookup_is_read_only_and_redacted(client, admin_headers):
    async for db in app.dependency_overrides[get_session]():
        buyer = User(email='v12-diagnostic@example.test', password_hash='test-hash',
                     display_name='Private Buyer', is_active=True)
        db.add(buyer)
        await db.flush()
        order = Order(user_id=buyer.id, idempotency_key='v12-diagnostic-001',
                      total=Decimal('199.00'), status='fulfillment_review',
                      contact='BUYER PRIVATE NAME', phone='+70000000099',
                      shipping_address='BUYER PRIVATE ADDRESS')
        db.add(order)
        await db.flush()
        attempt = SupplierPurchaseAttempt(
            client_ref='v12-diagnostic-attempt-001', buyer_order_id=order.id,
            supplier_account_id=uuid.uuid4(), supplier_destination_id=uuid.uuid4(),
            snapshot_id=uuid.uuid4(), quantity=2, approved_unit_price=Decimal('50.00'),
            contact_name='WAREHOUSE PRIVATE CONTACT', contact_phone='+71111111111',
            contact_comment='WAREHOUSE INTERNAL COMMENT', payment_id='invoice',
            requisite_id='PRIVATE REQUISITES', status='dispatch_unknown',
            remote_order_ids=['SUPP-12345'], error_code='TIMEOUT')
        db.add(attempt)
        await db.commit()
        attempt_id = str(attempt.id)
        break

    response = await client.get(f'{MON}/purchases/{attempt_id}', headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['id'] == attempt_id
    assert data['status'] == 'dispatch_unknown'
    assert data['remote_order_ids'] == ['SUPP-12345']
    assert data['manual_reconciliation_required'] is True
    assert data['quantity'] == 2
    assert data['sent_at'] is None
    for secret in ('BUYER PRIVATE NAME','BUYER PRIVATE ADDRESS','WAREHOUSE PRIVATE CONTACT',
                   'WAREHOUSE INTERNAL COMMENT','PRIVATE REQUISITES','+70000000099', '+71111111111'):
        assert secret not in response.text
    for key in ('contact_name','contact_phone','contact_comment','requisite_id','payment_id','approved_unit_price'):
        assert key not in data
    # Exact lookup is GET only: there is no mutation handler under this URI.
    assert (await client.post(f'{MON}/purchases/{attempt_id}', headers=admin_headers)).status_code == 405
    assert (await client.get(f'{MON}/purchases/{uuid.uuid4()}', headers=admin_headers)).status_code == 404
    assert (await client.get(f'{MON}/purchases/not-uuid', headers=admin_headers)).status_code == 422
    assert (await client.get(f'{MON}/purchases/{attempt_id}')).status_code == 403
