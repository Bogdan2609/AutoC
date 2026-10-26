"""Offline API acceptance: no provider access or database mutations."""
from uuid import uuid4
import pytest
from fastapi.routing import iter_route_contexts


def test_readback_route_is_registered_under_protected_admin_router():
    from app.main import app
    from app.core.security import require_admin
    path='/api/v1/admin/marketplace/supplier-accounts/{account_id}/order-readback'
    found=[r for r in iter_route_contexts(app.routes)
           if r.path==path and 'POST' in (r.methods or set())]
    assert len(found)==1
    def protected(node):
        return node.call is require_admin or any(protected(x) for x in node.dependencies)
    assert protected(found[0].dependant)


@pytest.mark.asyncio
async def test_live_supplier_readback_is_disabled_by_default_in_tests(client,admin_headers):
    ref=str(uuid4())
    response=await client.post(
        f'/api/v1/admin/marketplace/supplier-accounts/{ref}/order-readback',
        headers=admin_headers,
        json={'destination_id':str(uuid4()),'external_reference':'123456'})
    assert response.status_code==403, response.text
    assert 'LIVE' not in response.text
