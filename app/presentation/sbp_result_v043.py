"""Buyer-owned, read-only SBP outcome page. The browser never confirms money.

The bank's success URL, webhook, or redirect is NOT payment evidence. Only the
separately verified local payment record may be displayed as succeeded.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.commerce.models import Order
from app.modules.payments.models import Payment

router = APIRouter(tags=['sbp-result-v043'])


def interpret(status: str | None) -> tuple[str, str]:
    if status == 'succeeded':
        return 'paid', 'Оплата подтверждена системой'
    if status in ('canceled', 'cancelled'):
        return 'canceled', 'Платёж отменён'
    if status in ('pending', 'waiting_for_capture', 'creating', 'reconciliation_required'):
        return 'pending', 'Проверяем состояние платежа'
    return 'unknown', 'Статус платежа пока не подтверждён'


async def buyer_result(db, order_id: UUID, user_id: UUID) -> dict:
    order = await db.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(404, 'Order not found')
    rows = (await db.scalars(select(Payment).where(
        Payment.order_id == order_id).order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(10))).all()
    # Multiple successful records or nonfinal buyer order cannot be displayed as paid.
    paid = [p for p in rows if p.status == 'succeeded']
    if len(paid) == 1 and paid[0].provider_id and order.total_is_final and paid[0].amount == order.total:
        status = 'succeeded'
    elif len(paid) > 1 or (paid and (not order.total_is_final or paid[0].amount != order.total)):
        status = None
    else:
        status = rows[0].status if rows else None
        if status == 'succeeded':
            status = None
    code, message = interpret(status)
    return {'order_id': str(order_id), 'result': code, 'message': message,
            'read_only': True, 'bank_not_contacted': True,
            'note': 'Перенаправление после СБП не подтверждает оплату. Для уточнения обновите страницу.'}


@router.get('/api/v1/orders/{order_id}/sbp-result')
async def result_json(order_id: UUID, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    return await buyer_result(db, order_id, user.id)


@router.get('/sbp/result/{order_id}', response_class=HTMLResponse)
async def result_page(order_id: UUID):
    """Public redirect landing shell, never discloses a buyer's payment state.

    Browser redirects have no Bearer header; actual result stays behind the
    owner-authenticated JSON endpoint above. No bank requests, no mutations.
    """
    # The URL's order ID is not displayed, validated by the UUID path parameter.
    page = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>СБП — результат оплаты</title>
<style>body{font-family:system-ui,sans-serif;background:#f8fafc;color:#101828;margin:0;display:grid;min-height:100dvh;place-items:center}main{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;max-width:480px;margin:16px}h1{font-size:1.45rem}p{line-height:1.6}small{color:#64748b}a{color:#185adb}</style></head><body><main>
<h1>Возврат из СБП</h1><p>Само перенаправление не подтверждает успешную оплату. Для проверки результата войдите в магазин и откройте свой заказ.</p>
<p>Если статус пока неизвестен, не совершайте повторный платёж до сверки.</p>
<p><a href="/">Перейти в магазин</a></p>
<small>Страница не выполняет банковских запросов и не создаёт платежей.</small></main></body></html>'''
    return HTMLResponse(page, headers={'Cache-Control': 'no-store, private',
                                       'X-Content-Type-Options': 'nosniff',
                                       'Referrer-Policy': 'no-referrer',
                                       'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})
