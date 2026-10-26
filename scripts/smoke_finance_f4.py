"""Production DB read-only smoke for F4; no fake payment is written."""
import asyncio
from sqlalchemy import text
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.db import get_session_factory
from app.modules.finance.payment_sim_engine import next_payment_status, reconcile
from decimal import Decimal

TABLES = ('finance_f4_sim_payments','finance_f4_sim_events','finance_f4_sim_refunds',
          'finance_f4_sim_receipt_plans','finance_f4_sim_bank_lines','finance_f4_sim_bank_matches')
TRIGGERS = ('fin_f4_guard_payment','fin_f4_guard_refund',
            'fin_f4_immutable_events','fin_f4_immutable_receipt_plans',
            'fin_f4_immutable_bank_lines','fin_f4_immutable_bank_matches')

async def main():
    assert app.version=='0.24.3',app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION READ ONLY'))
        revision=(await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert revision=='20261020_0015',revision
        products=(await s.execute(text('SELECT count(*) FROM products'))).scalar_one()
        for t in TABLES:
            assert (await s.execute(text('SELECT to_regclass(:name)'),{'name':t})).scalar_one()==t,t
        guards=(await s.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:names) AND NOT tgisinternal"),{'names':list(TRIGGERS)})).scalar_one()
        assert guards==len(TRIGGERS),(guards,len(TRIGGERS))
        payments=(await s.execute(text('SELECT count(*) FROM finance_f4_sim_payments'))).scalar_one()
        await s.rollback()
    assert next_payment_status('CREATED','SUCCEEDED') and not next_payment_status('SUCCEEDED','CANCELED')
    assert reconcile('OUT',Decimal('700'),Decimal('700'),'REFUND')=='MATCH'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as c:
        for path in ('/health/live','/operator','/static/operator.js'):
            r=await c.get(path);assert r.status_code==200,(path,r.status_code)
        assert 'finance-payments' in (await c.get('/operator')).text
        assert 'financePaymentsAction' in (await c.get('/static/operator.js')).text
        for path in ('/api/v1/backoffice/finance/payments-sim/overview','/api/v1/backoffice/finance/payments-sim/bank-lines'):
            r=await c.get(path);assert r.status_code==403,(path,r.status_code)
    print(f'FINANCE F4 SMOKE PASS | alembic={revision} | products={products} | sim_payments={payments} | tables={len(TABLES)}/6 | pg_guards={guards}/6 | UI/auth=PASS')
    print('READ ONLY: no bank API, OFD, live payments/refunds, procurement, orders, F3 ledger entries or settlements changed.')

if __name__=='__main__':asyncio.run(main())
