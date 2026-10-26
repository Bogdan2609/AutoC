"""Sale-lot guards. Counts/prices always use BASE sellable Product units.

An outer factory package has its own Product UUID and one outer package is one
sellable unit. Seller min/multiples NEVER change stock or reserve suppliers.
"""
from math import lcm
from fastapi import HTTPException
from sqlalchemy import select
from .models import OfferSaleTerms


def conflict(row,quantity):
    if quantity<row.minimum_units or quantity % row.step_units:
        raise HTTPException(409,f'Sale terms: minimum {row.minimum_units}, quantity step {row.step_units} base units')


async def enforce_seller(db,offer_id,quantity):
    row=await db.scalar(select(OfferSaleTerms).where(
        OfferSaleTerms.seller_offer_id==offer_id,OfferSaleTerms.status=='active'))
    if row:conflict(row,quantity)
    return row


async def supplier_constraints(db,supplier_item_id,account_id,destination_id,warehouse,feed_min,feed_step):
    row=await db.scalar(select(OfferSaleTerms).where(
        OfferSaleTerms.supplier_item_id==supplier_item_id,
        OfferSaleTerms.supplier_account_id==account_id,
        OfferSaleTerms.supplier_destination_id==destination_id,
        OfferSaleTerms.warehouse_id==warehouse,
        OfferSaleTerms.status=='active'))
    minimum=max(1,int(feed_min or 1)); step=max(1,int(feed_step or 1));per_package=1
    if row:
        minimum=max(minimum,row.minimum_units)
        step=lcm(step,row.step_units)
        per_package=row.units_per_package
    return {'minimum_units':minimum,'step_units':step,'units_per_package':per_package}
