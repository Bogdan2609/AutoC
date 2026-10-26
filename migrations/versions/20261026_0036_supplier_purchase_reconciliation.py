"""Supplier order read-back verification and read-only reconciliation lease.

No new automatically enabled supplier write path. Historic 'accepted' entries
remain unverified until an operator runs GetOrders reconciliation.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261026_0036'
down_revision = '20261023_0035'
branch_labels = None
depends_on = None


OLD = "status IN ('prepared','validating','dispatch_unknown','accepted','rejected','manual_review')"
NEW = ("status IN ('prepared','validating','dispatch_unknown','accepted',"
       "'accepted_unverified','reconciling','verified','rejected','manual_review')")


def upgrade():
    op.drop_constraint('ck_purchase_attempt_validating', 'supplier_purchase_attempts', type_='check')
    op.create_check_constraint('ck_purchase_attempt_validating', 'supplier_purchase_attempts', NEW)
    for name in ('reconcile_started_at', 'last_reconciled_at', 'verified_at',
                 'cancellation_requested_at'):
        op.add_column('supplier_purchase_attempts',
                      sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column('supplier_purchase_attempts',
                  sa.Column('reconcile_token', sa.String(36), nullable=True))


def downgrade():
    # Cannot downgrade while new states are used: never silently corrupt history.
    bind = op.get_bind()
    count = bind.scalar(sa.text("SELECT COUNT(*) FROM supplier_purchase_attempts WHERE status IN "
        "('accepted_unverified', 'reconciling', 'verified')"))
    if count:
        raise RuntimeError('Reconcile v0.35 purchase states before downgrading')
    for name in ('reconcile_token', 'cancellation_requested_at', 'verified_at',
                 'last_reconciled_at', 'reconcile_started_at'):
        op.drop_column('supplier_purchase_attempts', name)
    op.drop_constraint('ck_purchase_attempt_validating', 'supplier_purchase_attempts', type_='check')
    op.create_check_constraint('ck_purchase_attempt_validating', 'supplier_purchase_attempts', OLD)
