"""HttpOnly employee sessions and synchronizer token CSRF."""
import hashlib
import hmac

from app.presentation.browser_security import secret, secure_cookie

ADMIN_COOKIE = 'ap_admin_session'
ADMIN_SESSION_HOURS = 8


def csrf_token(raw: str) -> str:
    return hmac.new(secret(), ('admin:' + raw).encode(), hashlib.sha256).hexdigest()


def valid_csrf(raw: str, submitted: str | None) -> bool:
    return bool(submitted and len(submitted) == 64 and
                hmac.compare_digest(csrf_token(raw), submitted))


def set_admin_cookie(response, raw: str):
    response.set_cookie(ADMIN_COOKIE, raw, max_age=ADMIN_SESSION_HOURS*3600,
                        httponly=True, secure=secure_cookie(), samesite='strict', path='/')


def clear_admin_cookie(response):
    response.delete_cookie(ADMIN_COOKIE, path='/', samesite='strict', secure=secure_cookie())
