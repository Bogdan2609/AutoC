"""Reusable Zeep WSDL client per event loop and WSDL version.

The synchronous WSDL fetch happens only once per event loop/version. No account
credentials are stored on cached transport/client: always send them per request.
"""
import asyncio
from weakref import WeakKeyDictionary
import httpx

_cached = WeakKeyDictionary()
_sync_http_clients = []

def rossko_client(wsdl: str):
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from app.modules.offer_engine.http_pool import get_client
    loop = asyncio.get_running_loop()
    clients = _cached.setdefault(loop, {})
    if wsdl not in clients:
        wsdl_http = httpx.Client(timeout=httpx.Timeout(6, connect=3))
        _sync_http_clients.append(wsdl_http)
        clients[wsdl] = AsyncClient(wsdl, transport=AsyncTransport(
            client=get_client(), wsdl_client=wsdl_http))
    return clients[wsdl]

async def close_soap_clients():
    _cached.clear()
    for client in _sync_http_clients:
        client.close()
    _sync_http_clients.clear()
