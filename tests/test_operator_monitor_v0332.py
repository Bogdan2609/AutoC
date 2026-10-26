"""Operator console: read-only, access controlled and no customer PII leakage."""
import uuid
from decimal import Decimal

import pytest

from app.core.db import get_session
from app.main import app
from app.modules.commerce.models import Job, Order, OrderWorkflowEvent
from app.modules.accounts.models import User
from app.presentation.operator_monitor import delivery_issues
from tests.test_operator_workspace_v080 import merchant, node

M = '/api/v1/admin/marketplace'
MON = '/api/v1/backoffice/monitor'


@pytest.mark.asyncio
async def test_monitor_auth_empty_state_and_frontend(client, admin_headers):
    for path in ('configuration', 'attention', 'receiving-readiness', 'orders'):
        assert (await client.get(f'{MON}/{path}')).status_code == 403
        response = await client.get(f'{MON}/{path}', headers=admin_headers)
        assert response.status_code == 200, response.text
    cfg = (await client.get(f'{MON}/configuration', headers=admin_headers)).json()
    assert cfg['supplier_procurement_enabled'] is False
    assert cfg['real_supplier_checkout_enabled'] is False
    assert cfg['financial_operations_verified'] is False
    assert 'secret_key' not in str(cfg)
    assert (await client.get(f'{MON}/attention', headers=admin_headers)).json()['review_orders'] == 0
    page = (await client.get('/operator')).text
    assert 'Центр контроля' in page and 'Готовность приёмки' in page
    js = await client.get('/static/operator.js')
    assert js.status_code == 200
    assert 'receivingReadiness' in js.text and 'async function monitor()' in js.text


@pytest.mark.asyncio
async def test_receiving_readiness_detects_and_hides_contact(client, admin_headers):
    owner = await merchant(client, admin_headers)
    warehouse = await node(client, admin_headers, owner, 'Склад Самара', 'warehouse', 'test-warehouse-v0332')
    supp = await client.post('/api/v1/backoffice/suppliers', json={'name':'ROSSKO','code':'rossko'}, headers=admin_headers)
    assert supp.status_code == 201, supp.text
    acc = await client.post(M+'/supplier-accounts', headers=admin_headers, json={
        'supplier_id':supp.json()['id'], 'merchant_id':owner, 'label':'РОССКО Самара',
        'credential_profile':'default', 'requests_per_hour':25, 'timeout_seconds':15})
    assert acc.status_code == 201, acc.text
    dest = await client.post(M+'/supplier-destinations', headers=admin_headers, json={
        'account_id':acc.json()['id'], 'node_id':warehouse,
        'external_address_id':'A-1', 'external_delivery_id':'000000001'})
    assert dest.status_code == 201, dest.text
    r = await client.get(MON+'/receiving-readiness', headers=admin_headers)
    assert r.status_code == 200, r.text
    row = r.json()['items'][0]
    assert 'receiving_contact_invalid' in row['issues']
    assert 'rossko_courier_required' in row['issues']
    assert row['contact_configured'] is False
    assert 'receiving_contact_name' not in row and 'receiving_contact_phone' not in row
    updated = await client.patch(M+f'/nodes/{warehouse}', headers=admin_headers, json={
        'receiving_contact_name':'Иванов Иван', 'receiving_contact_phone':'+79998887766'})
    assert updated.status_code == 200, updated.text
    row = (await client.get(MON+'/receiving-readiness', headers=admin_headers)).json()['items'][0]
    assert row['contact_configured'] is True
    assert 'receiving_contact_invalid' not in row['issues']
    assert 'Иванов' not in str(row) and '79998887766' not in str(row)


