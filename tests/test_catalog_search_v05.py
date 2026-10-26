"""Search contract that the browser, JSON API and catalog administrators rely on."""
import pytest


async def add_brand(client, headers, name, slug):
    response = await client.post(
        "/api/v1/backoffice/brands",
        json={"name": name, "slug": slug}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_product(client, headers, brand_id, article, name, publish=True):
    response = await client.post(
        "/api/v1/backoffice/products",
        json={"brand_id": brand_id, "article": article, "name": name}, headers=headers,
    )
    assert response.status_code == 201, response.text
    value = response.json()
    if publish:
        outcome = await client.post(
            f'/api/v1/backoffice/products/{value["id"]}/publish', headers=headers,
        )
        assert outcome.status_code == 200, outcome.text
    return value


@pytest.mark.asyncio
async def test_brand_article_and_mixed_text_queries(client, admin_headers):
    bosch = await add_brand(client, admin_headers, "BOSCH", "bosch")
    mann = await add_brand(client, admin_headers, "MANN FILTER", "mann-filter")
    disc = await add_product(client, admin_headers, bosch['id'], "0 986 479 088", "диск тормозной передний")
    await add_product(client, admin_headers, mann['id'], "0 986 479 088", "Фильтр масляный")
    draft = await add_product(client, admin_headers, bosch['id'], "0 123 456 777", "Диск тестовый", publish=False)

    for query in ("BOSCH", "bosch 0986479088", "Bosch 0 986 479 088", "bosch передний", "тормозной диск"):
        response = await client.get('/api/v1/search', params={'q':query})
        assert response.status_code == 200, response.text
        items = response.json()['items']
        assert disc['id'] in [row['id'] for row in items], query
        assert draft['id'] not in [row['id'] for row in items]

    # SKU is not globally unique and must not silently merge unrelated brands.
    both = (await client.get('/api/v1/search', params={'q':'0986479088'})).json()
    assert both['count'] == 2
    filtered = (await client.get('/api/v1/search', params={'q':'0986479088','brand':'bosch'})).json()
    assert filtered['count'] == 1
    assert filtered['items'][0]['id'] == disc['id']
    assert (await client.get('/api/v1/search', params={'q':'BOSCH','brand':'mann-filter'})).json()['count'] == 0

    html = await client.get('/catalog', params={'q':'bosch 0986479088', 'brand':'bosch'})
    assert html.status_code == 200
    assert 'диск тормозной передний' in html.text
    assert 'Фильтр масляный' not in html.text
    assert 'value="bosch" selected' in html.text


@pytest.mark.asyncio
async def test_search_ranks_exact_sku_before_prefix_and_escapes_wildcards(client, admin_headers):
    brand = await add_brand(client, admin_headers, "DEMO PARTS", "demo-parts")
    prefix = await add_product(client, admin_headers, brand['id'], 'ABC123999', 'Альтернативный фильтр')
    exact = await add_product(client, admin_headers, brand['id'], 'ABC123', 'Точный фильтр')
    response = (await client.get('/api/v1/search', params={'q':'ABC-123'})).json()
    assert [x['id'] for x in response['items']] == [exact['id'], prefix['id']]
    for special in ('%', '_', '\\', 'ABC%'):
        response = await client.get('/api/v1/search', params={'q':special})
        assert response.status_code == 200
        assert response.json()['count'] == 0, special
    response = await client.get('/catalog', params={'q':'<script>alert(1)</script>'})
    assert response.status_code == 200
    assert '&lt;script&gt;' in response.text
    assert '<script>alert(1)</script>' not in response.text


@pytest.mark.asyncio
async def test_catalog_pagination_produces_distinct_pages(client, admin_headers):
    brand = await add_brand(client, admin_headers, "PAGINATION", "pagination")
    for i in range(23):
        await add_product(client, admin_headers, brand['id'], f'PAG-{i:04}', f'Товар {i:04}')
    first = await client.get('/catalog', params={'q':'PAG','page':1})
    second = await client.get('/catalog', params={'q':'PAG','page':2})
    assert first.status_code == second.status_code == 200
    assert first.text.count('class="product"') == 20
    assert second.text.count('class="product"') == 3
    assert 'page=2' in first.text
    assert 'page=1' in second.text
    assert 'Страница 2 из 2' in second.text
    api = (await client.get('/api/v1/search', params={'q':'PAG','limit':20,'offset':20})).json()
    assert api['count'] == 23
    assert len(api['items']) == 3
    assert (await client.get('/catalog', params={'page':0})).status_code == 422
    assert (await client.get('/api/v1/search', params={'brand':'invalid slug!'})).status_code == 422
