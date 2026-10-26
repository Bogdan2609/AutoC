"""Authoritative supplier-delivery recipient: the merchant's receiving node.

Never derive supplier-order contact details from Order, OrderLine, Cart, User,
or operator-supplied checkout parameters. Contacts are site operational data.
"""
import re
from dataclasses import dataclass


class ReceivingContactError(ValueError):
    """No safe recipient contact has been configured on the receiving site."""


@dataclass(frozen=True)
class ReceivingContact:
    name: str
    phone: str
    comment: str = ''


def normalize_receiving_comment(comment: str | None) -> str:
    """Operational warehouse note, optional; never copied from the buyer."""
    if comment is None:
        return ''
    if not isinstance(comment, str) or len(comment) > 500 or any(ord(c) < 32 and c not in '\t\n' for c in comment):
        raise ReceivingContactError('Invalid receiving node contact comment')
    return comment.strip()


def validate_receiving_contact(name: str | None, phone: str | None, comment: str | None = '') -> ReceivingContact:
    """Normalize a warehouse/PVZ recipient; no silent customer fallback."""
    name = ' '.join((name or '').split())
    words = name.split() if name else []
    if not (5 <= len(name) <= 160 and 2 <= len(words) <= 4 and
            all(len(part) >= 2 and part.replace('-', '').isalpha() for part in words)):
        raise ReceivingContactError('Receiving node contact name must contain surname and given name')
    raw = (phone or '').strip()
    if not re.fullmatch(r'[+\d\s()\-]+', raw):
        raise ReceivingContactError('Invalid receiving node contact phone format')
    digits = ''.join(ch for ch in raw if ch.isascii() and ch.isdecimal())
    if len(digits) == 11 and digits[0] in '78':
        normalized_phone = '+7' + digits[1:]
    elif raw.startswith('+') and 8 <= len(digits) <= 15 and digits[0] != '0':
        normalized_phone = '+' + digits
    else:
        raise ReceivingContactError('Receiving node phone must be a valid international number')
    return ReceivingContact(name=name, phone=normalized_phone, comment=normalize_receiving_comment(comment))


def receiving_contact_for_node(node) -> ReceivingContact:
    """Returns the supplier-delivery recipient stored on a warehouse/PVZ."""
    if node is None or not getattr(node, 'active', False):
        raise ReceivingContactError('Receiving node is missing or disabled')
    return validate_receiving_contact(
        getattr(node, 'receiving_contact_name', None),
        getattr(node, 'receiving_contact_phone', None),
        getattr(node, 'receiving_contact_comment', ''),
    )


async def receiving_contact_for_destination_ids(account_id: str, destination_id: str) -> ReceivingContact:
    """For isolated supplier test utilities; reads configured receiving site only."""
    from uuid import UUID
    from app.core.db import get_session_factory
    from app.modules.marketplace.models import SupplierAccount, SupplierDestination, FulfillmentNode

    async with get_session_factory()() as db:
        destination = await db.get(SupplierDestination, UUID(destination_id))
        if not destination or destination.account_id != UUID(account_id):
            raise ReceivingContactError('Supplier destination/account mismatch')
        account = await db.get(SupplierAccount, destination.account_id)
        node = await db.get(FulfillmentNode, destination.node_id)
        if not account or not node or node.merchant_id != account.merchant_id:
            raise ReceivingContactError('Receiving site is not owned by the supplier merchant')
        if not destination.enabled or not destination.verified:
            raise ReceivingContactError('Supplier receiving destination is inactive or unverified')
        return receiving_contact_for_node(node)
