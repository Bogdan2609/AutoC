"""v0.16 ASGI no-network/no-DB smoke; works with FastAPI lazy include_router."""
import asyncio
from pathlib import Path
from decimal import Decimal

import httpx
from app.main import app
from app.core.db import get_session
from app.modules.marketplace.quote_preview import quote_breakdown

async def _no_db():
    yield None

async def check():
    old=app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session]=_no_db
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://smoke.local',timeout=4) as client:
            paths=[('GET','/api/v1/marketplace/products/not-a-uuid/offers/grouped',None),
                   ('POST','/api/v1/marketplace/products/not-a-uuid/offers/preview',{
                        'snapshot_id':'00000000-0000-4000-8000-000000000001',
                        'supplier_account_id':'00000000-0000-4000-8000-000000000001',
                        'supplier_destination_id':'00000000-0000-4000-8000-000000000001',
                        'quantity':1})]
            for method,path,payload in paths:
                result=await client.request(method,path,json=payload)
                if result.status_code != 422:
                    raise AssertionError(f'{method} {path}: {result.status_code}')
                detail=result.json().get('detail',[])
                if not any(tuple(d.get('loc',()))==('path','product_id') for d in detail):
                    raise AssertionError(f'Missing typed product_id validation for {path}')
    finally:
        if old is None: app.dependency_overrides.pop(get_session,None)
        else: app.dependency_overrides[get_session]=old

q=quote_breakdown({'source_kind':'supplier_quote','price':'590.72'},2,
    {'stock':5,'fee':'135.00','supplier_fee_known':False,'pickup_node_id':'pvz'})
assert q['known_components_total']=='1316.44' and q['grand_total'] is None
assert q['cart_enabled'] is False and q['payment_enabled'] is False
for filename in ('static/offer_preview.js','static/operator.js','templates/product.html'):
    assert Path(filename).is_file(),f'Missing {filename}'
assert 'quote-preview-form' in Path('templates/product.html').read_text('utf-8')
assert "data-action=\"edit-route\"" not in Path('templates/product.html').read_text('utf-8')
asyncio.run(check())
print('v0.16 SMOKE PASS: ASGI quote preview and grouping; no DB writes, supplier calls, cart, payment or purchase.')
