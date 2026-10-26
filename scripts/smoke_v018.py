"""Zero-write / zero-supplier-network ASGI structural smoke for Master Catalog v0.18."""
import asyncio
import httpx
from app.main import app
from app.core.db import Base, get_session
from app.modules.master_catalog.models import ProductFamily, FamilyMember, ProductComposition, ComponentLine, FactoryPackage, OfferSaleTerms

async def _no_db():
    yield None

async def check():
    old=app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session]=_no_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://smoke.local',timeout=4) as c:
            for method,path,payload,expected in [
                ('POST','/api/v1/backoffice/master-catalog/families',{'name':'x'},403),
                ('PUT','/api/v1/backoffice/master-catalog/products/00000000-0000-4000-8000-000000000001/composition',{},403),
                ('GET','/api/v1/master-catalog/products/not-a-uuid/links',None,422),
                ('POST','/api/v1/marketplace/cart/quotes',{},401),
                ('POST','/api/v1/marketplace/products/not-a-uuid/offers/preview',{},422),
            ]:
                r=await c.request(method,path,json=payload)
                if r.status_code!=expected:
                    raise AssertionError(f'{method} {path}: expected {expected}, got {r.status_code}: {r.text[:300]}')
    finally:
        if old is None:app.dependency_overrides.pop(get_session,None)
        else:app.dependency_overrides[get_session]=old

assert app.version in ('0.18.0','0.21.0','0.22.0')
assert len({'product_families','product_family_members','product_compositions','product_component_lines','factory_packages','offer_sale_terms'}.intersection(Base.metadata.tables))==6
assert ProductFamily.__table__.name=='product_families'
assert OfferSaleTerms.__table__.name=='offer_sale_terms'
asyncio.run(check())
print('v0.18 SMOKE PASS: ASGI catalog/admin/cart routes + six schema tables; no DB, supplier API, orders or payments.')