@pytest.mark.asyncio
async def test_order_page_and_redacted_events(client, admin_headers):
    r = await client.post('/api/v1/accounts/register',json={
        'email':'ops-console@example.org', 'password':'VeryStrongPassword123!'})
    assert r.status_code == 201, r.text
    async for db in app.dependency_overrides[get_session]():
        user = await db.scalar(__import__('sqlalchemy').select(User).where(User.email == 'ops-console@example.org'))
        order = Order(user_id=user.id, idempotency_key='ops-test-123456',
                      total=Decimal('153.00'), status='fulfillment_review',
                      contact='Секретный Покупатель', phone='+79992223344',
                      shipping_address='Секретная улица, 5')
        db.add(order)
        await db.flush()
        db.add(OrderWorkflowEvent(order_id=order.id, event_type='operator_quote_offered',
                                  actor_type='operator', evidence={
                                      'phone':'+79992223344', 'contact':'Секретный Покупатель',
                                      'line_id':str(uuid.uuid4())}))
        db.add(Job(dedup_key='ops-test-job-1', kind='procurement', payload={}, status='failed',
                   last_error='contains-secret@example.test'))
        await db.commit()
        oid = str(order.id)
        break
    page = await client.get(MON+'/orders?status=fulfillment_review&page=1&size=1',headers=admin_headers)
    assert page.status_code == 200, page.text
    assert page.json()['total'] == 1
    assert page.json()['items'][0]['id'] == oid
    assert 'Секретная' not in page.text
    assert (await client.get(MON+'/orders?page=0',headers=admin_headers)).status_code == 422
    events = await client.get(MON+f'/orders/{oid}/events',headers=admin_headers)
    assert events.status_code == 200, events.text
    assert events.json()['events'][0]['type'] == 'operator_quote_offered'
    assert events.json()['evidence_redacted'] is True
    assert 'Секретный' not in events.text and '+79992223344' not in events.text
    assert (await client.get(MON+f'/orders/{oid}/events')).status_code == 403
    attention = (await client.get(MON+'/attention',headers=admin_headers)).json()
    assert attention['review_orders'] == 1
    assert attention['failed_jobs'] == 1
    assert 'contains-secret' not in str(attention)
    assert (await client.get(MON+f'/orders/{uuid.uuid4()}/events',headers=admin_headers)).status_code == 404


@pytest.mark.asyncio
async def test_operations_lists_are_paginated_and_redact_raw_errors(client, admin_headers):
    empty = await client.get(MON+'/purchases?page=1&size=2',headers=admin_headers)
    assert empty.status_code == 200 and empty.json()['items'] == []
    assert (await client.get(MON+'/purchases?page=-1',headers=admin_headers)).status_code == 422
    assert (await client.get(MON+'/jobs?size=101',headers=admin_headers)).status_code == 422
    async for db in app.dependency_overrides[get_session]():
        db.add(Job(dedup_key='safe-list-job1',kind='procurement',status='failed',
                   last_error='secret-password-absolutely-should-not-leak'))
        await db.commit()
        break
    jobs = await client.get(MON+'/jobs?status=failed&size=2',headers=admin_headers)
    assert jobs.status_code == 200
    assert jobs.json()['total'] == 1
    assert jobs.json()['items'][0]['error_present'] is True
    assert 'secret-password' not in jobs.text


@pytest.mark.asyncio
async def test_operator_account_code_and_company_binding_field(client, admin_headers):
    owner = await merchant(client, admin_headers)
    warehouse = await node(client, admin_headers, owner, 'Warehouse', 'warehouse', 'company-test-warehouse')
    sup = await client.post('/api/v1/backoffice/suppliers',headers=admin_headers,json={'name':'РоссКо','code':'rossko'})
    account = await client.post(M+'/supplier-accounts',headers=admin_headers,json={
        'supplier_id':sup.json()['id'], 'merchant_id':owner, 'label':'ROSSKO test',
        'credential_profile':'default', 'requests_per_hour':30, 'timeout_seconds':20})
    dest = await client.post(M+'/supplier-destinations',headers=admin_headers,json={
        'account_id':account.json()['id'], 'node_id':warehouse,
        'external_address_id':'ADDR', 'external_delivery_id':'000000002',
        'external_requisite_id':'39619'})
    assert dest.status_code == 201, dest.text
    records = (await client.get('/api/v1/backoffice/workspace/accounts',headers=admin_headers)).json()
    assert records[0]['supplier_code']=='rossko'
    places = (await client.get('/api/v1/backoffice/workspace/destinations',headers=admin_headers)).json()
    assert places[0]['external_requisite_id']=='39619'
