"""Require durable buyer-order linkage for new supplier purchase attempts.

Historic rows are intentionally retained with NULL order_line_id, but no longer
eligible for dispatch. Old attempts need manual review, never auto-filling.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261022_0029'
down_revision = '20261022_0028'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('supplier_purchase_attempts', sa.Column('order_line_id', sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_purchase_order_line', 'supplier_purchase_attempts',
                          'order_lines', ['order_line_id'], ['id'], ondelete='RESTRICT')
    op.create_unique_constraint('uq_purchase_attempt_order_line', 'supplier_purchase_attempts', ['order_line_id'])


def downgrade():
    raise RuntimeError('Purchase-to-order audit link must not be removed by automatic downgrade')
