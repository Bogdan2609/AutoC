"""One successful SBP payment per order: a database invariant.

If historical duplicates exist, PostgreSQL refuses migration. Resolve such
payments with the acquiring bank before deployment; never discard evidence.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261022_0030'
down_revision = '20261022_0029'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('uq_successful_sbp_payment_per_order', 'sbp_payments', ['order_id'],
                    unique=True, postgresql_where=sa.text("status = 'succeeded'"),
                    sqlite_where=sa.text("status = 'succeeded'"))


def downgrade():
    raise RuntimeError('Do not remove payment uniqueness without financial data review')
