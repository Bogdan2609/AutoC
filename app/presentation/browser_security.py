"""Same-origin form security and database-backed browser login.

The opaque login cookie is shared with the existing login_sessions store, never
with localStorage. API Bearer authentication remains unchanged. CSRF is bound to
that opaque session; unauthenticated forms use a signed nonce cookie.
"""

import hashlib
import hmac
import secrets
from datetime import timezone
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.accounts.models import Session, User, utc

AUTH_COOKIE = "ap_browser_session"
CSRF_COOKIE = "ap_csrf_seed"
_DEV_KEY = secrets.token_bytes(32)  # per-process, development only


def secret() -> bytes:
    settings = get_settings()
    value = settings.browser_csrf_secret
    if value and not value.startswith("replace-") and len(value) >= 32:
        return value.encode("utf-8")
    if settings.app_env == "production":
        raise RuntimeError("BROWSER_CSRF_SECRET must be set to a 32+ character secret")
    return _DEV_KEY


def secure_cookie() -> bool:
    settings = get_settings()
    return settings.public_base_url.startswith("https://") or settings.app_env == "production"


def csrf_signature(message: str) -> str:
    return hmac.new(secret(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_for(request: Request, user: User | None) -> tuple[str, str | None]:
    """Return signed hidden form token and optional new anonymous nonce."""
    if user is not None:
        raw = request.cookies.get(AUTH_COOKIE, "")
        return csrf_signature("session:" + hashlib.sha256(raw.encode()).hexdigest()), None
    nonce = request.cookies.get(CSRF_COOKIE, "")
    if len(nonce) != 64 or not all(ch in "0123456789abcdef" for ch in nonce):
        nonce = secrets.token_hex(32)
        return csrf_signature("anonymous:" + nonce), nonce
    return csrf_signature("anonymous:" + nonce), None


def attach_csrf_cookie(response: Response, nonce: str | None) -> Response:
    if nonce:
        response.set_cookie(CSRF_COOKIE, nonce, max_age=3600, httponly=True,
                            secure=secure_cookie(), samesite="lax", path="/")
    return response


def check_csrf(request: Request, submitted: str, user: User | None) -> None:
    expected, nonce = csrf_for(request, user)
    if nonce is not None or len(submitted) != 64 or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Проверка безопасности формы не пройдена")


async def browser_user(request: Request, db) -> User | None:
    raw = request.cookies.get(AUTH_COOKIE, "")
    if len(raw) != 64 or any(ch not in "0123456789abcdef" for ch in raw):
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    token = await db.scalar(select(Session).where(Session.digest == digest, Session.revoked.is_(False)))
    if token is None:
        return None
    expiry = token.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry < utc():
        return None
    user = await db.get(User, token.user_id)
    return user if user is not None and user.is_active else None


def set_login_cookie(response: Response, raw: str) -> None:
    response.set_cookie(AUTH_COOKIE, raw, max_age=14*86400, httponly=True,
                        secure=secure_cookie(), samesite="lax", path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def safe_next(value: str | None, fallback: str = "/account") -> str:
    """Permit only root-relative paths; no protocol-relative URLs or control chars."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return fallback
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return fallback
    decoded_path = unquote(value)
    if decoded_path.startswith("//") or "\\" in decoded_path:
        return fallback
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return fallback
    return value[:500]
