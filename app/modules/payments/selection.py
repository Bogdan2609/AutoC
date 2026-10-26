"""Deterministic payment attempt selection for a buyer order."""
from sqlalchemy import case, select
from app.modules.payments.models import Payment


def selected_payment_stmt(order_id):
    # Terminal success always wins, then ongoing reconciliation, then latest canceled.
    # A separate unique partial index still enforces at most one succeeded payment.
    rank = case(
        (Payment.status == 'succeeded', 0),
        (Payment.status.in_(('pending', 'waiting_for_capture', 'creating',
                             'reconciliation_required')), 1),
        else_=2,
    )
    return (select(Payment).where(Payment.order_id == order_id)
            .order_by(rank, Payment.created_at.desc(), Payment.id.desc()))
