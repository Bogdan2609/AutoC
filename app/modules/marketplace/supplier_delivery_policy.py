"""Fail-closed, supplier-side courier delivery policy.

Supplier -> our receiving warehouse is distinct from customer delivery.
Only ROSSKO has a confirmed external courier method ID in this release.
Unknown provider-specific method IDs MUST NOT be guessed from text or order quotes.
"""
from __future__ import annotations

ROSSKO_COURIER_ID = "000000002"


class CourierPolicyViolation(ValueError):
    """The configured supplier route is not a verified courier delivery."""


def require_rossko_courier(delivery_id: object, address_id: object) -> None:
    """Validate configured ROSSKO supplier-to-merchant shipping, not buyer pickup."""
    if str(delivery_id or '').strip() != ROSSKO_COURIER_ID:
        raise CourierPolicyViolation('ROSSKO supplier purchasing only supports courier delivery 000000002')
    if not str(address_id or '').strip():
        raise CourierPolicyViolation('ROSSKO courier delivery requires a configured address ID')


def require_live_rossko_courier(delivery_id: object, address_id: object,
                                details: dict, payment_id: object | None = None) -> None:
    """Verify the already-configured address against current GetCheckoutDetails.

    This must run before the durable outbound-dispatch marker. Never switch
    delivery methods automatically or invent supplier address identifiers.
    """
    require_rossko_courier(delivery_id, address_id)
    deliveries = details.get('deliveries') or []
    if not any(str(x.get('id')) == ROSSKO_COURIER_ID for x in deliveries):
        raise CourierPolicyViolation('ROSSKO courier delivery is not currently offered')
    addresses = details.get('addresses') or []
    address = next((a for a in addresses if str(a.get('id')) == str(address_id)), None)
    if address is None:
        raise CourierPolicyViolation('Configured ROSSKO courier address was not returned by supplier')
    allowed = address.get('allowed_delivery_ids') or []
    if allowed and ROSSKO_COURIER_ID not in [str(x) for x in allowed]:
        raise CourierPolicyViolation('Configured address is not eligible for ROSSKO courier delivery')
    if payment_id is not None:
        payments = details.get('payments') or []
        if not any(str(x.get('id')) == str(payment_id) for x in payments):
            raise CourierPolicyViolation('Configured ROSSKO supplier payment method is unavailable')
