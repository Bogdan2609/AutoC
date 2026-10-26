"""Configure/read receiving-site courier contact, not end-customer contact.

Run only from authorized local Docker administrative console. Does not touch
supplier account/destination/remote APIs or ever place a supplier order.
Examples:
  python scripts/Configure-ReceivingContact.py status --destination-id UUID
  printf 'Surname Name\n+79990000000\n' | python scripts/Configure-ReceivingContact.py set --destination-id UUID
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import get_session_factory
from app.modules.marketplace.models import FulfillmentNode, SupplierAccount, SupplierDestination
from app.modules.marketplace.receiving_contact import (
    ReceivingContactError, validate_receiving_contact, receiving_contact_for_node,
)


async def run(command: str, destination_id: str) -> int:
    async with get_session_factory()() as db:
        destination=await db.get(SupplierDestination,UUID(destination_id))
        if destination is None:
            print('NOT_FOUND: supplier destination')
            return 2
        account=await db.get(SupplierAccount,destination.account_id)
        node=await db.get(FulfillmentNode,destination.node_id)
        if not account or not node or node.merchant_id!=account.merchant_id:
            print('BLOCKED: receiving node does not belong to account merchant')
            return 2
        if command=='status':
            try:
                receiving_contact_for_node(node)
                print(f'RECEIVING_CONTACT_CONFIGURED=True NODE_ID={node.id} SOURCE=fulfillment_nodes')
            except ReceivingContactError:
                print(f'RECEIVING_CONTACT_CONFIGURED=False NODE_ID={node.id} SOURCE=fulfillment_nodes')
            print('READ_ONLY=True NO_SUPPLIER_ORDER_SENT=True')
            return 0
        # CLI is an operator-only administrative console. Never accept buyer order IDs.
        name = sys.stdin.readline().strip()
        phone = sys.stdin.readline().strip()
        comment = sys.stdin.readline().rstrip('\r\n')
        try:
            receiver=validate_receiving_contact(name,phone,comment)
        except ReceivingContactError as exc:
            print('BLOCKED: '+str(exc)+'; NO CHANGES SAVED')
            return 2
        # Lock the actual receiving node so concurrent operator updates serialize.
        from sqlalchemy import select
        node=await db.scalar(select(FulfillmentNode).where(
            FulfillmentNode.id==node.id).with_for_update())
        if not node or not node.active or node.merchant_id!=account.merchant_id:
            print('BLOCKED: receiving node changed or inactive')
            return 2
        changed=(node.receiving_contact_name != receiver.name or
                 node.receiving_contact_phone != receiver.phone or
                 node.receiving_contact_comment != receiver.comment)
        node.receiving_contact_name=receiver.name
        node.receiving_contact_phone=receiver.phone
        node.receiving_contact_comment=receiver.comment
        await db.commit()
        print(f'RECEIVING_CONTACT_SAVED=True NODE_ID={node.id} CHANGED={changed}')
        print('SOURCE=fulfillment_nodes NO_SUPPLIER_ORDER_SENT=True CONTACT_DETAILS_PRINTED=False')
        return 0


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','set'])
    parser.add_argument('--destination-id',required=True)
    args=parser.parse_args()
    try:
        return asyncio.run(run(args.command,args.destination_id))
    except (ValueError,TypeError):
        print('BLOCKED: invalid destination or recipient; no supplier order sent')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
