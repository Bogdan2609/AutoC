"""Pure ASGI request limits; no request._body or BaseHTTPMiddleware internals.

For JSON APIs the middleware buffers at most the allowed number of bytes before
handing the request to Starlette, including chunked transfers without length.
Multipart is bounded separately at the reverse proxy and in route validators;
never buffer a 48 MiB supplier feed into ASGI middleware memory.
"""
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app.core.rate_limit import enforce_budget, peer_ip

_JSON_LIMIT = 1024 * 1024
_WEBHOOK_LIMIT = 16 * 1024
_EXPENSIVE_LIMIT = 64 * 1024
_WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
_EXPENSIVE_ROUTES = (
    ('/api/v1/marketplace/products/', '/offers'),
    ('/api/v1/catalog-v021/categories/', '/facets'),
)


def request_policy(path: str, method: str, content_type: str) -> tuple[int, str | None] | None:
    webhook = path == '/api/v1/payments/yookassa/webhook' and method == 'POST'
    expensive = any(path.startswith(prefix) and suffix in path for prefix, suffix in _EXPENSIVE_ROUTES)
    media = content_type.split(';', 1)[0].strip().lower()
    json_write = (path.startswith('/api/') and method in _WRITE_METHODS and
                  (not media or media == 'application/json' or media.endswith('+json')))
    if webhook:
        return _WEBHOOK_LIMIT, 'webhook'
    if expensive:
        return _EXPENSIVE_LIMIT, 'public_costly'
    if json_write:
        return _JSON_LIMIT, None
    return None


class BoundedRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = scope.get('path', ''), scope.get('method', 'GET')
        policy = request_policy(path, method, request.headers.get('content-type', ''))
        if policy is None:
            return await self.app(scope, receive, send)
        limit, budget_scope = policy
        length = request.headers.get('content-length')
        if length is not None and (not length.isdecimal() or int(length) > limit):
            return await JSONResponse({'detail': 'Request body too large'}, status_code=413)(scope, receive, send)
        if budget_scope is not None:
            try:
                await enforce_budget(budget_scope, peer_ip(request),
                                     limit=60 if budget_scope == 'webhook' else 20, period=60)
            except HTTPException as exc:
                return await JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                          headers=exc.headers)(scope, receive, send)
        # Buffer bounded JSON/body request, replay exact ASGI messages, then
        # pass through original receive for http.disconnect after consumption.
        chunks = []
        count = 0
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            if event['type'] != 'http.request':
                return await self.app(scope, receive, send)
            count += len(event.get('body', b''))
            if count > limit:
                return await JSONResponse({'detail': 'Request body too large'}, status_code=413)(scope, receive, send)
            chunks.append(event)
            if not event.get('more_body', False):
                break
        iterator = iter(chunks)

        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()

        return await self.app(scope, replay, send)
