"""A1 read-only dashboard with strongly separated operational and SHADOW scopes.

Never interprets order totals as paid revenue, supplier quotes as offers available
now, provider success as reconciled bank settlement, or F3-F5 as real money.
Uses only SELECT queries, no external supplier/bank calls, no customer PII.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product
from app.modules.catalog_ext.models import AttributeDefinition, Category
from app.modules.commerce.models import Order, OrderLine
from app.modules.finance.ledger_models import LedgerBook, LedgerEntry
from app.modules.finance.payment_sim_models import ShadowPayment, ShadowRefund
from app.modules.finance.pricing_models import PricingRule, FeeSchedule
from app.modules.finance.register_models import ShadowTaxBook, ShadowTaxRow
from app.modules.payments.models import Payment, Refund
from app.modules.suppliers.models import Supplier, SupplierItem, OfferSnapshot
from app.modules.offer_engine.models import SupplierLog

router = APIRouter(
    prefix='/api/v1/backoffice/analytics',
    tags=['analytics-a1-readonly'],
    dependencies=[Depends(require_admin)],
)


def money(v: Decimal | int | None) -> str:
    return f'{(v if v is not None else Decimal("0")):.2f}'


async def scalar_int(session: AsyncSession, stmt) -> int:
    return int((await session.scalar(stmt)) or 0)


async def grouped(session: AsyncSession, column, table, *, filters=()):
    stmt = select(column, func.count()).select_from(table)
    for f in filters:
        stmt = stmt.where(f)
    result = await session.execute(stmt.group_by(column))
    return {str(k or 'unknown'): int(v) for k, v in result.all()}


async def catalog_data(db: AsyncSession):
    product_statuses = await grouped(db, Product.status, Product)
    return {
        'source': 'CATALOG_CURRENT',
        'products_total': sum(product_statuses.values()),
        'products_by_status': product_statuses,
        'categories_total': await scalar_int(db, select(func.count()).select_from(Category)),
        'attribute_definitions_total': await scalar_int(db, select(func.count()).select_from(AttributeDefinition)),
        'description': 'Catalog identities, not sales or product availability.',
    }


async def orders_data(db: AsyncSession, days: int, now: datetime):
    start = now - timedelta(days=days)
    # Total of customer_orders is not necessarily paid money; nonfinal quote requests
    # must not be summed into order face value.
    orders_by_status = await grouped(db, Order.status, Order, filters=(Order.created_at >= start,))
    subtotal = await db.scalar(select(func.sum(Order.total)).where(
        Order.created_at >= start, Order.total_is_final.is_(True)))
    final_count = await scalar_int(db, select(func.count()).select_from(Order).where(
        Order.created_at >= start, Order.total_is_final.is_(True)))
    provisional_count = await scalar_int(db, select(func.count()).select_from(Order).where(
        Order.created_at >= start, Order.total_is_final.is_(False)))
    payments_by_status = await grouped(db, Payment.status, Payment,
                                      filters=(Payment.created_at >= start,))
    reported_success = await db.scalar(select(func.sum(Payment.amount)).where(
        Payment.created_at >= start, Payment.status == 'succeeded'))
    # Payments that the provider reported succeeded are not bank-reconciled or
    # necessarily captured in F3. Never expose them as recognized revenue.
    return {
        'source': 'OPERATIONAL_DB_UNRECONCILED',
        'window_days': days,
        'orders_by_status': orders_by_status,
        'orders_total': sum(orders_by_status.values()),
        'final_quote_orders': final_count,
        'provisional_quote_orders': provisional_count,
        'final_quote_order_value_rub': money(subtotal),
        'payments_by_status': payments_by_status,
        'provider_reported_succeeded_rub': money(reported_success),
        'revenue_verified_rub': None,
        'net_profit_verified_rub': None,
        'description': 'Order face value and gateway statuses are NOT bank reconciliation or recognized revenue.',
    }


async def suppliers_data(db: AsyncSession, days: int, now: datetime):
    start = now - timedelta(days=days)
    suppliers = (await db.execute(select(Supplier.id, Supplier.code).order_by(Supplier.code))).all()
    # One aggregate per table; never attempt live probes or expose external API data.
    items = dict((await db.execute(select(SupplierItem.supplier_id, func.count()).group_by(
        SupplierItem.supplier_id))).all())
    confirmed = dict((await db.execute(select(SupplierItem.supplier_id, func.count()).where(
        SupplierItem.match_status == 'confirmed').group_by(SupplierItem.supplier_id))).all())
    recent_logs = (await db.execute(select(SupplierLog.code, SupplierLog.outcome, func.count()).where(
        SupplierLog.created_at >= start).group_by(SupplierLog.code, SupplierLog.outcome))).all()
    logs: dict[str, dict[str, int]] = {}
    for code, outcome, n in recent_logs:
        bucket = logs.setdefault(code, {})
        bucket[str(outcome)] = int(n)
    snapshots = dict((await db.execute(select(SupplierItem.supplier_id, func.count(OfferSnapshot.id))
                       .select_from(SupplierItem)
                       .join(OfferSnapshot, OfferSnapshot.supplier_item_id == SupplierItem.id)
                       .group_by(SupplierItem.supplier_id))).all())
    return {
        'source': 'SUPPLIER_LOCAL_HISTORY',
        'window_days': days,
        'suppliers': [
            {'code': code, 'catalog_items': int(items.get(sid, 0)),
             'confirmed_matches': int(confirmed.get(sid, 0)),
             'historical_offer_snapshots': int(snapshots.get(sid, 0)),
             'api_outcomes_in_window': logs.get(code, {})}
            for sid, code in suppliers
        ],
        'description': 'Offer snapshots are historical; NOT current availability or stock.',
    }


async def shadow_data(db: AsyncSession):
    return {
        'source': 'SHADOW_SIMULATION_ONLY',
        'included_in_verified_financials': False,
        'f2': {
            'pricing_rules': await scalar_int(db, select(func.count()).select_from(PricingRule)),
            'fee_schedules': await scalar_int(db, select(func.count()).select_from(FeeSchedule)),
        },
        'f3': {
            'books': await scalar_int(db, select(func.count()).select_from(LedgerBook)),
            'entries': await scalar_int(db, select(func.count()).select_from(LedgerEntry)),
        },
        'f4': {
            'simulated_payments': await scalar_int(db, select(func.count()).select_from(ShadowPayment)),
            'simulated_refunds': await scalar_int(db, select(func.count()).select_from(ShadowRefund)),
        },
        'f5': {
            'tax_preview_books': await scalar_int(db, select(func.count()).select_from(ShadowTaxBook)),
            'tax_preview_rows': await scalar_int(db, select(func.count()).select_from(ShadowTaxRow)),
        },
        'description': 'These are simulation counters, not balances, receipts or statutory tax filings.',
    }


@router.get('/overview')
async def overview(days: int = Query(default=30, ge=1, le=90), db: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    return {
        'version': 'A1_READ_ONLY',
        'generated_at_utc': now.isoformat(),
        'window_days': days,
        'financial_reconciliation': 'NOT_CONNECTED',
        'verified_revenue_available': False,
        'verified_profit_available': False,
        'catalog': await catalog_data(db),
        'operations': await orders_data(db, days, now),
        'suppliers': await suppliers_data(db, days, now),
        'simulations': await shadow_data(db),
        'warnings': [
            'No verified recognized revenue or profit; bank and fiscal integration remain shadow-only.',
            'Local order/payment data may include tests. Supplier quote snapshots are not live stock.',
            'F3/F4/F5 simulated amounts are excluded from operational sales and revenue.',
        ],
    }


@router.get('/order-trend')
async def order_trend(days: int = Query(default=30, ge=1, le=90), db: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    # func.date is supported by SQLite (unit tests) and PostgreSQL (production).
    day = func.date(Order.created_at)
    rows = (await db.execute(select(day, func.count()).where(Order.created_at >= start)
                             .group_by(day).order_by(day))).all()
    return {
        'source': 'OPERATIONAL_DB_UNVERIFIED', 'window_days': days,
        'series': [{'date': str(day), 'order_count': int(n)} for day, n in rows],
        'description': 'Order creations by UTC date, not recognized sales.',
    }
