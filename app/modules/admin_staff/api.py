"""Employee management and cookie login. API tokens never persist in browser JS."""
import hashlib
import secrets
from datetime import timedelta, timezone
from uuid import UUID

import anyio
from argon2 import PasswordHasher, exceptions
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import enforce_budget, peer_ip
from app.core.security import require_admin
from app.modules.accounts.models import User, utc
from app.modules.admin_staff.models import AdminAuditLog, AdminStaff, AdminLoginSession
from app.modules.admin_staff.policy import ROLES, _SCOPES, role_capabilities
from app.modules.admin_staff.session import ADMIN_COOKIE, ADMIN_SESSION_HOURS, clear_admin_cookie, csrf_token, set_admin_cookie, valid_csrf

router = APIRouter(prefix='/api/v1/admin/staff', tags=['admin-staff'])
ph = PasswordHasher(memory_cost=32768, time_cost=2, parallelism=1)
_hash_limiter = anyio.CapacityLimiter(4)
_dummy_password = ph.hash('not-the-password-of-any-user')


class StaffLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class StaffCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=160)
    role: str = Field(pattern=r'^(owner|operator|catalog_manager|supplier_manager|finance_manager|analyst|viewer)$')


class StaffUpdate(BaseModel):
    role: str | None = Field(default=None, pattern=r'^(owner|operator|catalog_manager|supplier_manager|finance_manager|analyst|viewer)$')
    enabled: bool | None = None


def describe(user: User, staff: AdminStaff):
    return {'id': str(staff.id), 'user_id': str(user.id), 'email': user.email,
            'display_name': user.display_name, 'role': staff.role, 'enabled': staff.enabled}


def _verify(encoded: str, password: str) -> bool:
    try:
        return bool(ph.verify(encoded, password))
    except (exceptions.VerificationError, exceptions.InvalidHashError):
        return False


async def _session_staff(db: AsyncSession, raw: str):
    if len(raw) != 64 or any(c not in '0123456789abcdef' for c in raw):
        raise HTTPException(401, 'Требуется вход сотрудника')
    digest = hashlib.sha256(raw.encode('ascii')).hexdigest()
    session = await db.scalar(select(AdminLoginSession).where(AdminLoginSession.digest == digest, AdminLoginSession.revoked.is_(False)))
    if session is None:
        raise HTTPException(401, 'Сеанс сотрудника завершён')
    expiry = session.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= utc():
        raise HTTPException(401, 'Сеанс сотрудника истёк')
    user = await db.get(User, session.user_id)
    staff = await db.scalar(select(AdminStaff).where(AdminStaff.user_id == session.user_id))
    if not user or not user.is_active or not staff or not staff.enabled:
        raise HTTPException(403, 'Сотрудник отключён')
    return session, user, staff


@router.post('/login')
async def login(body: StaffLogin, request: Request, db: AsyncSession = Depends(get_session)):
    email = str(body.email).strip().lower()
    await enforce_budget('staff_login_ip', peer_ip(request), limit=12, period=300)
    await enforce_budget('staff_login_user', email, limit=7, period=300)
    user = await db.scalar(select(User).where(User.email == email))
    staff = await db.scalar(select(AdminStaff).where(AdminStaff.user_id == user.id)) if user else None
    encoded = user.password_hash if (user and user.is_active and staff and staff.enabled) else _dummy_password
    ok = await anyio.to_thread.run_sync(_verify, encoded, body.password, limiter=_hash_limiter)
    if not ok or not user or not staff or not user.is_active or not staff.enabled:
        raise HTTPException(401, 'Неверный логин или пароль')
    raw = secrets.token_hex(32)
    db.add(AdminLoginSession(user_id=user.id, digest=hashlib.sha256(raw.encode('ascii')).hexdigest(),
                   expires_at=utc()+timedelta(hours=ADMIN_SESSION_HOURS)))
    await db.commit()
    response = JSONResponse({'staff': describe(user, staff), 'csrf': csrf_token(raw), 'permissions': role_capabilities(staff.role), 'scope_rules': [{'prefix': prefix, 'scope': scope} for prefix, scope in _SCOPES]})
    set_admin_cookie(response, raw)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/session')
