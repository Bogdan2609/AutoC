"""Central enforcement for old admin endpoints and new employee sessions.

Legacy X-Admin-Token works only while explicitly permitted. For employee cookies
RBAC, CSRF and revocation are checked at *each API call*, never just in React.
"""
import hashlib
import hmac
from datetime import timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import get_session
from app.modules.accounts.models import User, utc
from app.modules.admin_staff.models import AdminAuditLog, AdminStaff, AdminLoginSession
from app.modules.admin_staff.policy import resource_scope, role_allowed
from app.modules.admin_staff.session import ADMIN_COOKIE, valid_csrf

_MUTATIONS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


def valid_legacy_token(value: str | None) -> bool:
    settings = get_settings()
    expected = settings.admin_api_token
    return bool(settings.admin_legacy_token_enabled and expected and
                not expected.startswith('replace-') and value and
                hmac.compare_digest(expected, value))


async def require_admin(request: Request, x_admin_token: str | None = Header(default=None),
                        db: AsyncSession = Depends(get_session)) -> None:
    """A legacy superadmin or authorized employee may use existing endpoints.

    Unclassified endpoints are denied to staff (including owner) until explicitly
    included in the resource policy. Legacy is a break-glass migration path.
    """
    scope = resource_scope(request.url.path)
    legacy = valid_legacy_token(x_admin_token)
    actor = None
    actor_kind = 'legacy_token'
    if not legacy:
        raw = request.cookies.get(ADMIN_COOKIE, '')
        if len(raw) != 64 or any(c not in '0123456789abcdef' for c in raw):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Access denied')
        digest = hashlib.sha256(raw.encode('ascii')).hexdigest()
        session = await db.scalar(select(AdminLoginSession).where(AdminLoginSession.digest == digest, AdminLoginSession.revoked.is_(False)))
        if session is None:
            raise HTTPException(401, 'Сеанс сотрудника завершён')
        expires = session.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < utc():
            raise HTTPException(401, 'Сеанс сотрудника истёк')
        user = await db.get(User, session.user_id)
        staff = await db.scalar(select(AdminStaff).where(AdminStaff.user_id == session.user_id))
        if not user or not user.is_active or not staff or not staff.enabled:
            raise HTTPException(403, 'Доступ сотрудника отключён')
        if not scope or not role_allowed(staff.role, scope, request.method):
            raise HTTPException(403, 'Недостаточно полномочий')
        actor = user.id
        actor_kind = 'staff'
        if request.method in _MUTATIONS and not valid_csrf(raw, request.headers.get('x-admin-csrf')):
            raise HTTPException(403, 'Неверный CSRF-токен')
    elif scope is None:
        scope = 'legacy_unclassified'

    request.state.admin_actor_id = actor
    request.state.admin_actor_kind = actor_kind
    # Fail closed: if an audit insert fails, do NOT execute the mutation.
    if request.method in _MUTATIONS:
        try:
            db.add(AdminAuditLog(actor_user_id=actor, actor_kind=actor_kind,
                                 action=request.method, path=request.url.path[:400],
                                 scope=(scope or 'unclassified')))
            await db.commit()
        except SQLAlchemyError as exc:
            raise HTTPException(503, 'Журнал административных операций недоступен') from exc
