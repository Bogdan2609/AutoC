"""The single write boundary for payment-backed replacement refunds.

Lock the parent payment to serialize concurrent refunds of different lines.
A pending/uncertain refund still consumes its portion of the paid amount: the
provider may have executed it even when its acknowledgement was lost.
"""
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select

from app.modules.commerce.models import Job
from app.modules.payments.models import Payment, Refund


async def queue_replacement_refund(db, payment_id, replacement_id, amount):
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if not payment or payment.status != 'succeeded' or not payment.provider_id:
        raise HTTPException(409, 'Verified settled SBP payment required for refund')
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    if amount <= 0 or amount > payment.amount:
        raise HTTPException(409, 'Refund amount is invalid')
    refunds = (await db.scalars(select(Refund).where(Refund.payment_id == payment.id))).all()
    if any(x.replacement_id == replacement_id for x in refunds):
        raise HTTPException(409, 'Replacement refund was already requested')
    committed = sum((Decimal(str(x.amount)) for x in refunds
                     if x.status != 'canceled'), Decimal('0.00'))
    if committed + amount > payment.amount:
        raise HTTPException(409, 'Refunds would exceed the confirmed SBP payment')
    refund = Refund(payment_id=payment.id, replacement_id=replacement_id, amount=amount)
    db.add(refund)
    await db.flush()
    db.add(Job(dedup_key=f'refund:{refund.id}', kind='refund',
               payload={'id': str(refund.id)}))
    return refund
