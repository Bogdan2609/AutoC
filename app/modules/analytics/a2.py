"""Analytics A2: read-only, operational (unreconciled) management metrics.

No figure here is certified revenue or profit. No bank, suppliers, payment
providers or SHADOW ledger modules are accessed.
"""
from __future__ import annotations

import csv
import io
import re
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import case, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Brand, Product
from app.modules.catalog_ext.models import Category, ProductCategory
from app.modules.commerce.models import Order, OrderLine
from app.modules.marketplace.models import Merchant
from app.modules.offer_engine.models import SupplierLog
from app.modules.suppliers.models import OfferSnapshot, Supplier, SupplierItem

router = APIRouter(prefix='/api/v1/backoffice/analytics/a2', tags=['analytics-a2-readonly'],
                   dependencies=[Depends(require_admin)])

ZERO = Decimal('0.00')


def rub(value: Decimal | None) -> str:
    return f'{(value if value is not None else ZERO):.2f}'


def period_start(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


def period_label(day: date, bucket: str) -> str:
    if bucket == 'month':
        return day.strftime('%Y-%m')
    if bucket == 'week':
        return (day - timedelta(days=day.weekday())).isoformat()
    return day.isoformat()


def calendar_buckets(start: date, end: date, bucket: str) -> list[str]:
    # Even empty periods appear on a chart; UTC dates and no guessed amounts.
    result: list[str] = []
    day = start
    while day <= end:
        label = period_label(day, bucket)
        if not result or result[-1] != label:
            result.append(label)
        day += timedelta(days=1)
    return result


def safe_excel_text(value: object) -> str:
    """Stop CSV formula evaluation when opened in desktop spreadsheet software."""
    s = str(value if value is not None else '')
    if s and not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', s) and (s.lstrip().startswith(('=', '+', '-', '@')) or s.startswith(('\t', '\r', '\n'))):
        return "'" + s
    return s


def csv_response(headers: list[str], rows: list[list[object]], filename: str) -> Response:
    out = io.StringIO(newline='')
    writer = csv.writer(out, delimiter=';', lineterminator='\r\n', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_excel_text(v) for v in row])
    return Response(content='\ufeff' + out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"',
                             'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})



def xlsx_response(headers: list[str], rows: list[list[object]], filename: str) -> Response:
    """Dependency-free simple OOXML workbook; untrusted strings are never formulas."""
    def col_name(col: int) -> str:
        out = ''
        while col:
            col, r = divmod(col - 1, 26)
            out = chr(65 + r) + out
        return out

    def cell(row: int, col: int, raw: object) -> str:
        ref = f'{col_name(col)}{row}'
        val = '' if raw is None else str(raw)
        if isinstance(raw, (int, Decimal)) and not isinstance(raw, bool):
            return f'<c r="{ref}"><v>{raw}</v></c>'
        # XML 1.0 excludes C0 controls and surrogate code points.
        val = ''.join(ch for ch in val if ch in ('\t', '\n', '\r') or
                      0x20 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD or
                      0x10000 <= ord(ch) <= 0x10FFFF)
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{xml_escape(val)}</t></is></c>'

    payload = [
        ['АНАЛИТИКА A2 — ПРЕДВАРИТЕЛЬНЫЕ ДАННЫЕ, НЕ ВЫРУЧКА И НЕ ОТЧЁТНОСТЬ'],
        ['Источник: заказы с окончательной ценой; без банковской сверки; SHADOW исключён'],
        [], headers, *rows,
    ]
    sheet = ''.join('<row r="%s">%s</row>' % (i, ''.join(cell(i, c, v) for c, v in enumerate(row, 1)))
                    for i, row in enumerate(payload, 1))
    worksheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
                 '<sheetFormatPr defaultRowHeight="17"/>'
                 '<cols><col min="1" max="1" width="25" customWidth="1"/>'
                 '<col min="2" max="3" width="30" customWidth="1"/>'
                 '<col min="4" max="8" width="22" customWidth="1"/></cols>'
                 '<sheetData>' + sheet + '</sheetData></worksheet>')
    buff = io.BytesIO()
    with ZipFile(buff, 'w', compression=ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        z.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        z.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Аналитика" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        z.writestr('xl/worksheets/sheet1.xml', worksheet)
    return Response(buff.getvalue(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"',
                             'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


async def trends(db: AsyncSession, days: int, bucket: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = period_start(days, now)
    utc_day = (func.date(func.timezone('UTC', Order.created_at))
               if isinstance(db, AsyncSession) else func.date(Order.created_at))
    final_value = func.sum(case((Order.total_is_final.is_(True), Order.total), else_=ZERO))
    final_count = func.sum(case((Order.total_is_final.is_(True), 1), else_=0))
    stmt = (select(utc_day, func.count(Order.id), final_count, final_value)
            .where(Order.created_at >= start, Order.created_at <= now)
            .group_by(utc_day).order_by(utc_day))
    rows = (await db.execute(stmt)).all()
    amounts: dict[str, list] = defaultdict(lambda: [0, 0, ZERO])
    for raw_day, n, nf, value in rows:
        day = date.fromisoformat(str(raw_day)[:10])
        k = period_label(day, bucket)
        amounts[k][0] += int(n)
        amounts[k][1] += int(nf or 0)
        amounts[k][2] += Decimal(value or 0)
    series = [{'period': k, 'orders_created': amounts[k][0],
               'final_price_orders': amounts[k][1], 'final_order_value_rub': rub(amounts[k][2])}
              for k in calendar_buckets(start.date(), now.date(), bucket)]
    return {'source': 'ORDER_DB_UNVERIFIED', 'days': days, 'bucket': bucket, 'timezone': 'UTC',
            'series': series, 'verified_revenue_rub': None, 'verified_net_profit_rub': None,
            'description': 'Created orders and face-value of final quotes; not paid or recognized revenue.'}


async def product_ranking(db: AsyncSession, days: int, *, brand_id: UUID | None = None,
                          category_id: UUID | None = None, merchant_id: UUID | None = None,
                          supplier_code: str | None = None, limit: int = 20,
                          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = period_start(days, now)
    face = OrderLine.unit_retail * OrderLine.quantity
    cost = OrderLine.unit_purchase * OrderLine.quantity
    # Indicative buy-vs-sell spread only for identified platform-owned merchant.
    # It is neither accounting cost of sales nor taxable / recognized profit.
    own_spread = case((Merchant.is_platform_owner.is_(True), face - cost), else_=None)
    stmt = (select(Product.id, Product.name, Product.article, Brand.name,
                   func.count(distinct(Order.id)), func.sum(OrderLine.quantity), func.sum(face),
                   func.sum(own_spread), func.count(own_spread))
            .join(OrderLine, OrderLine.product_id == Product.id)
            .join(Order, Order.id == OrderLine.order_id)
            .join(Brand, Brand.id == Product.brand_id)
            .outerjoin(Merchant, Merchant.id == OrderLine.merchant_id)
            .where(Order.created_at >= start, Order.created_at <= now,
                   Order.total_is_final.is_(True)))
    if brand_id is not None:
        stmt = stmt.where(Product.brand_id == brand_id)
    if merchant_id is not None:
        stmt = stmt.where(OrderLine.merchant_id == merchant_id)
    if supplier_code:
        stmt = stmt.where(OrderLine.supplier_code == supplier_code)
    if category_id is not None:
        stmt = stmt.where(exists(select(ProductCategory.id).where(
            ProductCategory.product_id == Product.id,
            ProductCategory.category_id == category_id)))
    stmt = (stmt.group_by(Product.id, Product.name, Product.article, Brand.name)
            .order_by(func.sum(face).desc(), Product.id).limit(limit))
    rows = (await db.execute(stmt)).all()
    return {'source': 'FINAL_ORDER_LINE_SNAPSHOTS_UNRECONCILED', 'days': days,
            'filters': {'brand_id': str(brand_id) if brand_id else None,
                        'category_id': str(category_id) if category_id else None,
                        'merchant_id': str(merchant_id) if merchant_id else None,
                        'supplier_code': supplier_code},
            'rows': [{'product_id': str(pid), 'name': name, 'article': article, 'brand': brand,
                      'orders': int(n), 'units': int(qty or 0), 'order_line_value_rub': rub(value),
                      'owner_spread_estimate_rub': rub(spread) if count_own else None,
                      'owner_spread_coverage_lines': int(count_own or 0)}
                     for pid, name, article, brand, n, qty, value, spread, count_own in rows],
            'verified_revenue_rub': None, 'verified_profit_rub': None,
            'description': 'Not sales rankings by bank-confirmed revenue. Only final quote order lines; owner spread excludes VAT, logistics, fees, returns and agency offers.'}


async def supplier_quality(db: AsyncSession, days: int, *, supplier_code: str | None = None,
                           now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = period_start(days, now)
    suppliers = (await db.execute(select(Supplier.id, Supplier.code).order_by(Supplier.code))).all()
    if supplier_code:
        suppliers = [s for s in suppliers if s.code == supplier_code]
    # Aggregates never call external providers.
    logs = (await db.execute(select(SupplierLog.code, SupplierLog.outcome,
                                    func.count(SupplierLog.id), func.avg(SupplierLog.duration_ms))
                              .where(SupplierLog.created_at >= start, SupplierLog.created_at <= now)
                              .group_by(SupplierLog.code, SupplierLog.outcome))).all()
    log_counts: dict[str, dict[str, int]] = defaultdict(dict)
    durations: dict[str, list[tuple[int, Decimal]]] = defaultdict(list)
    for code, outcome, n, avg_ms in logs:
        log_counts[code][str(outcome)] = int(n)
        durations[code].append((int(n), Decimal(str(avg_ms or 0))))
    snapshots = dict((await db.execute(select(SupplierItem.supplier_id, func.count(OfferSnapshot.id))
                    .join(OfferSnapshot, OfferSnapshot.supplier_item_id == SupplierItem.id)
                    .where(OfferSnapshot.observed_at >= start, OfferSnapshot.observed_at <= now)
                    .group_by(SupplierItem.supplier_id))).all())
    rows = []
    for sid, code in suppliers:
        result = log_counts.get(code, {})
        durs = durations.get(code, [])
        count = sum(result.values())
        avg = (sum(Decimal(n) * v for n, v in durs) / count).quantize(Decimal('0.1')) if count else None
        rows.append({'supplier': code, 'api_requests_logged': count,
                     'api_outcomes': result, 'avg_logged_duration_ms': str(avg) if avg is not None else None,
                     'offer_snapshots_in_window': int(snapshots.get(sid, 0))})
    return {'source': 'SUPPLIER_API_LOGS_AND_SNAPSHOT_HISTORY', 'days': days, 'rows': rows,
            'description': 'Historical API activity; does not measure actual delivery SLA, successful procurement or current stock.'}


@router.get('/trend')
async def trend_endpoint(days: int = Query(30, ge=1, le=365),
                         bucket: str = Query('day', pattern='^(day|week|month)$'),
                         db: AsyncSession = Depends(get_session)):
    return await trends(db, days, bucket)


@router.get('/products')
async def products_endpoint(days: int = Query(30, ge=1, le=365),
                            brand_id: UUID | None = None, category_id: UUID | None = None,
                            merchant_id: UUID | None = None,
                            supplier_code: str | None = Query(None, min_length=1, max_length=40),
                            limit: int = Query(20, ge=1, le=50),
                            db: AsyncSession = Depends(get_session)):
    return await product_ranking(db, days, brand_id=brand_id, category_id=category_id,
                                 merchant_id=merchant_id, supplier_code=supplier_code, limit=limit)


@router.get('/suppliers')
async def suppliers_endpoint(days: int = Query(30, ge=1, le=365),
                             supplier_code: str | None = Query(None, min_length=1, max_length=40),
                             db: AsyncSession = Depends(get_session)):
    return await supplier_quality(db, days, supplier_code=supplier_code)


@router.get('/export/products.csv')
async def products_export(days: int = Query(30, ge=1, le=365),
                          brand_id: UUID | None = None, category_id: UUID | None = None,
                          merchant_id: UUID | None = None,
                          supplier_code: str | None = Query(None, min_length=1, max_length=40),
                          db: AsyncSession = Depends(get_session)):
    d = await product_ranking(db, days, brand_id=brand_id, category_id=category_id,
                              merchant_id=merchant_id, supplier_code=supplier_code, limit=50)
    rows = [[r['brand'], r['article'], r['name'], r['orders'], r['units'],
             r['order_line_value_rub'], r['owner_spread_estimate_rub'] or '',
             'UNRECONCILED_ORDER_DATA'] for r in d['rows']]
    return csv_response(['Бренд', 'Артикул', 'Товар', 'Заказов', 'Штук',
                         'Стоимость строк заказа (НЕ выручка), RUB',
                         'Расчётный ценовой спред (НЕ прибыль), RUB', 'Статус данных'],
                        rows, f'analytics_a2_products_{days}d.csv')


@router.get('/export/trend.csv')
async def trend_export(days: int = Query(30, ge=1, le=365),
                       bucket: str = Query('day', pattern='^(day|week|month)$'),
                       db: AsyncSession = Depends(get_session)):
    d = await trends(db, days, bucket)
    rows = [[r['period'], r['orders_created'], r['final_price_orders'],
             r['final_order_value_rub'], 'UNRECONCILED_ORDER_DATA'] for r in d['series']]
    return csv_response(['Период UTC', 'Создано заказов', 'С окончательной ценой',
                         'Стоимость заказов (НЕ выручка), RUB', 'Статус данных'], rows,
                        f'analytics_a2_trend_{days}d_{bucket}.csv')

@router.get('/filters')
async def available_filters(db: AsyncSession = Depends(get_session)):
    async def fetch(model):
        return [{'id': str(row[0]), 'name': str(row[1])} for row in
                (await db.execute(select(model.id, model.name).order_by(model.name).limit(300))).all()]
    return {'brands': await fetch(Brand), 'categories': await fetch(Category),
            'merchants': await fetch(Merchant),
            'suppliers': [str(v) for v in (await db.scalars(select(Supplier.code).order_by(
                Supplier.code).limit(300))).all()],
            'scope': 'Filters apply to PRODUCT ranking, supplier selection is historical; order chart is global.'}


@router.get('/export/products.xlsx')
async def products_excel(days: int = Query(30, ge=1, le=365),
                         brand_id: UUID | None = None, category_id: UUID | None = None,
                         merchant_id: UUID | None = None,
                         supplier_code: str | None = Query(None, min_length=1, max_length=40),
                         db: AsyncSession = Depends(get_session)):
    d = await product_ranking(db, days, brand_id=brand_id, category_id=category_id,
                              merchant_id=merchant_id, supplier_code=supplier_code, limit=50)
    rows = [[r['brand'], r['article'], r['name'], r['orders'], r['units'],
             Decimal(r['order_line_value_rub']),
             Decimal(r['owner_spread_estimate_rub']) if r['owner_spread_estimate_rub'] is not None else '',
             'UNRECONCILED_ORDER_DATA'] for r in d['rows']]
    return xlsx_response(['Бренд', 'Артикул', 'Товар', 'Заказов', 'Штук', 'Стоимость строк заказа, RUB',
                          'Ценовой спред собственного магазина, RUB', 'Статус данных'], rows,
                         f'analytics_a2_products_{days}d.xlsx')


@router.get('/export/trend.xlsx')
async def trend_excel(days: int = Query(30, ge=1, le=365),
                      bucket: str = Query('day', pattern='^(day|week|month)$'),
                      db: AsyncSession = Depends(get_session)):
    d = await trends(db, days, bucket)
    rows = [[r['period'], r['orders_created'], r['final_price_orders'],
             Decimal(r['final_order_value_rub']), 'UNRECONCILED_ORDER_DATA'] for r in d['series']]
    return xlsx_response(['Период UTC', 'Создано заказов', 'С окончательной ценой',
                          'Стоимость заказов (НЕ выручка), RUB', 'Статус данных'], rows,
                         f'analytics_a2_trend_{days}d_{bucket}.xlsx')

async def customer_activity(db: AsyncSession, days: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = period_start(days, now)
    grouped = (select(Order.user_id, func.count(Order.id).label('n'))
               .where(Order.created_at >= start, Order.created_at <= now)
               .group_by(Order.user_id)).subquery()
    distinct_count, multiple_count = (await db.execute(select(
        func.count(),func.coalesce(func.sum(
            case((grouped.c.n >= 2, 1), else_=0)), 0)).select_from(grouped))).one()
    final_count, final_total = (await db.execute(select(func.count(Order.id),func.sum(Order.total))
              .where(Order.created_at >= start, Order.created_at <= now,
                     Order.total_is_final.is_(True)))).one()
    average = ((Decimal(final_total) / Decimal(final_count)).quantize(Decimal('0.01'))
               if final_count else None)
    return {'source': 'ORDER_CREATION_ACTIVITY_UNVERIFIED', 'days': days,
            'distinct_users_with_orders': int(distinct_count),
            'users_with_multiple_orders': int(multiple_count),
            'average_final_quote_order_value_rub': rub(average) if average is not None else None,
            'description': 'Repeated order creations, not confirmed purchases or repeat buyers. No customer identifiers exposed.'}


@router.get('/customers')
async def customer_activity_endpoint(days: int = Query(30, ge=1, le=365),
                                     db: AsyncSession = Depends(get_session)):
    return await customer_activity(db, days)
