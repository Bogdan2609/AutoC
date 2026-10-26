"""Read-only production smoke report. No supplier HTTP, purchases or payments."""
import asyncio
import json
from sqlalchemy import select, func, text
from app.core.db import get_session_factory
from app.modules.marketplace.models import Merchant, Store, SupplierAccount, SupplierDestination, SellerOffer
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.suppliers.models import OfferSnapshot
from app.modules.commerce.models import CartLine


async def main():
    async with get_session_factory()() as db:
        version=await db.scalar(text('SELECT version_num FROM alembic_version'))
        counts={}
        for name,model in [
            ('merchants', Merchant),('stores',Store),('supplier_accounts',SupplierAccount),
            ('destinations',SupplierDestination),('seller_offers',SellerOffer),
            ('supplier_quotes',OfferSnapshot),('cart_lines',CartLine),
            ('supplier_purchase_attempts',SupplierPurchaseAttempt)]:
            counts[name]=int(await db.scalar(select(func.count()).select_from(model)) or 0)
    result={'schema':version,'counts':counts,'supplier_orders_sent_by_smoke':0}
    assert version=='20261019_0005','Schema not upgraded to v0.14'
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    asyncio.run(main())
