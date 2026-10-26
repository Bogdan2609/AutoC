"""Analytics A3: strictly read-only supplier/product/seller economics and rounding simulation.

These are historical procurement quotes and unverified final order-line face values,
NOT real-time supplier availability, reconciled revenue, tax or certified profits.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.analytics.a2 import csv_response, rub, xlsx_response
from app.modules.catalog.models import Brand, Product
from app.modules.commerce.models import Order, OrderLine
from app.modules.finance.pricing_engine import customer_price_rounding
from app.modules.marketplace.models import Merchant
from app.modules.suppliers.models import OfferSnapshot, Supplier, SupplierItem

router = APIRouter(prefix='/api/v1/backoffice/analytics/a3',
                   tags=['analytics-a3-readonly'], dependencies=[Depends(require_admin)])


def utc_start(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


async def quote_comparison(db: AsyncSession, days: int, *, article: str | None = None,
                           supplier_code: str | None = None, limit: int = 30,
                           now: datetime | None = None) -> dict:
    """Aggregate recorded prices in a window, never present historical minima as live offers.

    An external supplier can have multiple accounts/warehouses for the same SKU.
    Keep all such snapshots in the range and show MIN/MAX and observation time,
    rather than guessing which warehouse is currently procurable.
    """
    now = now or datetime.now(timezone.utc)
    stmt = (select(Product.id, Product.name, Product.article, Brand.name, Supplier.code,
                   OfferSnapshot.currency, func.min(OfferSnapshot.purchase_price),
                   func.max(OfferSnapshot.purchase_price), func.count(OfferSnapshot.id),
                   func.max(OfferSnapshot.observed_at),
                   func.min(OfferSnapshot.lead_time_days),
                   func.max(OfferSnapshot.lead_time_days))
            .select_from(OfferSnapshot)
            .join(SupplierItem, SupplierItem.id == OfferSnapshot.supplier_item_id)
            .join(Supplier, Supplier.id == SupplierItem.supplier_id)
            .join(Product, Product.id == SupplierItem.product_id)
            .join(Brand, Brand.id == Product.brand_id)
            .where(SupplierItem.match_status == 'confirmed',
                   OfferSnapshot.observed_at >= utc_start(days, now),
                   OfferSnapshot.observed_at <= now,
                   OfferSnapshot.currency == 'RUB'))
    if article:
        stmt = stmt.where(Product.article.ilike('%' + article.replace('%', '\\%').replace('_', '\\_') + '%', escape='\\'))
    if supplier_code:
        stmt = stmt.where(Supplier.code == supplier_code)
    stmt = (stmt.group_by(Product.id, Product.name, Product.article, Brand.name,
                          Supplier.code, OfferSnapshot.currency)
            .order_by(Product.article, Supplier.code).limit(1000))
    records = (await db.execute(stmt)).all()
    grouped: dict[str, dict] = {}
    for pid, name, sku, brand, code, currency, low, high, n, observed, lead_min, lead_max in records:
        key = str(pid)
        rec = grouped.setdefault(key, {'product_id': key, 'article': sku, 'name': name,
                                       'brand': brand, 'suppliers': []})
        rec['suppliers'].append({'supplier': code, 'currency': currency,
                                 'historical_min_purchase_rub': rub(Decimal(low)),
                                 'historical_max_purchase_rub': rub(Decimal(high)),
                                 'snapshots': int(n),
                                 'last_observed_at': observed.isoformat() if observed else None,
                                 'min_lead_time_days': int(lead_min) if lead_min is not None else None,
                                 'max_lead_time_days': int(lead_max) if lead_max is not None else None})
    result = list(grouped.values())
    result.sort(key=lambda v: (-len(v['suppliers']), str(v['brand']).casefold(), str(v['article'])))
    for entry in result:
        entry['supplier_count'] = len(entry['suppliers'])
        entry['comparable'] = len(entry['suppliers']) >= 2
        lows = [Decimal(q['historical_min_purchase_rub']) for q in entry['suppliers']]
        entry['historical_low_rub'] = rub(min(lows))
        entry['historical_high_rub'] = rub(max(lows))
        entry['historical_range_rub'] = rub(max(lows) - min(lows))
    return {'source': 'HISTORICAL_SUPPLIER_SNAPSHOTS_ONLY', 'days': days, 'article': article,
            'rows': result[:limit], 'total_groups_scanned': len(result),
            'truncated_query': len(records) == 1000,
            'verified_current_stock': None, 'verified_procurement_price': None,
            'description': 'Each price is a historical observed range across accounts and warehouses. Not a live quote, availability, fulfillment promise, or tax-deductible cost.'}


async def merchant_economics(db: AsyncSession, days: int, *, merchant_id: UUID | None = None,
                             now: datetime | None = None) -> dict:
    """Separate operational quote value from platform vs third-party income.

    Unattributed order lines are not assumed to belong to the owner. Merchant
    fees are NOT inferred from percentage settings or from external shop revenue.
    """
    now = now or datetime.now(timezone.utc)
    face = OrderLine.unit_retail * OrderLine.quantity
    indicative = (OrderLine.unit_retail - OrderLine.unit_purchase) * OrderLine.quantity
    own = Merchant.is_platform_owner.is_(True)
    stmt = (select(Merchant.id, Merchant.name, Merchant.is_platform_owner,
                   func.count(distinct(Order.id)), func.count(OrderLine.id),
                   func.sum(OrderLine.quantity), func.sum(face),
                   func.sum(case((own, face), else_=0)),
                   func.sum(case((own, indicative), else_=0)),
                   func.sum(case((own, 1), else_=0)))
            .select_from(OrderLine)
            .join(Order, Order.id == OrderLine.order_id)
            .outerjoin(Merchant, Merchant.id == OrderLine.merchant_id)
            .where(Order.created_at >= utc_start(days, now), Order.created_at <= now,
                   Order.total_is_final.is_(True)))
    if merchant_id:
        stmt = stmt.where(OrderLine.merchant_id == merchant_id)
    stmt = stmt.group_by(Merchant.id, Merchant.name, Merchant.is_platform_owner).order_by(func.sum(face).desc())
    rows = []
    for mid, name, is_owner, orders, lines, units, gross, own_value, spread, own_lines in (await db.execute(stmt)).all():
        role = 'OWNER' if is_owner is True else 'AGENCY_MERCHANT' if mid is not None else 'UNATTRIBUTED'
        rows.append({'merchant_id': str(mid) if mid else None, 'merchant_name': name or 'Не определён',
                     'role': role, 'orders_with_final_quotes': int(orders), 'order_lines': int(lines),
                     'units': int(units or 0), 'final_quote_line_value_rub': rub(Decimal(gross or 0)),
                     'owner_quote_value_rub': rub(Decimal(own_value or 0)) if role == 'OWNER' else None,
                     'owner_buy_sell_spread_rub': rub(Decimal(spread or 0)) if own_lines else None,
                     'platform_agency_fee_rub': None,
                     'verified_revenue_rub': None, 'verified_profit_rub': None})
    return {'source': 'FINAL_ORDER_LINES_UNRECONCILED', 'days': days, 'rows': rows,
            'verified_revenue_rub': None, 'verified_profit_rub': None,
            'description': 'Only final quotes, not paid sales. Owner buy-sell spread is before VAT, fees, logistics, refunds and taxes. Agency fee/payout are unknown until real settlement integration; never count seller merchandise as platform revenue.'}


def rounding_comparison(price: Decimal) -> dict:
    """Pure F2 arithmetic preview, never applies or publishes any price."""
    exact = customer_price_rounding(price, 'EXACT_RUB')
    smart = customer_price_rounding(price, 'SMART_UP')
    return {'status': 'SIMULATION', 'source': 'F2_SMART_ROUNDING_DETERMINISTIC',
            'input_gross_rub': str(price), 'exact_rub': exact, 'smart_up': smart,
            'difference_rub': rub(Decimal(smart['customer_gross']) - Decimal(exact['customer_gross'])),
            'conversion_uplift': None, 'real_offer_changed': False,
            'description': 'No customer conversion experiment and no link to active price policies. Agency prices are not rounded.'}


@router.get('/supplier-quotes')
async def supplier_quotes_endpoint(days: int = Query(90, ge=1, le=365),
                                   article: str | None = Query(None, min_length=2, max_length=90),
                                   supplier_code: str | None = Query(None, min_length=1, max_length=40),
                                   limit: int = Query(30, ge=1, le=100),
                                   db: AsyncSession = Depends(get_session)):
    return await quote_comparison(db, days, article=article, supplier_code=supplier_code, limit=limit)


@router.get('/merchants')
async def merchant_economics_endpoint(days: int = Query(30, ge=1, le=365),
                                      merchant_id: UUID | None = None,
                                      db: AsyncSession = Depends(get_session)):
    return await merchant_economics(db, days, merchant_id=merchant_id)


@router.get('/rounding-preview')
async def rounding_preview_endpoint(price: Decimal = Query(Decimal('1335'), ge=0, le=1_000_000,
                                                    max_digits=12, decimal_places=2)):
    return rounding_comparison(price)


@router.get('/export/supplier-quotes.csv')
async def supplier_quotes_csv(days: int = Query(90, ge=1, le=365),
                              article: str | None = Query(None, min_length=2, max_length=90),
                              db: AsyncSession = Depends(get_session)):
    d = await quote_comparison(db, days, article=article, limit=100)
    rows = [[row['brand'], row['article'], row['name'], s['supplier'],
             s['historical_min_purchase_rub'], s['historical_max_purchase_rub'],
             s['last_observed_at'], s['snapshots'], 'HISTORICAL_ONLY']
            for row in d['rows'] for s in row['suppliers']]
    return csv_response(['Бренд','Артикул','Товар','Поставщик','Историческая мин. закупка, ₽',
                         'Историческая макс. закупка, ₽','Последнее наблюдение UTC',
                         'Снимков','Статус'], rows, f'analytics_a3_supplier_history_{days}d.csv')


@router.get('/export/merchants.xlsx')
async def merchants_excel(days: int = Query(30, ge=1, le=365),
                          db: AsyncSession = Depends(get_session)):
    d = await merchant_economics(db, days)
    rows = [[r['merchant_name'], r['role'], r['orders_with_final_quotes'], r['units'],
             Decimal(r['final_quote_line_value_rub']),
             Decimal(r['owner_buy_sell_spread_rub']) if r['owner_buy_sell_spread_rub'] is not None else '',
             'UNRECONCILED_NO_SHADOW'] for r in d['rows']]
    return xlsx_response(['Продавец','Роль','Заказов с конечной ценой','Штук',
                          'Стоимость строк (НЕ выручка), ₽','Спред своих товаров (НЕ прибыль), ₽',
                          'Статус'], rows, f'analytics_a3_merchants_{days}d.xlsx')
