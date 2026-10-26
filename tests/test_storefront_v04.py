"""Browser flow tests: form CSRF, HttpOnly cookies, cart, checkout and isolation."""
import re

import pytest

from tests.test_catalog_integration import create_product


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([a-f0-9]{64})"', html)
    assert match, "Rendered page missing CSRF token"
    return match.group(1)


@pytest.mark.asyncio
async def test_browser_accounts_session_csrf_logout(client):
    assert (await client.get('/cart', follow_redirects=False)).status_code == 303
    page = await client.get('/register')
    assert page.status_code == 200
    assert 'ap_csrf_seed' in client.cookies
    bad = await client.post('/register', data={
        'csrf': '0'*64, 'email': 'new@example.com', 'password': 'StrongPassword123456',
    })
    assert bad.status_code == 403
    r = await client.post('/register', data={
        'csrf': csrf(page.text), 'email': 'new@example.com', 'password': 'StrongPassword123456',
        'display_name': 'New Customer', 'next': '//attacker.example/steal',
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers['location'] == '/account'
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert 'ap_browser_session' in client.cookies
    account = await client.get('/account')
    assert account.status_code == 200 and 'New Customer' in account.text
    token = csrf(account.text)
    assert (await client.post('/logout', data={'csrf': 'x'*64})).status_code == 403
    exit_result = await client.post('/logout', data={'csrf': token}, follow_redirects=False)
    assert exit_result.status_code == 303
    assert (await client.get('/account', follow_redirects=False)).status_code == 303


@pytest.mark.asyncio
async def test_browser_login_address_cart_and_offer(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    supplier = (await client.post('/api/v1/backoffice/suppliers', json={
        'code':'partkom','name':'Partkom'}, headers=admin_headers)).json()
    item = (await client.post('/api/v1/backoffice/supplier-items', json={
        'supplier_id':supplier['id'], 'product_id':product['id'], 'external_key':'m:0986479088:p:x',
        'raw_brand':'BOSCH','raw_article':product['article'],
    }, headers=admin_headers)).json()
    assert (await client.post('/api/v1/backoffice/offer-snapshots', json={
        'supplier_item_id':item['id'], 'purchase_price':'100.00','quantity':3,
    }, headers=admin_headers)).status_code == 201
    assert (await client.post('/api/v1/backoffice/price-policy', json={
        'markup_percent':'25.00'}, headers=admin_headers)).status_code == 201
    offers = (await client.get(f'/api/v1/products/{product["id"]}/offers')).json()
    assert offers[0]['supplier_item_id'] == item['id']
    page = await client.get(f'/product/{product["slug"]}')
    assert 'Войти, чтобы добавить' in page.text
    reg = await client.get('/register')
    await client.post('/register', data={
        'csrf':csrf(reg.text),'email':'client@example.org','password':'BrowserPassword12345',
    }, follow_redirects=False)
    product_page = await client.get(f'/product/{product["slug"]}')
    assert 'В корзину' in product_page.text
    assert f'value="{item["id"]}"' in product_page.text
    assert (await client.post('/cart/items', data={'csrf':'0'*64,
        'supplier_item_id':item['id'],'quantity':'2'})).status_code == 403
    add = await client.post('/cart/items', data={
        'csrf':csrf(product_page.text), 'supplier_item_id':item['id'], 'quantity':'2',
    }, follow_redirects=False)
    assert add.status_code == 303 and add.headers['location']=='/cart', add.text
    cart_page = await client.get('/cart')
    assert cart_page.status_code==200
    assert '250.00' in cart_page.text
    assert 'Базовая' not in cart_page.text
    account = await client.get('/account')
    add_address = await client.post('/account/addresses', data={
        'csrf':csrf(account.text),'contact':'Имя Клиента','phone':'+79990000000',
        'address':'Москва, улица Тестовая, дом 4',
    }, follow_redirects=False)
    assert add_address.status_code==303
    assert 'Москва, улица Тестовая' in (await client.get('/account')).text
    checkout_page = await client.get('/checkout')
    assert checkout_page.status_code == 200
    assert 'Москва, улица Тестовая' in checkout_page.text
    # Fake gateway isn't enabled: zero payment/order creation.
    submit = await client.post('/checkout', data={
        'csrf':csrf(checkout_page.text), 'idem_key':'a'*32,
        'contact':'Имя Клиента','phone':'+79990000000',
        'shipping_address':'Москва, улица Тестовая, дом 4',
    })
    assert submit.status_code == 409
    assert 'Оплата СБП ещё не подключена' in submit.text
    assert (await client.get('/orders')).status_code == 200
    assert 'Заказов ещё нет' in (await client.get('/orders')).text
    line_id = (await client.get('/api/v1/cart',headers={
        'Authorization':'Bearer incorrect'})).status_code
    assert line_id == 401  # browser cookie never authenticates the separate bearer API
    match = re.search(r'/cart/items/([a-f0-9-]+)/quantity', cart_page.text)
    assert match
    line_id = match.group(1)
    quantity = await client.post(f'/cart/items/{line_id}/quantity', data={
        'csrf':csrf(cart_page.text),'quantity':'3',
    }, follow_redirects=False)
    assert quantity.status_code==303
    assert '375.00' in (await client.get('/cart')).text
    remove = await client.post(f'/cart/items/{line_id}/remove', data={
        'csrf':csrf((await client.get('/cart')).text),
    }, follow_redirects=False)
    assert remove.status_code==303
    assert 'В корзине пока пусто' in (await client.get('/cart')).text


@pytest.mark.asyncio
async def test_browser_login_again_after_logout(client):
    register = await client.get('/register')
    await client.post('/register', data={'csrf':csrf(register.text),
        'email':'example@example.com','password':'StrongPassword123456'},
        follow_redirects=False)
    account = await client.get('/account')
    await client.post('/logout', data={'csrf':csrf(account.text)}, follow_redirects=False)
    login = await client.get('/login')
    bad = await client.post('/login', data={
        'csrf':csrf(login.text), 'email':'example@example.com','password':'bad',
    })
    assert bad.status_code==400
    assert 'Не удалось войти' in bad.text
    # A failed attempt retains the anonymous CSRF cookie.
    succeed = await client.post('/login', data={
        'csrf':csrf(login.text), 'email':'example@example.com',
        'password':'StrongPassword123456', 'next':'/orders',
    }, follow_redirects=False)
    assert succeed.status_code == 303 and succeed.headers['location'] == '/orders'
    assert (await client.get('/orders')).status_code==200

@pytest.mark.asyncio
async def test_browser_order_payment_and_cross_account_isolation(client, admin_headers, monkeypatch):
    """Browser uses tested checkout API, never invents its own SBP workflow."""
    from decimal import Decimal
    from app.modules.offer_engine.providers import Offer
    from tests.test_commerce_flow import FakeSBP
    import app.modules.commerce.api as commerce_api
    import app.modules.payments.api as payment_api

    _, product = await create_product(client, admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    supplier = (await client.post('/api/v1/backoffice/suppliers', json={
        'code':'partkom', 'name':'Partkom'}, headers=admin_headers)).json()
    item = (await client.post('/api/v1/backoffice/supplier-items', json={
        'supplier_id':supplier['id'], 'product_id':product['id'],
        'external_key':'m:0986479088:p:x','raw_brand':'BOSCH','raw_article':product['article'],
    }, headers=admin_headers)).json()
    await client.post('/api/v1/backoffice/offer-snapshots', json={
        'supplier_item_id':item['id'], 'purchase_price':'100.00','quantity':3,
    }, headers=admin_headers)
    await client.post('/api/v1/backoffice/price-policy', json={
        'markup_percent':'25.00'}, headers=admin_headers)
    page = await client.get('/register')
    await client.post('/register', data={'csrf':csrf(page.text),
        'email':'owner@example.com','password':'BrowserPassword12345'}, follow_redirects=False)
    p = await client.get('/product/'+product['slug'])
    await client.post('/cart/items', data={'csrf':csrf(p.text),
        'supplier_item_id':item['id'], 'quantity':'1'}, follow_redirects=False)
    fake = FakeSBP()
    async def fake_verify(code,key,brand,article,quantity):
        return Offer('partkom',key,brand,article,Decimal('100.00'),3,1,'provider')
    monkeypatch.setattr(commerce_api, 'gateway', lambda:fake)
    monkeypatch.setattr(commerce_api, 'verify', fake_verify)
    monkeypatch.setattr(payment_api, 'gateway', lambda:fake)
    checkout = await client.get('/checkout')
    assert checkout.status_code==200
    assert 'Система быстрых платежей' in checkout.text
    idem = re.search(r'name="idem_key" value="([a-f0-9]{32})"', checkout.text).group(1)
    submitted = await client.post('/checkout', data={
        'csrf':csrf(checkout.text), 'idem_key':idem, 'contact':'Тест Покупатель',
        'phone':'+79990000000', 'shipping_address':'Москва, улица Тестовая, дом 4',
    }, follow_redirects=False)
    assert submitted.status_code==303, submitted.text
    assert submitted.headers['location'].startswith('/orders/')
    order_url = submitted.headers['location']
    detail = await client.get(order_url)
    assert detail.status_code==200
    assert 'mock-gateway.test' in detail.text  # accepted only under APP_ENV=test
    assert '125.00' in detail.text
    assert 'pending' in detail.text
    # Provider still pending -> no procurement until authoritative verification succeeds.
    refresh = await client.post(order_url+'/refresh', data={'csrf':csrf(detail.text)}, follow_redirects=False)
    assert refresh.status_code==303
    fake.paid = True
    detail = await client.get(order_url)
    refresh = await client.post(order_url+'/refresh', data={'csrf':csrf(detail.text)}, follow_redirects=False)
    assert refresh.status_code==303
    paid_page = await client.get(order_url)
    assert 'paid_procurement_pending' in paid_page.text
    # A second browser must never see another user's order.
    await client.post('/logout', data={'csrf':csrf(paid_page.text)}, follow_redirects=False)
    outsider = await client.get('/register')
    await client.post('/register', data={'csrf':csrf(outsider.text),
        'email':'outsider@example.org','password':'AnotherPassword12345'}, follow_redirects=False)
    assert (await client.get(order_url)).status_code==404


@pytest.mark.asyncio
async def test_unsafe_redirect_and_csrf_form_replay(client):
    from app.presentation.browser_security import safe_next
    assert safe_next('//evil.example')=='/account'
    assert safe_next('http://evil.example')=='/account'
    assert safe_next('/\\evil.example')=='/account'
    assert safe_next('/cart')=='/cart'
    form = await client.get('/register')
    token = csrf(form.text)
    # Do not allow POST with the correct token from a different anonymous browser.
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://testserver') as second:
        reply = await second.post('/register', data={
            'csrf':token, 'email':'impostor@example.org',
            'password':'StrongPassword123456',
        })
        assert reply.status_code == 403