async def current_staff(request: Request, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(ADMIN_COOKIE, '')
    _, user, staff = await _session_staff(db, raw)
    return JSONResponse({'staff': describe(user, staff), 'csrf': csrf_token(raw), 'permissions': role_capabilities(staff.role), 'scope_rules': [{'prefix': prefix, 'scope': scope} for prefix, scope in _SCOPES]}, headers={'Cache-Control':'no-store'})


@router.post('/logout')
async def logout(request: Request, x_admin_csrf: str | None = Header(default=None),
                 db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(ADMIN_COOKIE, '')
    session, _, _ = await _session_staff(db, raw)
    if not valid_csrf(raw, x_admin_csrf):
        raise HTTPException(403, 'Неверный CSRF-токен')
    session.revoked = True
    await db.commit()
    response = JSONResponse({'status': 'logged_out'})
    clear_admin_cookie(response)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/sessions', dependencies=[Depends(require_admin)])
async def list_my_sessions(request: Request, db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(ADMIN_COOKIE, '')
    _, user, _ = await _session_staff(db, raw)
    digest = hashlib.sha256(raw.encode('ascii')).hexdigest()
    records = (await db.scalars(select(AdminLoginSession).where(
        AdminLoginSession.user_id == user.id, AdminLoginSession.revoked.is_(False))
        .order_by(AdminLoginSession.created_at.desc()).limit(100))).all()
    now = utc()
    return [{'id': str(record.id), 'created_at': record.created_at.isoformat(),
             'expires_at': record.expires_at.isoformat(), 'current': record.digest == digest}
            for record in records if (record.expires_at.replace(tzinfo=timezone.utc)
            if record.expires_at.tzinfo is None else record.expires_at) > now]


@router.post('/sessions/revoke-others', dependencies=[Depends(require_admin)])
async def revoke_other_sessions(request: Request, x_admin_csrf: str | None = Header(default=None),
                                db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(ADMIN_COOKIE, '')
    _, user, _ = await _session_staff(db, raw)
    if not valid_csrf(raw, x_admin_csrf):
        raise HTTPException(403, 'Неверный CSRF-токен')
    digest = hashlib.sha256(raw.encode('ascii')).hexdigest()
    result = await db.execute(update(AdminLoginSession).where(
        AdminLoginSession.user_id == user.id, AdminLoginSession.digest != digest,
        AdminLoginSession.revoked.is_(False)).values(revoked=True))
    await db.commit()
    return {'revoked': result.rowcount}


@router.delete('/sessions/{session_id}', dependencies=[Depends(require_admin)])
async def revoke_my_session(session_id: UUID, request: Request,
                            x_admin_csrf: str | None = Header(default=None),
                            db: AsyncSession = Depends(get_session)):
    raw = request.cookies.get(ADMIN_COOKIE, '')
    _, user, _ = await _session_staff(db, raw)
    if not valid_csrf(raw, x_admin_csrf):
        raise HTTPException(403, 'Неверный CSRF-токен')
    record = await db.scalar(select(AdminLoginSession).where(
        AdminLoginSession.id == session_id, AdminLoginSession.user_id == user.id))
    if not record:
        raise HTTPException(404, 'Сеанс не найден')
    record.revoked = True
    await db.commit()
    response = JSONResponse({'status': 'revoked', 'current': record.digest == hashlib.sha256(raw.encode('ascii')).hexdigest()})
    if record.digest == hashlib.sha256(raw.encode('ascii')).hexdigest():
        clear_admin_cookie(response)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('', dependencies=[Depends(require_admin)])
async def list_staff(db: AsyncSession = Depends(get_session)):
    records = (await db.execute(select(User, AdminStaff).join(AdminStaff, User.id == AdminStaff.user_id)
                                .order_by(User.email).limit(250))).all()
    return [describe(user, staff) for user, staff in records]


@router.post('', status_code=201, dependencies=[Depends(require_admin)])
async def create_staff(body: StaffCreate, db: AsyncSession = Depends(get_session)):
    encoded = await anyio.to_thread.run_sync(ph.hash, body.password, limiter=_hash_limiter)
    user = User(email=str(body.email).strip().lower(), password_hash=encoded,
                display_name=body.display_name.strip(), is_active=True)
    db.add(user)
    try:
        await db.flush()
        staff = AdminStaff(user_id=user.id, role=body.role, enabled=True)
        db.add(staff)
        await db.commit()
        await db.refresh(staff)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Этот email уже занят; выберите отдельную учётную запись сотрудника') from exc
    return describe(user, staff)


@router.patch('/{staff_id}', dependencies=[Depends(require_admin)])
async def change_staff(staff_id: UUID, body: StaffUpdate, db: AsyncSession = Depends(get_session)):
    if body.role is None and body.enabled is None:
        raise HTTPException(422, 'Не указано изменение')
    # Lock all owners in a fixed order before potentially removing owner access.
    # Concurrent demotions cannot both pass a stale 'last owner' count.
    owners = (await db.scalars(select(AdminStaff).where(AdminStaff.role == 'owner')
                               .order_by(AdminStaff.id).with_for_update())).all()
    staff = await db.get(AdminStaff, staff_id, with_for_update=True)
    if staff is None:
        raise HTTPException(404, 'Сотрудник не найден')
    # Prevent accidental lockout even if legacy token has already been disabled.
    if staff.role == 'owner' and ((body.role is not None and body.role != 'owner') or body.enabled is False):
        remaining = sum(1 for owner in owners if owner.enabled and owner.id != staff.id)
        if remaining == 0:
            raise HTTPException(409, 'Нельзя отключить последнего владельца')
    if body.role is not None:
        staff.role = body.role
    if body.enabled is not None:
        staff.enabled = body.enabled
    # Any role change or enable/disable invalidates prior admin sessions.
    await db.execute(update(AdminLoginSession).where(AdminLoginSession.user_id == staff.user_id,
                         AdminLoginSession.revoked.is_(False)).values(revoked=True))
    await db.commit()
    user = await db.get(User, staff.user_id)
    return describe(user, staff)


@router.get('/audit', dependencies=[Depends(require_admin)])
async def audit(limit: int = 50, db: AsyncSession = Depends(get_session)):
    if not 1 <= limit <= 200:
        raise HTTPException(422, 'limit must be 1..200')
    rows = (await db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.occurred_at.desc())
                             .limit(limit))).all()
    return [{'at': r.occurred_at.isoformat(), 'actor_id': str(r.actor_user_id) if r.actor_user_id else None,
             'actor_kind': r.actor_kind, 'action': r.action, 'path': r.path, 'scope': r.scope}
            for r in rows]
