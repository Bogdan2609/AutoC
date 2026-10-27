"""v0.42 read-only guardrails; no external suppliers or payment providers."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.modules.marketplace.commercial_guardrails import MAX_CART_LINES, checked_money, receiving_uuid
from app.modules.marketplace.quote_preview import quote_breakdown
from app.modules.marketplace.data_integrity import stale_snapshot_candidates
from app.modules.suppliers.models import OfferSnapshot


def option(**fields):
    return dict({'stock':3, 'fee':'10.00', 'supplier_fee_known':False,
                 'pickup_node_id':'pvz', 'receiving_node_id':str(uuid4()),
                 'receiving_node':'Receiving name', 'route_node_ids':[]}, **fields)


def variant(**fields):
    return dict({'source_kind':'supplier_quote', 'price':'100.00',
                 'min_qty':1,'pack_multiple':1}, **fields)


def test_receiving_node_is_uuid_not_human_name():
    uid=str(uuid4())
    result=quote_breakdown(variant(),2,option(receiving_node_id=uid))
    assert result['receiving_node_id']==uid
    assert receiving_uuid(result)==uid
    with pytest.raises(HTTPException) as error:
        receiving_uuid({'receiving_node_id':'Our Warehouse'})
    assert error.value.status_code==409


@pytest.mark.parametrize('value', [None,True,False,1.2,'NaN','Infinity','-0.10','1.001'])
def test_invalid_money_fails_closed(value):
    with pytest.raises(HTTPException) as error:
        checked_money(value)
    assert error.value.status_code==409


def test_no_payable_total_without_explicit_supplier_fee():
    result=quote_breakdown(variant(),2,option(supplier_fee_known=True))
    assert result['grand_total'] is None
    assert not result['final_price_complete']


def test_explicit_supplier_fee_is_added_to_known_goods_and_merchant_shipping():
    result=quote_breakdown(variant(),2,option(supplier_fee_known=True,supplier_delivery_fee='15.00'))
    assert result['known_components_total']=='225.00'
    assert result['grand_total']=='225.00'
    assert result['supplier_delivery_fee']=='15.00'
    assert not result['payment_enabled']


def test_bounded_cart_configuration():
    assert MAX_CART_LINES==40


def test_offer_retention_query_never_selects_newest_without_successor():
    from sqlalchemy.dialects import postgresql
    sql=str(stale_snapshot_candidates(older_than=datetime.now(timezone.utc)-timedelta(days=180)).compile(dialect=postgresql.dialect()))
    for table in ('order_lines','cart_lines','supplier_purchase_attempts','supplier_offer_snapshots'):
        assert table in sql
    assert 'EXISTS' in sql and 'NOT' in sql


@pytest.mark.asyncio
async def test_data_integrity_api_requires_admin(client):
    for endpoint in ('/api/v1/admin/marketplace/data-integrity/offer-history',
                     '/api/v1/admin/marketplace/data-integrity/legacy-reservations'):
        result=await client.get(endpoint)
        assert result.status_code==403


@pytest.mark.asyncio
async def test_data_integrity_reports_do_not_write(client, admin_headers):
    for endpoint in ('/api/v1/admin/marketplace/data-integrity/offer-history',
                     '/api/v1/admin/marketplace/data-integrity/legacy-reservations'):
        r=await client.get(endpoint,headers=admin_headers)
        assert r.status_code==200, r.text
        assert r.json()['read_only'] is True


def test_zip_actual_bytes_are_bounded():
    import io
    from app.modules.supplier_feeds.parsers import BoundedReader, FeedFormatError, bounded_copy
    reader=BoundedReader(io.BytesIO(b'12345'),4)
    assert reader.read(3)==b'123'
    with pytest.raises(FeedFormatError):reader.read(1000)
    target=io.BytesIO()
    with pytest.raises(FeedFormatError):bounded_copy(io.BytesIO(b'x'*200),target,limit=32)
    assert len(target.getvalue())<=32
