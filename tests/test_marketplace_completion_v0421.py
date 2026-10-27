"""v0.42.1 safe contract regressions. No real accounts, orders or networks."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.modules.marketplace.price_integrity_v0421 import compare_accepted_price
from app.modules.marketplace.quote_cache_v0421 import preview_scope, read_quoted_marketplace
from app.modules.marketplace.legacy_reservations_v0421 import classify_reserve, release_key
from app.modules.marketplace.snapshot_retention_v0421 import (
    cutoff_for_days, candidate_ids, retention_batch,
)


def test_price_drift_exact_kopecks_and_positive_required():
    ok=compare_accepted_price('590.72','590.72','135.00','135.00')
    assert ok['changed'] is False and ok['unit_delta']=='0.00'
    changed=compare_accepted_price('590.72','590.73','135.00','132.00')
    assert changed == {'changed': True, 'reason':'commercial_terms_changed',
                        'unit_delta':'0.01', 'route_delta':'-3.00'}
    unknown=compare_accepted_price(None,'590.72',None,'0.00')
    assert unknown['changed'] and unknown['unit_delta'] is None
    with pytest.raises(HTTPException):
        compare_accepted_price('590.72','NaN','135.00','135.00')


@pytest.mark.asyncio
async def test_preview_cache_per_evaluation_scope_only():
    counter=0
    async def query():
        nonlocal counter
        counter+=1
        return {'offers':[counter]}
    p=uuid4();node=uuid4()
    with preview_scope():
        assert await read_quoted_marketplace(p,node,query) == {'offers':[1]}
        assert await read_quoted_marketplace(p,node,query) == {'offers':[1]}
        assert await read_quoted_marketplace(p,uuid4(),query) == {'offers':[2]}
    assert await read_quoted_marketplace(p,node,query) == {'offers':[3]}
    with preview_scope():
        assert await read_quoted_marketplace(p,node,query) == {'offers':[4]}


@pytest.mark.asyncio
async def test_preview_cache_is_task_isolated():
    calls=[]
    async def each(tag):
        async def query():
            calls.append(tag)
            await asyncio.sleep(0)
            return tag
        with preview_scope():
            await read_quoted_marketplace('p','n',query)
            return await read_quoted_marketplace('p','n',query)
    assert await asyncio.gather(each('alice'),each('bob')) == ['alice','bob']
    assert sorted(calls) == ['alice','bob']


def test_legacy_release_evidence():
    order=uuid4();line=uuid4();balance=uuid4()
    original=SimpleNamespace(idempotency_key='original-123',balance_id=balance,
                             delta_reserved=2,reason='reserve')
    stock=SimpleNamespace(reserved=5)
    base=dict(source_kind='own_stock',reservation_key='original-123',
              balance_id=balance,quantity=2,reservation=original,
              release=None,balance=stock,matching_line_count=1)
    assert classify_reserve(**base)=='unreleased_requires_review'
    assert classify_reserve(**{**base,'matching_line_count':2})=='manual_review_identity_ambiguous'
    assert classify_reserve(**{**base,'balance':SimpleNamespace(reserved=1)})=='manual_review_balance_shortfall'
    assert classify_reserve(**{**base,'reservation':None})=='manual_review_original_evidence_invalid'
    done=SimpleNamespace(balance_id=balance,delta_reserved=-2,reason='v017_request_expired')
    assert classify_reserve(**{**base,'release':done})=='released'
    assert release_key(order,line)==f'v017-release:{order}:{line}'


def test_snapshot_prune_query_excludes_referenced_and_newest():
    before=datetime.now(timezone.utc)-timedelta(days=180)
    sql=str(candidate_ids(before,50,lock=True).compile(dialect=postgresql.dialect()))
    assert 'supplier_offer_snapshots' in sql
    assert 'order_lines' in sql and 'cart_lines' in sql and 'supplier_purchase_attempts' in sql
    assert 'FOR UPDATE' in sql and 'SKIP LOCKED' in sql
    for days in (1, 90, 179, 3651, True):
        with pytest.raises(ValueError):cutoff_for_days(days)


@pytest.mark.asyncio
async def test_retention_never_deletes_without_explicit_approval():
    class Db:
        async def scalars(self,query):
            class Result:
                def all(self):return [uuid4()]
            return Result()
    result=await retention_batch(Db(),days=180,limit=1)
    assert result['dry_run'] and result['deleted']==0
    with pytest.raises(PermissionError):
        await retention_batch(Db(),days=180,limit=1,apply=True,approved=False)


@pytest.mark.asyncio
async def test_batch_stock_preview_is_read_only_and_uses_shared_queries(monkeypatch):
    from app.modules.marketplace import cart_batch_v0421 as mod
    from app.modules.marketplace.models import SellerOffer, Store, Merchant, UsedPart, InventoryBalance
    from app.modules.master_catalog.models import OfferSaleTerms
    offer_id,store_id,merchant_id,node_id,balance_id = (uuid4() for _ in range(5))
    offer=SimpleNamespace(id=offer_id,store_id=store_id,merchant_id=merchant_id,
        used_part_id=None,status='active',source_kind='own_stock',price=Decimal('20.00'))
    store=SimpleNamespace(id=store_id,merchant_id=merchant_id,status='active')
    merchant=SimpleNamespace(id=merchant_id,status='active')
    balance=SimpleNamespace(id=balance_id,seller_offer_id=offer_id,node_id=node_id,on_hand=10,reserved=2)
    node=SimpleNamespace(id=node_id,merchant_id=merchant_id,active=True)
    line1=SimpleNamespace(id=uuid4(),seller_offer_id=offer_id,quantity=2,fulfillment_node_id=node_id)
    line2=SimpleNamespace(id=uuid4(),seller_offer_id=offer_id,quantity=7,fulfillment_node_id=node_id)
    counts=[]
    class Result:
        def __init__(self,data):self.data=data
        def all(self):return self.data
    class Db:
        async def scalars(self,statement):
            model=statement.column_descriptions[0]['entity']
            counts.append(model.__name__)
            return Result({SellerOffer:[offer],Store:[store],Merchant:[merchant],UsedPart:[],
                           OfferSaleTerms:[]}[model])
        async def execute(self,statement):
            counts.append('inventory_join')
            return Result([(balance,node)])
    async def paths(_db, merchant, source, pickup):
        return [{'days':0,'fee':'0.00','route_node_ids':[str(node_id)],'pickup_node_id':str(node_id)}]
    monkeypatch.setattr(mod,'routes_to_pickup',paths)
    result=await mod.own_stock_preview_batch(Db(),[line1,line2])
    assert result[line1.id]['in_stock']
    assert result[line2.id]['in_stock']
    assert len(counts)==5,counts   # not multiplied by number of cart lines
