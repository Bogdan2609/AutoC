"""Canonical ROSSKO GetCheckout payload; no I/O, no customer PII.

A supplier shipment is addressed to our configured receiving warehouse/PVZ.
PARTS.Part.comment must always be the internal customer order UUID, never the
supplier order number, order line ID, attempt ID or buyer-provided comment.
"""
from __future__ import annotations

from uuid import UUID

from app.modules.marketplace.receiving_contact import (
    ReceivingContact, validate_receiving_contact,
)
from app.modules.marketplace.rossko_requisites import make_payment
from app.modules.marketplace.supplier_delivery_policy import require_rossko_courier


def build_rossko_checkout_payload(
    *, keys: tuple[str, str], delivery_id: str, address_id: str,
    payment_id: str, requisite_id: str, receiving_contact: ReceivingContact,
    parts: list[dict], order_uuid: UUID | str,
    delivery_parts: bool = False,
) -> dict:
    """Build a validated, account/destination-specific supplier order request.

    This function deliberately has no customer/contact input; warehouse contact
    must have been fetched from the authorized FulfillmentNode beforehand.
    """
    require_rossko_courier(delivery_id, address_id)
    if len(keys) != 2 or not all(keys) or not parts:
        raise ValueError('ROSSKO requires supplier credentials and at least one line')
    try:
        order_ref = str(UUID(str(order_uuid)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('A valid internal customer order UUID is required') from exc
    # Validate even when caller constructed ReceivingContact directly.
    recipient = validate_receiving_contact(receiving_contact.name,
                                            receiving_contact.phone,
                                            receiving_contact.comment)
    items = []
    for part in parts:
        allowed = {'partnumber', 'brand', 'stock', 'count'}
        if not isinstance(part, dict) or not allowed <= part.keys():
            raise ValueError('Incomplete ROSSKO line')
        try:
            qty = int(part['count'])
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid ROSSKO line quantity') from exc
        if qty <= 0 or str(part['count']) != str(qty) or not all(str(part[k]).strip() for k in ('partnumber','brand','stock')):
            raise ValueError('Invalid ROSSKO part identity or count')
        # Explicitly override any upstream comment, including buyer comments.
        items.append({k: part[k] for k in ('partnumber','brand','stock')} |
                     {'count': qty, 'comment': order_ref})
    return {
        'KEY1': keys[0], 'KEY2': keys[1],
        'delivery': {'delivery_id': delivery_id, 'address_id': address_id},
        'payment': make_payment(payment_id, requisite_id),
        'contact': {'name': recipient.name, 'phone': recipient.phone,
                    'comment': recipient.comment},
        'delivery_parts': bool(delivery_parts),
        'PARTS': {'Part': items},
    }
