"""Request-local, short-lived marketplace quote query reuse.

No Redis, no globals holding data beyond the explicit evaluation scope.
A product/pickup lookup is reused only while evaluating one cart revision.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_CACHE = ContextVar('autoc_marketplace_quote_preview_cache_v0421', default=None)


@contextmanager
def preview_scope():
    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


async def read_quoted_marketplace(product_id, pickup_node_id, loader):
    cache = _CACHE.get()
    if cache is None:
        return await loader()
    key = (product_id, pickup_node_id)
    if key not in cache:
        cache[key] = await loader()
    return cache[key]
