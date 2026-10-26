"""Read-only production DB smoke: validates F5 schema, immutable guards and UI auth."""
import asyncio
from sqlalchemy import text
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.db import get_session_factory
from app.modules.finance.register_engine import calculate_vat, EXPORT_FORMATS
from decimal import Decimal
from datetime import date

TABLES=('finance_f5_sim_books','finance_f5_sim_documents','finance_f5_sim_rows')
TRIGGERS=('fin_f5_immutable_books','fin_f5_immutable_docs','fin_f5_immutable_rows')
async def main():
    assert app.version=='0.24.4',app.version
    async with get_session_factory()() as s:
        await s.execute(text('SET TRANSACTION READ ONLY'))
        rev=(await s.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        assert rev=='20261020_0016',rev
        product_count=(await s.execute(text('SELECT count(*) FROM products'))).scalar_one()
        for t in TABLES:
            found=(await s.execute(text('SELECT to_regclass(:n)'),{'n':t})).scalar_one()
            assert found==t,(t,found)
        guards=(await s.execute(text('SELECT count(*) FROM pg_trigger WHERE tgname = ANY(:t) AND NOT tgisinternal'),{'t':list(TRIGGERS)})).scalar_one()
        assert guards==len(TRIGGERS),(guards,len(TRIGGERS))
        rows=(await s.execute(text('SELECT count(*) FROM finance_f5_sim_rows'))).scalar_one()
        await s.rollback()
    net,vat=calculate_vat('OWN_SALE',Decimal('1220.00'),'STANDARD','STANDARD_GOODS',date(2026,10,20),'SIM:F5:DOC:SMOKE')
    assert net==Decimal('1000.00') and vat==Decimal('220.00')
    assert len(EXPORT_FORMATS)==5
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as c:
        for path in ('/health/live','/operator','/static/operator.js'):
            response=await c.get(path);assert response.status_code==200,(path,response.status_code)
        assert 'finance-registers' in (await c.get('/operator')).text
        assert 'financeTaxRegistersAction' in (await c.get('/static/operator.js')).text
        # Check each protected endpoint using a method it actually supports.
        # An empty POST body is intentional: it guarantees this smoke test cannot
        # create a book even if authorization is accidentally disabled.
        path='/api/v1/backoffice/finance/tax-registers-sim/overview'
        response=await c.get(path)
        assert response.status_code==403,(path,response.status_code,response.text)
        path='/api/v1/backoffice/finance/tax-registers-sim/books'
        response=await c.post(path,json={})
        assert response.status_code==403,(path,response.status_code,response.text)
    print(f'FINANCE F5 SMOKE PASS | alembic={rev} | products={product_count} | shadow_rows={rows} | tables=3/3 | pg_guards={guards}/3 | UI/auth=PASS')
    print('READ ONLY: no income tax postings, real orders, seller payouts, procurement, banking, F4 or F3 financial records modified.')
if __name__=='__main__':asyncio.run(main())
