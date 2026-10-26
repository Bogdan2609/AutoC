"""Optional targeted corrections for catalog v0.23 R3.

Default --plan is READ ONLY. --apply changes ONLY the bad primary category
of GATES 5631XS and published synthetic QA fixtures (when no active offers).
NGK publication is NEVER changed automatically: it could be intentional.
"""
import argparse
import asyncio
import uuid

from sqlalchemy import select, text
from app.main import app  # load all mapped models; no HTTP calls
from app.core.db import get_session_factory
from app.modules.catalog.models import Brand, Product
from app.modules.catalog_ext.models import Category, ProductCategory
from app.modules.marketplace.models import SellerOffer

QA_ARTICLES={'FIXTUREPLUGUNIT','FIXTUREPLUGBOX4','FIXTUREPLUGASSEMBLED4'}


async def inspect(db):
    gate_brand=await db.scalar(select(Brand).where(Brand.name_norm=='GATES'))
    qa_brand=await db.scalar(select(Brand).where(Brand.name_norm=='CATALOG FIXTURE'))
    ngk_brand=await db.scalar(select(Brand).where(Brand.name_norm=='NGK'))
    belt=await db.scalar(select(Product).where(Product.brand_id==gate_brand.id,
        Product.article_norm=='5631XS')) if gate_brand else None
    belt_cats=(await db.scalars(select(ProductCategory).where(
        ProductCategory.product_id==belt.id))).all() if belt else []
    category=await db.scalar(select(Category).where(Category.slug=='remni-grm'))
    old_category=await db.scalar(select(Category).where(Category.slug=='rem-grp'))
    qa=(await db.scalars(select(Product).where(Product.brand_id==qa_brand.id,
        Product.article_norm.in_(QA_ARTICLES)))).all() if qa_brand else []
    ngk=(await db.scalars(select(Product).where(Product.brand_id==ngk_brand.id,
        Product.article_norm=='BKR6E11'))).first() if ngk_brand else None
    return belt,belt_cats,category,old_category,qa,ngk


async def main():
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--plan',action='store_true')
    group.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    async with get_session_factory()() as db:
        if not args.apply:
            await db.execute(text('SET TRANSACTION READ ONLY'))
        belt,links,new,old,qa,ngk=await inspect(db)
        primary=[r for r in links if r.is_primary]
        print('CATALOG SAFETY R3 | '+('APPLY' if args.apply else 'PLAN / READ ONLY'))
        print('GATES 5631XS:', 'NOT FOUND' if belt is None else
              f'status={belt.status} main_categories={len(primary)} category_links={len(links)}')
        print('CATEGORY remni-grm:',str(new.id) if new else 'NOT FOUND')
        print('QA fixtures:',[(p.article,p.status) for p in qa])
        print('NGK BKR6E-11:',ngk.status if ngk else 'NOT FOUND', '(manual decision only)')
        if not args.apply:
            await db.rollback()
            print('SAFETY PLAN COMPLETE: NO CHANGES. --apply must be separately approved.')
            return
        revision=(await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
        if revision!='20261020_0010':raise RuntimeError('Upgrade database to v0.23 R3 first')
        if belt and not primary:
            if not new or not old or not new.active:
                raise RuntimeError('Expected active remni-grm and old rem-grp categories are missing')
            if len(links)!=1 or links[0].category_id!=old.id or links[0].is_primary:
                raise RuntimeError('GATES classification has changed; refuse blind correction')
            links[0].category_id=new.id
            links[0].is_primary=True
            print('FIX: 5631XS now uses main category remni-grm; Product UUID retained')
        elif belt and len(primary)!=1:
            raise RuntimeError('Unexpected category configuration for GATES 5631XS')
        for p in qa:
            if p.status!='published':continue
            offers=(await db.scalars(select(SellerOffer).where(SellerOffer.product_id==p.id))).all()
            if any(x.status=='active' for x in offers):
                raise RuntimeError(f'QA {p.article} has active offers; do not unpublish automatically')
            p.status='draft'
            print('FIX: unpublish synthetic QA product',p.article)
        await db.commit()
        print('SAFETY APPLY COMMIT: NO real offers/stock/orders/supplier history changed; NGK unchanged')


if __name__=='__main__':
    asyncio.run(main())
