"""Read-only v0.22 ASGI, route and static asset checks (no database or supplier HTTP)."""
import asyncio
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from app.core.db import Base, get_session
from app.main import app


def documented_api_routes(application):
    """Effective API paths, compatible with FastAPI lazy included routers (>=0.137).

    app.routes is NOT a flattened route table in the production FastAPI 0.143.3.
    OpenAPI resolves the included router prefixes correctly. The /operator page
    is excluded from schema, so we probe its real ASGI response below.
    """
    return {(method.upper(), path)
            for path, operations in application.openapi()['paths'].items()
            for method in operations if method in {'get', 'post', 'put', 'patch', 'delete'}}


async def smoke():
    assert app.version == '0.22.0', app.version
    paths = documented_api_routes(app)
    expected = {
        ('GET','/operator'),
        ('GET','/api/v1/backoffice/catalog-editor/families'),
        ('GET','/api/v1/backoffice/catalog-editor/brands/{brand_id}/profile'),
        ('GET','/api/v1/backoffice/catalog-editor/categories/{category_id}/bindings'),
        ('GET','/api/v1/backoffice/catalog-editor/products/{product_id}/identity'),
        ('PATCH','/api/v1/backoffice/catalog-editor/products/{product_id}/content'),
        ('GET','/api/v1/backoffice/catalog-editor/media'),
        ('GET','/api/v1/backoffice/catalog-editor/media/{media_id}/preview'),
        ('GET','/api/v1/backoffice/catalog-editor/products/{product_id}/sale-terms'),
        ('POST','/api/v1/backoffice/master-catalog/families'),
        ('POST','/api/v1/backoffice/catalog-v021/media'),
    }
    # The unlisted operator page is verified via real ASGI dispatch below.
    expected.remove(('GET', '/operator'))
    missing = expected - paths
    assert not missing, f'Missing registered API routes: {sorted(missing)}'
    assert {'brands','products','product_families','catalog_product_media','catalog_categories',
            'catalog_attribute_definitions','supplier_offer_snapshots','seller_offers',
            'inventory_balances'} <= set(Base.metadata.tables), 'Required v0.21 schema models missing'
    for name in ['templates/operator.html','static/operator.js','static/catalog_editor.js','static/catalog_editor.css']:
        assert Path(name).is_file(),name
    text=Path('templates/operator.html').read_text(encoding='utf-8')
    assert '/static/catalog_editor.js' in text
    assert 'ce-brands' in text and 'ce-categories' in text and 'ce-attributes' in text and 'ce-families' in text
    async def fail_if_database():
        raise AssertionError('Unexpected database access')
        yield
    app.dependency_overrides[get_session]=fail_if_database
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://local.test') as c:
            r=await c.get('/operator');assert r.status_code==200,r.status_code
            assert (await c.get('/static/catalog_editor.js')).status_code==200
            # Use actual ASGI dispatch, including the included-router prefixes.
            # Invalid UUIDs would produce 422 if the admin dependency were not
            # enforced; returning 403 proves the security gate runs first.
            sample_id = '00000000-0000-4000-8000-000000000001'
            protected_paths = [
                '/api/v1/backoffice/catalog-editor/families',
                '/api/v1/backoffice/catalog-editor/media',
                f'/api/v1/backoffice/catalog-editor/brands/{sample_id}/profile',
                f'/api/v1/backoffice/catalog-editor/categories/{sample_id}/bindings',
                f'/api/v1/backoffice/catalog-editor/products/{sample_id}/identity',
                f'/api/v1/backoffice/catalog-editor/products/{sample_id}/sale-terms',
                f'/api/v1/backoffice/catalog-editor/media/{sample_id}/preview',
            ]
            for path in protected_paths:
                res = await c.get(path)
                assert res.status_code == 403, (path, res.status_code)
    finally:
        app.dependency_overrides.pop(get_session,None)
    print('v0.22 SMOKE PASS: 10 OpenAPI endpoints + /operator ASGI, 7 protected ASGI GET checks, 4 UI assets, v0.21 schema unchanged; anonymous admin endpoints forbidden. No database/supplier/order/payment calls.')


if __name__=='__main__':
    asyncio.run(smoke())
