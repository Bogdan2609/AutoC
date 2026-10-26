"""Contract flow uses fake provider; NEVER sends money or places real supplier orders."""
from decimal import Decimal
import pytest
from app.modules.offer_engine.providers import Offer
import app.modules.commerce.api as commerce_api
import app.modules.payments.api as payment_api
from app.modules.payments.gateway import GatewayError
from tests.test_catalog_integration import create_product

class FakeSBP:
    def __init__(self):self.order_id=None;self.amount=None;self.paid=False
    def config(self):return object()
    async def create(self,order_id,total,items,email):
        self.order_id=order_id;self.amount=total
        return {'id':'fake-payment-123','status':'pending','confirmation':{'confirmation_url':'https://mock-gateway.test/sbp/123'},'payment_method':{'type':'sbp'}}
    async def status(self,provider_id):
        return {'id':provider_id,'status':'succeeded' if self.paid else 'pending','paid':self.paid,'payment_method':{'type':'sbp'},
                'metadata':{'order_id':str(self.order_id)},'amount':{'value':f'{self.amount:.2f}','currency':'RUB'}}

@pytest.mark.asyncio
async def test_accounts_cart_sbp_paid_outbox(client,admin_headers,monkeypatch):
    _,product=await create_product(client,admin_headers)
    r=await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish',headers=admin_headers)
    assert r.status_code==200,r.text
    assert (await client.get('/product/'+product['slug'])).status_code==200
    assert (await client.get('/product/'+product['slug'])).status_code==200
    background=(await client.get('/api/v1/backoffice/operations/jobs',headers=admin_headers)).json()
    assert len([j for j in background if j['kind']=='search'])==4  # dedup refresh by provider
    supp=(await client.post('/api/v1/backoffice/suppliers',json={'code':'partkom','name':'PartKom'},headers=admin_headers)).json()
    item=(await client.post('/api/v1/backoffice/supplier-items',json={'supplier_id':supp['id'],'product_id':product['id'],
            'external_key':'m:0986479088:p:x','raw_brand':'BOSCH','raw_article':product['article']},headers=admin_headers)).json()
    snap=await client.post('/api/v1/backoffice/offer-snapshots',json={'supplier_item_id':item['id'],'purchase_price':'200.00','quantity':5},headers=admin_headers)
    assert snap.status_code==201,snap.text
    assert (await client.post('/api/v1/backoffice/price-policy',json={'markup_percent':'25.00'},headers=admin_headers)).status_code==201
    user=await client.post('/api/v1/accounts/register',json={'email':'buyer@example.org','password':'VeryStrongPassword123!'})
    assert user.status_code==201,user.text
    response=await client.post('/api/v1/accounts/login',json={'email':'buyer@example.org','password':'VeryStrongPassword123!'})
    assert response.status_code==200,response.text
    h={'Authorization':f'Bearer {response.json()["access_token"]}'}
    cart=await client.post('/api/v1/cart/items',json={'supplier_item_id':item['id'],'quantity':2},headers=h)
    assert cart.status_code==201,cart.text
    assert (await client.get('/api/v1/cart',headers=h)).json()['items'][0]['quantity']==2
    # Test unconfigured payment MUST fail before checkout order is created.
    blocked=await client.post('/api/v1/checkout',headers={**h,'Idempotency-Key':'orderkey-0001'},
           json={'contact':'Тест Покупатель','phone':'+79995557755','shipping_address':'Тестовая улица, дом 1'})
    assert blocked.status_code==503,blocked.text
    fake=FakeSBP()
    async def fake_verify(code,key,brand,article,quantity):
        assert (code,key,quantity)==('partkom','m:0986479088:p:x',2)
        return Offer('partkom',key,brand,article,Decimal('202.00'),6,1,'provider')
    monkeypatch.setattr(commerce_api,'gateway',lambda:fake)
    monkeypatch.setattr(commerce_api,'verify',fake_verify)
    monkeypatch.setattr(payment_api,'gateway',lambda:fake)
    body={'contact':'Тест Покупатель','phone':'+79995557755','shipping_address':'Тестовая улица, дом 1'}
    adjusted=await client.post('/api/v1/checkout',headers={**h,'Idempotency-Key':'orderkey-0001'},json=body)
    assert adjusted.status_code==409 and adjusted.json()['detail']['changes'][0]['new_retail_price']=='252.50'
    assert (await client.get('/api/v1/orders',headers=h)).json()==[] # no silent price increase, no order
    checkout=await client.post('/api/v1/checkout',headers={**h,'Idempotency-Key':'orderkey-0001'},json=body)
    assert checkout.status_code==200,checkout.text
    assert checkout.json()['total']=='505.00' # 202 * 1.25 * 2
    oid=checkout.json()['order_id']
    duplicate=await client.post('/api/v1/checkout',headers={**h,'Idempotency-Key':'orderkey-0001'},json=body)
    assert duplicate.status_code==200 and duplicate.json()['reused'] is True
    changed_key=await client.post('/api/v1/checkout',headers={**h,'Idempotency-Key':'orderkey-0001'},
                                  json={**body,'phone':'+79995557756'})
    assert changed_key.status_code==409  # Reused key cannot silently accept new buyer details
    assert (await client.get(f'/api/v1/orders/{oid}',headers=h)).json()['status']=='awaiting_sbp'
    callback={'type':'notification','event':'payment.succeeded','object':{'id':'fake-payment-123','status':'succeeded'}}
    forged=await client.post('/api/v1/payments/yookassa/webhook',json=callback)
    assert forged.status_code==200
    assert (await client.get(f'/api/v1/orders/{oid}',headers=h)).json()['status']=='awaiting_sbp'
    fake.paid=True
    for _ in range(2):
        pay=await client.post('/api/v1/payments/yookassa/webhook',json=callback)
        assert pay.status_code==200,pay.text
    detail=(await client.get(f'/api/v1/orders/{oid}',headers=h)).json()
    assert detail['status']=='paid_procurement_pending',detail
    admin=await client.get(f'/api/v1/backoffice/operations/orders/{oid}',headers=admin_headers)
    assert admin.status_code==200
    assert admin.json()['items'][0]['procurement']['status']=='pending'  # worker executes later; purchase not sent on webhook
    jobs=(await client.get('/api/v1/backoffice/operations/jobs',headers=admin_headers)).json()
    assert len([j for j in jobs if j['kind']=='procurement'])==1
    # A different user cannot inspect the order.
    attacker=await client.post('/api/v1/accounts/register',json={'email':'other@example.org','password':'AnotherStrongPassword123!'})
    assert attacker.status_code==201
    attacker_login=(await client.post('/api/v1/accounts/login',json={'email':'other@example.org','password':'AnotherStrongPassword123!'})).json()
    assert (await client.get(f'/api/v1/orders/{oid}',headers={'Authorization':'Bearer '+attacker_login['access_token']})).status_code==404

@pytest.mark.asyncio
async def test_no_anonymous_cart(client):
    assert (await client.get('/api/v1/cart')).status_code==401
    assert (await client.get('/api/v1/backoffice/operations/overview')).status_code==403
