"""Report catalog publication readiness; never change legacy test catalog rows.

Usage: python -m scripts.catalog_production_audit_r7 --plan
"""
import argparse
import asyncio
from collections import Counter
from sqlalchemy import select, text
from app.core.config import get_settings
from app.core.db import get_session_factory
from app.modules.catalog.models import Product
from app.modules.catalog.publication_policy import publication_issues

async def audit():
    async with get_session_factory()() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        rows=(await session.scalars(select(Product).where(Product.status=='published')
                                    .order_by(Product.id).limit(10001))).all()
        scanned=rows[:10000]
        counts=Counter();not_ready=0
        for row in scanned:
            issues=await publication_issues(session,row)
            errors={i['code'] for i in issues if i['severity']=='error'}
            if errors:not_ready+=1
            counts.update(errors)
        await session.rollback()
    print(f'R7 READ-ONLY CATALOG AUDIT | env={get_settings().app_env} | published_scanned={len(scanned)} | legacy_not_ready={not_ready}')
    for code,n in sorted(counts.items()):print(f'  {code}: {n}')
    if len(rows)>10000:print('WARNING: results truncated at 10000 published products')
    print('Legacy test data remains untouched; errors block NEW publication but do not retroactively unpublish.')
    print('NO CHANGES, no supplier calls, no order/purchase/payment operations.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true',required=True,help='Read-only report')
    parser.parse_args()
    asyncio.run(audit())

if __name__=='__main__':main()
