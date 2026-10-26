"""Customer-facing server-rendered HTML. Reuses Accounts/Cart/Orders business actions.

No supplier network calls from GET handlers. No browser payment processing; the
existing API remains fail-closed until live purchase/fiscal contracts are approved.
"""

import hashlib
import secrets
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.config import get_settings
from app.modules.accounts import api as accounts_api
from app.modules.accounts.models import Address, Session, User
from app.modules.commerce import api as commerce_api
from app.modules.commerce import quote_workflow
from app.modules.commerce.models import CartLine, Order, OrderLine, Replacement
from app.modules.payments.models import Payment
from app.modules.pricing.service import calculate_retail, get_active_policy
from app.modules.catalog.models import Product
from app.presentation.browser_security import (
    AUTH_COOKIE, attach_csrf_cookie, browser_user, check_csrf,
    csrf_for, safe_next, set_login_cookie,
)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="templates")


def html(request: Request, template: str, *, user: User | None = None,
         error: str | None = None, code: int = 200, **context):
    csrf, anon_seed = csrf_for(request, user)
    resp = templates.TemplateResponse(request, template, {
        "user": user, "csrf": csrf, "error": error, "title": context.pop("title", "Автозапчасти"),
        **context,
    }, status_code=code, headers={"X-Robots-Tag": "noindex, nofollow",
                                "Cache-Control": "private, no-store"})
    return attach_csrf_cookie(resp, anon_seed)


def go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def must_login(user: User | None, path: str):
    if user is None:
        return go("/login?next=" + quote(safe_next(path), safe=""))
    return None


def readable(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return str(exc.detail.get("message") or exc.detail.get("reason") or
                   exc.detail.get("error") or "Данные предложения изменились. Проверьте заказ.")[:260]
    if exc.status_code == 503:
        return "Оплата СБП ещё не подключена: нужен проверенный платёжно-закупочный контур."
    return {
        "Not enough cached stock": "Недостаточно товара в сохранённых остатках.",
        "Empty cart": "Корзина пуста.",
        "No pending replacement": "Предложение замены уже обработано.",
    }.get(str(exc.detail), str(exc.detail)[:260])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/account", db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if user:
        return go(safe_next(next))
    return html(request, "auth.html", mode="login", next=safe_next(next), title="Вход")


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, email: str = Form(), password: str = Form(),
                       csrf: str = Form(), next: str = Form(default="/account"),
                       db: AsyncSession = Depends(get_session)):
    check_csrf(request, csrf, None)
    dest = safe_next(next)
    try:
        reply = await accounts_api.login(accounts_api.Login(email=email, password=password), request=request, db=db)
    except (HTTPException, ValidationError):
        return html(request, "auth.html", mode="login", next=dest, title="Вход",
                    error="Не удалось войти. Проверьте почту и пароль.", code=400)
    resp = go(dest)
    set_login_cookie(resp, reply["access_token"])
    return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, next: str = "/account", db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if user:
        return go(safe_next(next))
    return html(request, "auth.html", mode="register", next=safe_next(next), title="Регистрация")


@router.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request, email: str = Form(), password: str = Form(),
                          display_name: str = Form(default=""), csrf: str = Form(),
                          next: str = Form(default="/account"),
                          db: AsyncSession = Depends(get_session)):
    check_csrf(request, csrf, None)
    dest = safe_next(next)
    try:
        body = accounts_api.Register(email=email, password=password, display_name=display_name)
        await accounts_api.register(body, request=request, db=db)
        token = await accounts_api.login(accounts_api.Login(email=email, password=password), request=request, db=db)
    except ValidationError:
        return html(request, "auth.html", mode="register", next=dest, title="Регистрация",
                    error="Укажите корректный email и пароль минимум из 12 символов.", code=400)
    except HTTPException as exc:
        return html(request, "auth.html", mode="register", next=dest, title="Регистрация",
                    error="Этот адрес электронной почты уже используется." if exc.status_code == 409
                    else "Не удалось создать аккаунт.", code=409)
    resp = go(dest)
    set_login_cookie(resp, token["access_token"])
    return resp


