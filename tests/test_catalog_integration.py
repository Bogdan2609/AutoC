from datetime import datetime, timedelta, timezone

import pytest


async def create_product(client, admin_headers):
    brand = await client.post("/api/v1/backoffice/brands", json={"name": "BOSCH", "slug": "bosch"}, headers=admin_headers)
    assert brand.status_code == 201, brand.text
    new = await client.post("/api/v1/backoffice/products", json={
        "brand_id": brand.json()["id"], "article": "0 986 479 088", "name": "Диск тормозной BOSCH",
    }, headers=admin_headers)
    assert new.status_code == 201, new.text
    return brand.json(), new.json()


@pytest.mark.asyncio
async def test_catalog_draft_publish_and_duplicate(client, admin_headers):
    brand, product = await create_product(client, admin_headers)
    assert (await client.get("/api/v1/search", params={"q": "0986479088"})).json()["count"] == 0
    assert (await client.get(f'/api/v1/products/{product["id"]}')).status_code == 404
    duplicate = await client.post("/api/v1/backoffice/products", json={
        "brand_id": brand["id"], "article": "0986-479-088", "name": "Дубликат",
    }, headers=admin_headers)
    assert duplicate.status_code == 409
    published = await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    assert published.status_code == 200
    search = (await client.get("/api/v1/search", params={"q": "0986-479-088"})).json()
    assert search["count"] == 1
    assert search["items"][0]["id"] == product["id"]
    public = await client.get(f'/api/v1/products/{product["id"]}')
    assert public.status_code == 200
    page = await client.get(f'/product/{product["slug"]}')
    assert page.status_code == 200
    assert "noindex" in page.headers["x-robots-tag"]


@pytest.mark.asyncio
async def test_slug_redirect(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    r = await client.patch(f'/api/v1/backoffice/products/{product["id"]}/slug',
        json={"slug": "bosch-disc-new"}, headers=admin_headers)
    assert r.status_code == 200
    old = await client.get(f'/product/{product["slug"]}', follow_redirects=False)
    assert old.status_code == 301
    assert old.headers["location"] == "/product/bosch-disc-new"


@pytest.mark.asyncio
async def test_supplier_offer_price_and_freshness(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    supplier = await client.post("/api/v1/backoffice/suppliers", json={"code": "rossko", "name": "ROSSKO"}, headers=admin_headers)
    assert supplier.status_code == 201
    item = await client.post("/api/v1/backoffice/supplier-items", json={
        "supplier_id": supplier.json()["id"], "product_id": product["id"], "external_key": "real-offer-ref",
        "raw_brand": "BOSCH", "raw_article": "0986479088",
    }, headers=admin_headers)
    assert item.status_code == 201
    assert item.json()["match_status"] == "confirmed"
    policy = await client.post("/api/v1/backoffice/price-policy", json={"markup_percent": "25.00"}, headers=admin_headers)
    assert policy.status_code == 201
    bad_tax = await client.post("/api/v1/backoffice/offer-snapshots", json={
        "supplier_item_id": item.json()["id"], "purchase_price": "2450.00", "tax_status": "unknown",
    }, headers=admin_headers)
    assert bad_tax.status_code == 422
    snapshot = await client.post("/api/v1/backoffice/offer-snapshots", json={
        "supplier_item_id": item.json()["id"], "purchase_price": "2450.00", "tax_status": "included", "quantity": 5,
    }, headers=admin_headers)
    assert snapshot.status_code == 201, snapshot.text
    offers = await client.get(f'/api/v1/products/{product["id"]}/offers')
    assert offers.status_code == 200
    assert len(offers.json()) == 1
    assert offers.json()[0]["retail_price"] == "3062.50"
    assert "purchase_price" not in offers.json()[0]
    assert "tax_status" not in offers.json()[0]
    # An old snapshot is never claimed as fresh when latest by observed_at remains fresh.
    older = await client.post("/api/v1/backoffice/offer-snapshots", json={
        "supplier_item_id": item.json()["id"], "purchase_price": "10.00", "tax_status": "included",
        "quantity": 1, "observed_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    }, headers=admin_headers)
    assert older.status_code == 201
    assert (await client.get(f'/api/v1/products/{product["id"]}/offers')).json()[0]["retail_price"] == "3062.50"


@pytest.mark.asyncio
async def test_supplier_mismatch_cannot_merge(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    supplier = await client.post("/api/v1/backoffice/suppliers", json={"code": "demo", "name": "Demo"}, headers=admin_headers)
    response = await client.post("/api/v1/backoffice/supplier-items", json={
        "supplier_id": supplier.json()["id"], "product_id": product["id"], "external_key": "sku-999",
        "raw_brand": "OTHER", "raw_article": "0986479088",
    }, headers=admin_headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_no_policy_and_zero_stock_do_not_display_retail_price(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    await client.post(f'/api/v1/backoffice/products/{product["id"]}/publish', headers=admin_headers)
    supplier = await client.post("/api/v1/backoffice/suppliers", json={"code": "demo", "name": "Demo"}, headers=admin_headers)
    item = await client.post("/api/v1/backoffice/supplier-items", json={
        "supplier_id": supplier.json()["id"], "product_id": product["id"], "external_key": "demo1",
        "raw_brand": "BOSCH", "raw_article": product["article"],
    }, headers=admin_headers)
    snapshot = {"supplier_item_id": item.json()["id"], "purchase_price": "100.00", "quantity": 2}
    assert (await client.post("/api/v1/backoffice/offer-snapshots", json=snapshot, headers=admin_headers)).status_code == 201
    url = f'/api/v1/products/{product["id"]}/offers'
    assert (await client.get(url)).json()[0]["retail_price"] is None
    await client.post("/api/v1/backoffice/price-policy", json={"markup_percent": "10.00"}, headers=admin_headers)
    assert (await client.get(url)).json()[0]["retail_price"] == "110.00"
    snapshot["quantity"] = 0
    assert (await client.post("/api/v1/backoffice/offer-snapshots", json=snapshot, headers=admin_headers)).status_code == 201
    assert (await client.get(url)).json()[0]["retail_price"] is None


@pytest.mark.asyncio
async def test_old_slug_cannot_be_reused(client, admin_headers):
    _, product = await create_product(client, admin_headers)
    base = f'/api/v1/backoffice/products/{product["id"]}/slug'
    first = await client.patch(base, json={"slug": "bosch-next"}, headers=admin_headers)
    assert first.status_code == 200
    second = await client.patch(base, json={"slug": product["slug"]}, headers=admin_headers)
    assert second.status_code == 409
