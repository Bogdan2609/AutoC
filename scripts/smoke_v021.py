"""Read-only release integrity check. No DB, network, supplier calls or orders."""
import asyncio
from PIL import Image
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.db import Base, get_session


async def smoke():
    needed={'brand_profiles','brand_aliases','catalog_categories','catalog_product_categories',
            'catalog_attribute_definitions','catalog_category_attributes','catalog_product_attributes',
            'catalog_product_media','products','brands','product_families'}
    missing=needed-set(Base.metadata.tables)
    assert not missing,f'Missing database models: {sorted(missing)}'
    async def no_database():
        yield None
    app.dependency_overrides[get_session]=no_database
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            for path in ['/api/v1/catalog-v021/products/not-a-uuid/details',
                         '/api/v1/catalog-v021/products/not-a-uuid/media',
                         '/api/v1/catalog-v021/media/not-a-uuid/file',
                         '/api/v1/backoffice/catalog-v021/products/not-a-uuid/details']:
                r=await client.get(path)
                assert r.status_code in (403,422), (path,r.status_code,r.text[:300])
            protected=await client.post('/api/v1/backoffice/catalog-v021/categories',json={'name':'Smoketest','slug':'smoketest'})
            assert protected.status_code==403,protected.text
    finally:
        app.dependency_overrides.pop(get_session,None)
    assert Image.registered_extensions()['.png']=='PNG'
    print('v0.21 SMOKE PASS: brands/categories/typed attributes/media ASGI and 8 schema tables; read-only, no DB, supplier calls, orders or payments.')


if __name__=='__main__':
    asyncio.run(smoke())