@router.post("/logout")
async def logout(request: Request, csrf: str = Form(), db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if not user:
        raise HTTPException(403, "Активная сессия не найдена")
    check_csrf(request, csrf, user)
    raw = request.cookies[AUTH_COOKIE]
    digest = hashlib.sha256(raw.encode()).hexdigest()
    session = await db.scalar(select(Session).where(Session.digest == digest))
    if session:
        session.revoked = True
        await db.commit()
    resp = go("/")
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/account"):
        return redirect
    items = (await db.scalars(select(Address).where(Address.user_id == user.id))).all()
    orders = (await db.scalars(select(Order).where(Order.user_id == user.id)
                               .order_by(Order.created_at.desc()).limit(5))).all()
    return html(request, "account.html", user=user, addresses=items, orders=orders,
                title="Личный кабинет")


@router.post("/account/addresses")
async def account_add_address(request: Request, csrf: str = Form(), contact: str = Form(),
                              phone: str = Form(), address: str = Form(),
                              db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/account"):
        return redirect
    check_csrf(request, csrf, user)
    try:
        await accounts_api.add_address(
            accounts_api.NewAddress(contact=contact, phone=phone, address=address),
            user=user, db=db)
    except ValidationError:
        return go("/account?message=invalid-address")
    return go("/account")


async def cart_rows(db: AsyncSession, user: User) -> tuple[list[dict], Decimal]:
    cart = await commerce_api.open_cart(db, user, create=False)
    if not cart:
        return [], Decimal("0")
    lines = (await db.scalars(select(CartLine).where(CartLine.cart_id == cart.id))).all()
    policy = await get_active_policy(db)
    result = []
    total = Decimal("0")
    for line in lines:
        if line.seller_offer_id:
            from app.modules.marketplace.models import SellerOffer, Store
            from app.modules.marketplace.service import offer_check, options_for_offer
            from app.modules.marketplace.models import UsedPart
            offer = await db.get(SellerOffer,line.seller_offer_id)
            try:
                if not offer: raise HTTPException(404,'Offer not found')
                await offer_check(db,offer.id)
                opts=await options_for_offer(db,offer,line.fulfillment_node_id)
                available=bool(opts and max(x['stock'] for x in opts)>=line.quantity)
                product=await db.get(Product,offer.product_id) if offer.product_id else None
                used=await db.get(UsedPart,offer.used_part_id) if offer.used_part_id else None
                store=await db.get(Store,offer.store_id)
                subtotal=offer.price*line.quantity
                total+=subtotal
                result.append({'line':line,'product':product,'title':used.title if used else product.name if product else 'Товар',
                               'supplier_name':store.name if store else 'Магазин',
                               'snapshot':None,'unit_price':offer.price,'subtotal':subtotal,
                               'available':available,'error':None})
            except HTTPException:
                result.append({'line':line,'product':None,'title':'Предложение недоступно',
                               'supplier_name':'','snapshot':None,'unit_price':None,
                               'subtotal':None,'available':False,'error':'Предложение недоступно'})
            continue
        try:
            item, product, brand, supplier, snap = await commerce_api.offer_context(db, line.supplier_item_id)
            unit_price = calculate_retail(snap.purchase_price, policy.markup_percent) if policy else None
            observed = snap.observed_at
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            fresh = 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= get_settings().offer_max_age_seconds
            available = bool(policy and fresh and snap.quantity != 0 and
                             (snap.quantity is None or snap.quantity >= line.quantity))
            subtotal = unit_price * line.quantity if unit_price is not None else None
            if subtotal is not None:
                total += subtotal
            result.append({"line": line, "product": product, "brand": brand, "supplier": supplier,
                           "snapshot": snap, "title": product.name, "supplier_name":supplier.name, "unit_price": unit_price,
                           "subtotal": subtotal, "available": available, "error": None})
        except HTTPException:
            result.append({"line": line, "product": None, "unit_price": None,
                           "subtotal": None, "title": "Недоступное предложение", "supplier_name": "", "snapshot":None,"available": False,
                           "error": "Предложение больше не доступно"})
    return result, total


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(request: Request, db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/cart"):
        return redirect
    rows, total = await cart_rows(db, user)
    return html(request, "cart.html", user=user, rows=rows, total=total, title="Корзина")


@router.post("/cart/items", response_class=HTMLResponse)
async def cart_add(request: Request, supplier_item_id: uuid.UUID = Form(),
                   quantity: int = Form(), csrf: str = Form(),
                   next: str = Form(default="/cart"), db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, safe_next(next, "/cart")):
        return redirect
    check_csrf(request, csrf, user)
    try:
        body = commerce_api.Add(supplier_item_id=supplier_item_id, quantity=quantity)
        await commerce_api.add(body, user=user, db=db)
    except (HTTPException, ValidationError):
        return go(safe_next(next, "/cart") + ("&" if "?" in next else "?") + "message=offer-unavailable")
    return go("/cart")


@router.post('/cart/seller-items')
async def browser_seller_add(request:Request,seller_offer_id:uuid.UUID=Form(),
                             pickup_node_id:uuid.UUID=Form(),quantity:int=Form(),
                             csrf:str=Form(),db:AsyncSession=Depends(get_session)):
    user=await browser_user(request,db)
    if redirect:=must_login(user,'/cart'):return redirect
    check_csrf(request,csrf,user)
    from app.modules.marketplace.cart import add_seller_line,AddSeller
    try:
        await add_seller_line(AddSeller(seller_offer_id=seller_offer_id,
            pickup_node_id=pickup_node_id,quantity=quantity),user,db)
    except (HTTPException,ValidationError):return go('/cart?message=offer-unavailable')
    return go('/cart')


@router.post("/cart/items/{line_id}/quantity")
async def cart_quantity(line_id: uuid.UUID, request: Request, quantity: int = Form(),
                        csrf: str = Form(), db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/cart"):
        return redirect
    check_csrf(request, csrf, user)
    cart = await commerce_api.open_cart(db, user, create=False)
    line = await db.get(CartLine, line_id)
    if not cart or not line or line.cart_id != cart.id:
        raise HTTPException(404, "Позиция не найдена")
    if line.seller_offer_id:
        from app.modules.marketplace.cart import add_seller_line,AddSeller
        try:
            await add_seller_line(AddSeller(seller_offer_id=line.seller_offer_id,
                pickup_node_id=line.fulfillment_node_id,quantity=quantity),user,db)
        except (HTTPException,ValidationError):return go('/cart?message=offer-unavailable')
        return go('/cart')
    try:
        await commerce_api.add(commerce_api.Add(supplier_item_id=line.supplier_item_id,
                                                 quantity=quantity), user=user, db=db)
    except (HTTPException, ValidationError):
        return go("/cart?message=offer-unavailable")
    return go("/cart")


@router.post("/cart/items/{line_id}/remove")
async def cart_remove(line_id: uuid.UUID, request: Request, csrf: str = Form(),
                      db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/cart"):
        return redirect
    check_csrf(request, csrf, user)
    await commerce_api.remove(line_id, user=user, db=db)
    return go("/cart")


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request, db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/checkout"):
        return redirect
    rows, total = await cart_rows(db, user)
    if not rows:
        return go("/cart")
    addresses = (await db.scalars(select(Address).where(Address.user_id == user.id))).all()
    return html(request, "checkout.html", user=user, rows=rows, total=total, addresses=addresses,
                idem_key=secrets.token_hex(16), title="Оформление заказа",
                payment_blocked=get_settings().app_env != "test")


@router.post("/checkout", response_class=HTMLResponse)
async def checkout_submit(request: Request, csrf: str = Form(), idem_key: str = Form(),
                          contact: str = Form(), phone: str = Form(),
                          shipping_address: str = Form(), db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/checkout"):
        return redirect
    check_csrf(request, csrf, user)
    if not 8 <= len(idem_key) <= 100 or not all(ch in "0123456789abcdef" for ch in idem_key):
        raise HTTPException(422, "Неверный ключ заказа")
    try:
        body = commerce_api.Checkout(contact=contact, phone=phone,
                                     shipping_address=shipping_address)
        result = await commerce_api.checkout(body, idempotency_key=idem_key, user=user, db=db)
    except (HTTPException, ValidationError) as exc:
        rows, total = await cart_rows(db, user)
        addresses = (await db.scalars(select(Address).where(Address.user_id == user.id))).all()
        return html(request, "checkout.html", user=user, rows=rows, total=total, addresses=addresses,
                    idem_key=idem_key, title="Оформление заказа", error=(
                        readable(exc) if isinstance(exc, HTTPException) else "Проверьте данные доставки"),
                    contact=contact, phone=phone, shipping_address=shipping_address,
                    payment_blocked=get_settings().app_env != "test", code=409)
    return go("/orders/" + result["order_id"])


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/orders"):
        return redirect
    rows = (await db.scalars(select(Order).where(Order.user_id == user.id)
                             .order_by(Order.created_at.desc()).limit(100))).all()
    return html(request, "orders.html", user=user, orders=rows, title="Мои заказы")


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_page(order_id: uuid.UUID, request: Request,
                     db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/orders"):
        return redirect
    order = await db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, "Заказ не найден")
    lines = (await db.scalars(select(OrderLine).where(OrderLine.order_id == order.id))).all()
    replacements = (await db.scalars(select(Replacement).where(
        Replacement.order_line_id.in_([line.id for line in lines])))).all() if lines else []
    payment = await db.scalar(select(Payment).where(Payment.order_id == order.id))
    payment_link = None
    if payment and payment.payment_url:
        from urllib.parse import urlparse
        parsed = urlparse(payment.payment_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (host == "yookassa.ru" or host.endswith(".yookassa.ru") or
                                           (get_settings().app_env == "test" and host == "mock-gateway.test")):
            payment_link = payment.payment_url
    return html(request, "order.html", user=user, order=order, lines=lines,
                operator_quote=(order.request_details or {}).get("operator_quote"),
                replacements=replacements, payment=payment, payment_link=payment_link,
                title="Заказ " + str(order.id)[:8])


@router.post("/orders/{order_id}/refresh")
async def order_refresh(order_id: uuid.UUID, request: Request, csrf: str = Form(),
                        db: AsyncSession = Depends(get_session)):
    from app.modules.payments.api import refresh
    user = await browser_user(request, db)
    if redirect := must_login(user, "/orders"):
        return redirect
    check_csrf(request, csrf, user)
    try:
        await refresh(order_id, user=user, db=db)
    except HTTPException:
        return go(f"/orders/{order_id}?message=payment-check-failed")
    return go(f"/orders/{order_id}")


@router.post("/orders/{order_id}/replacements/{replacement_id}/{action}")
async def replacement_decision(order_id: uuid.UUID, replacement_id: uuid.UUID, action: str,
                               request: Request, csrf: str = Form(),
                               db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/orders"):
        return redirect
    check_csrf(request, csrf, user)
    if action not in ("accept", "decline"):
        raise HTTPException(404, "Unknown action")
    try:
        fn = commerce_api.accept if action == "accept" else commerce_api.decline
        await fn(order_id, replacement_id, user=user, db=db)
    except HTTPException:
        return go(f"/orders/{order_id}?message=replacement-failed")
    return go(f"/orders/{order_id}")


@router.post("/orders/{order_id}/quote/{action}")
async def browser_quote_decision(order_id: uuid.UUID, action: str, request: Request,
                                 csrf: str = Form(), revision: int = Form(),
                                 quote_digest: str = Form(),
                                 db: AsyncSession = Depends(get_session)):
    user = await browser_user(request, db)
    if redirect := must_login(user, "/orders"):
        return redirect
    check_csrf(request, csrf, user)
    if action not in ('accept', 'decline'):
        raise HTTPException(404, 'Unknown quote action')
    try:
        await quote_workflow.decision(order_id, quote_workflow.BuyerDecision(
            revision=revision, quote_digest=quote_digest, decision=action), user=user, db=db)
    except (HTTPException, ValidationError):
        return go(f"/orders/{order_id}?message=quote-changed-or-expired")
    return go(f"/orders/{order_id}")
