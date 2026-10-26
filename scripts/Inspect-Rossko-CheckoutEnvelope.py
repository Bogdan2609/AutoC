"""Safe ROSSKO GetCheckout SOAP envelope inspection. NO GetCheckout network call.

Reads an existing supplier destination, obtains GetCheckoutDetails (READ ONLY),
fetches public GetCheckout WSDL and asks Zeep to serialize synthetic values.
It prints only field-presence checks; never logs an XML envelope or credentials.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DUMMY_CONTACT_NAME = 'TEST CONTACT'
DUMMY_CONTACT_PHONE = '+79990000000'
WSDL_URL = 'https://api.rossko.ru/service/v2.1/GetCheckout?wsdl'


def children_matching(parent: Any, wanted: str) -> list:
    """Direct XML children matched by localname, with no namespace assumptions."""
    from lxml import etree
    return [node for node in parent if isinstance(node.tag, str) and
            etree.QName(node).localname == wanted]


def soap_field_presence(message: Any) -> dict[str, bool | int | str]:
    """Inspect serialized XML, never exposing values of keys or contact data."""
    from lxml import etree
    nodes = message.xpath("//*[local-name()='GetCheckout']")
    if len(nodes) != 1:
        return {'operation_node_count': len(nodes), 'valid': False}
    operation = nodes[0]
    def field(container: str, member: str, expected: str | None = None) -> bool:
        parents = children_matching(operation, container)
        if len(parents) != 1:
            return False
        matches = children_matching(parents[0], member)
        return len(matches) == 1 and (expected is None or (matches[0].text or '') == expected)
    parts = children_matching(operation, 'PARTS')
    part_count = 0
    if len(parts) == 1:
        part_count = len(children_matching(parts[0], 'Part'))
    result = {
        'operation_node_count': len(nodes),
        'contact_container_present': len(children_matching(operation, 'contact')) == 1,
        'contact_name_serialized': field('contact', 'name', DUMMY_CONTACT_NAME),
        'contact_phone_serialized': field('contact', 'phone', DUMMY_CONTACT_PHONE),
        'delivery_id_present': field('delivery', 'delivery_id'),
        'courier_address_present': field('delivery', 'address_id'),
        'payment_id_present': field('payment', 'payment_id'),
        'requisite_id_present': field('payment', 'requisite_id'),
        'delivery_parts_present': len(children_matching(operation, 'delivery_parts')) == 1,
        'parts_count': part_count,
    }
    result['valid'] = all(x for key, x in result.items() if key.endswith('_present') or key.endswith('_serialized')) and part_count == 1
    return result


async def inspect(account_id: str, destination_id: str) -> int:
    # Imports remain inside the safe entrypoint: xml self-tests need no app or SOAP dependencies.
    import httpx
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from scripts.rossko_test_checkout_lab import context
    from app.modules.marketplace.account_probe import live_rossko_details, normalize_checkout_details
    from app.modules.marketplace.rossko_requisites import resolve_live_requisite, make_payment
    from app.modules.marketplace.supplier_delivery_policy import require_live_rossko_courier

    cfg, delivery_id, address_id = await context(account_id, destination_id)
    checkout_details = await live_rossko_details(cfg) # ONLY supplier read
    details = normalize_checkout_details(checkout_details)
    require_live_rossko_courier(delivery_id, address_id, details, payment_id='1')
    requisite = resolve_live_requisite(checkout_details, getattr(cfg, 'rossko_requisite_id', None))

    # Crucially, substitute fake authentication AND fake contact values here. No
    # personal or secret data enters the serialization test.
    synthetic_request = {
        'KEY1': 'SYNTHETIC_NOT_AN_API_KEY_1',
        'KEY2': 'SYNTHETIC_NOT_AN_API_KEY_2',
        'delivery': {'delivery_id': delivery_id, 'address_id': address_id},
        'payment': make_payment('1', requisite),
        'contact': {'name': DUMMY_CONTACT_NAME, 'phone': DUMMY_CONTACT_PHONE,
                    'comment': 'LOCAL XML SERIALIZATION CHECK ONLY'},
        'delivery_parts': False,
        'PARTS': {'Part': [{'partnumber': 'CF 705', 'brand': 'Trialli',
                            'stock': 'HST282827366', 'count': 1}]},
    }

    # WSDL GET is allowed. create_message does not call GetCheckout or send
    # the XML to ROSSKO. There are NO supplier-writing calls in this file.
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=4)) as http:
        with httpx.Client(timeout=httpx.Timeout(12, connect=4)) as wsdl_http:
            soap = AsyncClient(WSDL_URL, transport=AsyncTransport(client=http, wsdl_client=wsdl_http))
            outgoing_xml = soap.create_message(soap.service, 'GetCheckout', **synthetic_request)

    fields = soap_field_presence(outgoing_xml)
    print('READ_ONLY=True GETCHECKOUT_CALLED=False WSDL_READ_ONLY=True')
    for key, value in fields.items():
        print(f'{key.upper()}={value}')
    print('SECRETS_OR_CONTACT_DETAILS_PRINTED=False')
    print('NOTE: XML serialization success alone does not prove supplier acceptance.')
    return 0 if fields.get('valid') is True else 3


def main() -> int:
    parser = argparse.ArgumentParser(description='Inspect ROSSKO SOAP envelope WITHOUT dispatch')
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--destination-id', required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(inspect(args.account_id, args.destination_id))
    except Exception as exc:
        # Never print external SOAP, URLs, secrets or personal data in exceptions.
        print(f'SAFE_INSPECTION_FAILED={type(exc).__name__} NO_ORDER_SENT=True')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
