"""Live supplier READ transports and single adapter registry.

Response normalization lives in parsers.py and is testable without HTTP.
Write operations (GetCheckout, make_orders, /basket/order, addGoodsToOrder)
remain exclusively at the durable marketplace.procurement boundary.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import httpx

from app.core.config import get_settings
from app.modules.catalog.normalize import normalize_article, normalize_brand
from app.modules.suppliers.adapters.base import ReadAdapter
from .contracts import Offer
from .currency import require_rub, rub_amount
from .http_pool import get_client
from .parsers import (
    MAX_RESPONSE_BYTES, as_int, parse_adeo_stock, parse_adeo_xml,
    parse_forum_rows, parse_json_body, parse_partkom_rows,
    parse_rossko_search_result,
)
from .resilience import classify_http, protected_search
from .supplier_errors import (
    MalformedResponseError, NetworkError, Unavailable, UnsupportedCurrencyError,
    UpstreamBusinessError,
)


def dec(value):
    return rub_amount(value)


def exact(offer, brand, article):
    return (normalize_brand(offer.brand) == normalize_brand(brand)
            and normalize_article(offer.article) == normalize_article(article))


@protected_search
class PartKom(ReadAdapter):
    code = 'partkom'

    async def search(self, brand, article, config=None):
        s = config or get_settings()
        if not (s.supplier_api_enabled and s.partkom_login and s.partkom_password):
            raise Unavailable('PartKom account not configured')
        response = await get_client().get(
            'https://ws.part-kom.ru/v4/search/offers',
            params={'number': article, 'find_substitutes': 0, 'store': 0},
            auth=(s.partkom_login, s.partkom_password),
            timeout=httpx.Timeout(14, connect=3),
        )
        classify_http(response)
        return parse_partkom_rows(parse_json_body(response.content), brand, article)


@protected_search
class Forum(ReadAdapter):
    code = 'forum-auto'

    async def search(self, brand, article, config=None):
        s = config or get_settings()
        if not (s.supplier_api_enabled and s.forum_login and s.forum_password):
            raise Unavailable('Forum-Auto account not configured')
        # Supplier's documented REST v2 puts credentials in query. Do not log
        # full request URLs/exception text and never follow redirects.
        response = await get_client().get(
            'https://api.forum-auto.ru/v2/listgoods',
            params={'login': s.forum_login, 'pass': s.forum_password,
                    'art': article, 'br': brand, 'cross': 0},
            timeout=httpx.Timeout(14, connect=3),
        )
        classify_http(response)
        return parse_forum_rows(parse_json_body(response.content), brand, article)


@protected_search
class Adeo(ReadAdapter):
    code = 'adeo'

    async def search(self, brand, article, config=None):
        s = config or get_settings()
        if not (s.supplier_api_enabled and s.adeo_login and s.adeo_password):
            raise Unavailable('ADEO account not configured')
        root = ET.Element('message')
        param = ET.SubElement(root, 'param')
        for tag, value in (
            ('action', 'price'), ('login', s.adeo_login),
            ('password', s.adeo_password), ('code', article),
            ('brand', brand), ('crosses', 'disallow'),
        ):
            ET.SubElement(param, tag).text = value
        response = await get_client().post(
            'https://xml.adeo.pro/pricedetals2.php',
            data={'xml': ET.tostring(root, encoding='unicode')},
            timeout=httpx.Timeout(20, connect=3),
        )
        classify_http(response)
        return parse_adeo_xml(response.content, brand, article)


@protected_search
class Rossko(ReadAdapter):
    code = 'rossko'

    async def search(self, brand, article, config=None):
        s = config or get_settings()
        if not (s.supplier_api_enabled and s.rossko_key1 and s.rossko_key2
                and s.rossko_delivery_id):
            raise Unavailable('ROSSKO SOAP credentials/delivery not configured')
        from zeep.helpers import serialize_object
        from zeep.exceptions import Fault
        from .soap_client import rossko_client

        try:
            client = rossko_client('https://api.rossko.ru/service/v2.1/GetSearch?wsdl')
        except Exception:
            raise NetworkError('ROSSKO WSDL unavailable') from None
        params = {
            'KEY1': s.rossko_key1, 'KEY2': s.rossko_key2,
            'text': f'{brand} {article}', 'delivery_id': s.rossko_delivery_id,
        }
        if s.rossko_address_id:
            params['address_id'] = s.rossko_address_id
        try:
            raw = await client.service.GetSearch(**params)
        except Fault:
            raise UpstreamBusinessError('ROSSKO SOAP business fault') from None
        except Exception:
            raise NetworkError('ROSSKO SOAP GetSearch failed') from None
        try:
            obj = serialize_object(raw, target_cls=dict)
        except Exception:
            raise MalformedResponseError('ROSSKO SOAP response could not be normalized') from None
        return parse_rossko_search_result(obj, brand, article)


# The registry is the sole live READ registry; aliases under suppliers/adapters
# point to these classes, rather than implementing a second supplier stack.
PROVIDERS = {
    'partkom': PartKom(), 'forum-auto': Forum(), 'adeo': Adeo(), 'rossko': Rossko(),
}


def get_adapter(supplier_code: str):
    try:
        return PROVIDERS[supplier_code]
    except KeyError:
        raise UpstreamBusinessError('Unknown supplier code') from None


async def verify(code, key, brand, article, quantity):
    adapter = get_adapter(code)
    # Backwards compatibility with test doubles and external integrations that
    # implement only search(). Production adapters expose check_stock().
    if isinstance(adapter, ReadAdapter):
        try:
            return await adapter.check_stock(key, brand, article, quantity)
        except UpstreamBusinessError:
            raise Unavailable('Selected offer unavailable or quantity invalid') from None
    rows = await adapter.search(brand, article)
    selected = next((o for o in rows if o.key == key and exact(o, brand, article)), None)
    if (selected is None or selected.qty is None or selected.qty < quantity
            or quantity < selected.minimum or quantity % selected.multiple):
        raise Unavailable('Selected offer unavailable or quantity invalid')
    return selected
