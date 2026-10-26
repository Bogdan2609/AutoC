"""Explicit supplier order READ transports. Never sends an order or cancellation.

Allowlisted HTTPS hosts, no redirects, no inherited environment proxies, bounded
streaming bodies. Actual provider credentials are never returned or logged.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET
from decimal import Decimal
import json
from urllib.parse import quote

import httpx

from .order_readback import ReadbackError, validate_reference

LIMIT = 1024 * 1024


async def _read_bounded(http: httpx.AsyncClient, method: str, url: str, **kwargs) -> bytes:
    chunks=[]
    size=0
    async with http.stream(method,url,**kwargs) as response:
        if response.status_code != 200:
            raise ReadbackError('supplier_read_failed')
        value=response.headers.get('content-length')
        if value is not None and (not value.isascii() or not value.isdecimal() or int(value)>LIMIT):
            raise ReadbackError('supplier_response_too_large')
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size>LIMIT:
                raise ReadbackError('supplier_response_too_large')
            chunks.append(chunk)
    return b''.join(chunks)


def _json(data: bytes):
    try:
        return json.loads(data,parse_float=Decimal)
    except (ValueError,UnicodeError):
        raise ReadbackError('invalid_supplier_json') from None


async def read_external_order(code: str, config, reference: str):
    ref=validate_reference(code,reference)
    timeout=httpx.Timeout(20,connect=4)
    if code=='rossko':
        # SOAP GetOrders exact IDs; not a bounded recent-orders page.
        from zeep import AsyncClient
        from zeep.transports import AsyncTransport
        from zeep.helpers import serialize_object
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,
                                     trust_env=False) as http:
            with httpx.Client(timeout=httpx.Timeout(8,connect=4),
                              follow_redirects=False,trust_env=False) as wsdl:
                service=AsyncClient('https://api.rossko.ru/service/v2.1/GetOrders?wsdl',
                                    transport=AsyncTransport(client=http,wsdl_client=wsdl))
                raw=await service.service.GetOrders(KEY1=config.rossko_key1,
                       KEY2=config.rossko_key2,order_ids={'id':[int(ref)]})
        return serialize_object(raw,target_cls=dict)
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,
                                 trust_env=False) as http:
        if code=='adeo':
            root=ET.Element('message');param=ET.SubElement(root,'param')
            for key,value in [('action','getOrderStatus'),('login',config.adeo_login),
                              ('password',config.adeo_password),('id',ref)]:
                ET.SubElement(param,key).text=value
            return await _read_bounded(http,'POST','https://xml.adeo.pro/pricedetals2.php',
                        data={'xml':ET.tostring(root,encoding='unicode')})
        if code=='partkom':
            raw=await _read_bounded(http,'GET',
                'https://ws.part-kom.ru/v4/basket/motion/'+quote(ref,safe=''),
                auth=(config.partkom_login,config.partkom_password))
            return _json(raw)
        if code=='forum-auto':
            # v2 documents credentials in GET query. No URL/errors leave this module.
            raw=await _read_bounded(http,'GET','https://api.forum-auto.ru/v2/listorders',
                params={'login':config.forum_login,'pass':config.forum_password,'did':ref})
            return _json(raw)
    raise ReadbackError('unsupported_supplier')
