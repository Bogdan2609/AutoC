"""Per-event-loop supplier HTTP pooling; no account credentials on the client.

The connection pool contains only transports. Authentication is attached to each
request, so SupplierAccount A never becomes the default auth for account B.
"""
import asyncio
from weakref import WeakKeyDictionary
import httpx

_clients = WeakKeyDictionary()


def get_client():
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            follow_redirects=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=12,
                                keepalive_expiry=15),
            timeout=httpx.Timeout(20, connect=3))
        _clients[loop] = client
    return client


async def close_clients():
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.aclose()
