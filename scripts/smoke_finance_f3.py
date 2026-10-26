"""Read-only F3 deployment verification; no test journal is created."""
import asyncio
from sqlalchemy import text
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.db import get_session_factory
from app.modules.finance.ledger_engine import canonical_lines, signed_balance
from decimal import Decimal

async def run():
    assert app.version=='0.24.2',app.version
    async with get_session_factory()() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        rev=(await session.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert rev=='20261020_0014',rev
        n=(await session.execute(text('SELECT count(*) FROM products'))).scalar_one()
        books=(await session.execute(text('SELECT count(*) FROM finance_ledger_books'))).scalar_one()
        entries=(await session.execute(text('SELECT count(*) FROM finance_ledger_entries'))).scalar_one()
        guards=(await session.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('fin_f3_valid_entry','fin_f3_valid_line','fin_f3_no_change_entry','fin_f3_no_change_line') AND NOT tgisinternal"))).scalar_one()
        assert guards==4,f'F3 PostgreSQL journal guards missing: {guards}/4'
        await session.rollback()
    assert signed_balance('LIABILITY',Decimal('200'),Decimal('2000'))==Decimal('1800')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
        for path in ('/health/live','/operator','/static/operator.js'):
            r=await c.get(path);assert r.status_code==200,(path,r.status_code)
        assert 'Finance F3' in (await c.get('/operator')).text
        assert 'finance-ledger' in (await c.get('/static/operator.js')).text
        for url in ('/api/v1/backoffice/finance/ledger/overview','/api/v1/backoffice/finance/ledger/books/00000000-0000-0000-0000-000000000001/trial-balance'):
            r=await c.get(url);assert r.status_code==403,(url,r.status_code)
    print(f'FINANCE F3 SMOKE PASS | alembic={rev} | products={n} | shadow_books={books} | journal_entries={entries} | pg_guards={guards}/4 | UI/auth=PASS')
    print('READ ONLY: no real offers, orders, payments, supplier requests, purchases or settlements changed.')

if __name__=='__main__':asyncio.run(run())
