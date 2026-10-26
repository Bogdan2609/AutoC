"""Regression checks for unified catalog, quote pricing and fulfillment."""
from decimal import Decimal
from types import SimpleNamespace
import pytest
from app.modules.marketplace.offer_rank import fulfillment_rank
from app.modules.pricing.resolver import resolve_supplier_quote
from app.modules.supplier_feeds.pricing import quote_unit


def test_route_order_handles_unknown_delivery_days():
    rows=[{'days':None,'fee':'0.00'}, {'days':2,'fee':'20.00'},
          {'days':0,'fee':'5.00'}, {'days':2,'fee':'2.00'}]
    assert sorted(rows,key=fulfillment_rank)==[rows[2],rows[3],rows[1],rows[0]]


def test_route_order_rejects_negative_fee_and_days():
    with pytest.raises(ValueError):fulfillment_rank({'days':-1,'fee':'0'})
    with pytest.raises(ValueError):fulfillment_rank({'days':None,'fee':'NaN'})


def test_supplier_pricing_fail_closed_and_explicit_legacy():
    assert resolve_supplier_quote(Decimal('100')) is None
    policy=SimpleNamespace(markup_percent=Decimal('20'),version=7)
    r=resolve_supplier_quote(Decimal('100'),legacy_policy=policy)
    assert r['price']=='120.00'
    assert r['pricing_basis']=='legacy_markup_unverified_costs'
    assert r['payment_enabled'] is False
    assert resolve_supplier_quote(Decimal('100'),commercial_rule=SimpleNamespace(
        tax_status_verified='included'),warehouse_map_approved=True) is None


@pytest.mark.asyncio
async def test_legacy_marketplace_category_routes_use_canonical_tables(client,admin_headers):
    from app.core.db import get_session
    from app.main import app
    from app.modules.catalog_ext.models import Category as CanonicalCategory, ProductCategory as CanonicalProductCategory
    from app.modules.marketplace.models import Category as LegacyCategory
    from sqlalchemy import select
    headers=admin_headers
    root=await client.post('/api/v1/admin/marketplace/categories',headers=headers,
        json={'name':'Safety unified','slug':'safety-unified-v0317'})
    assert root.status_code==201,root.text
    from uuid import UUID
    category_id=UUID(root.json()['id'])
    assert root.json()['canonical'] is True
    async for db in app.dependency_overrides[get_session]():
        assert await db.get(CanonicalCategory,category_id)
        assert await db.get(LegacyCategory,category_id) is None


@pytest.mark.asyncio
async def test_legacy_relation_writes_blocked(client,admin_headers):
    # No ambiguous relation is silently converted into a directed supersession.
    from uuid import uuid4
    res=await client.post('/api/v1/admin/marketplace/product-relations',headers=admin_headers,
        json={'product_id':str(uuid4()),'related_product_id':str(uuid4()),'relation':'replacement'})
    assert res.status_code in (409,410)
