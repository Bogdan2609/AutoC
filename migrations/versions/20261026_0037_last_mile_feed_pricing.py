"""Persist reviewed last-mile landed cost; revoke approvals requiring re-review.

Existing rules had no destination-to-customer delivery cost field, so their
approval cannot safely be carried forward. No automated price publication.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261026_0037'
down_revision = '20261026_0036'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('feed_price_rules', sa.Column('last_mile_delivery',sa.Numeric(14,2),
        nullable=False, server_default=sa.text('0')))
    # Required: after schema change historical approvals must be reviewed again.
    op.execute('UPDATE feed_price_rules SET approved = false')
    op.drop_constraint('feed_price_nonnegative_costs','feed_price_rules',type_='check')
    op.create_check_constraint('feed_price_nonnegative_costs','feed_price_rules',
        'supplier_delivery >= 0 AND last_mile_delivery >= 0 AND packaging >= 0 '
        'AND other_costs >= 0 AND tax_reserve >= 0 AND sbp_fixed >= 0 AND min_contribution >= 0')


def downgrade():
    op.drop_constraint('feed_price_nonnegative_costs','feed_price_rules',type_='check')
    op.create_check_constraint('feed_price_nonnegative_costs','feed_price_rules',
        'supplier_delivery >= 0 AND packaging >= 0 AND other_costs >= 0 '
        'AND tax_reserve >= 0 AND sbp_fixed >= 0 AND min_contribution >= 0')
    op.drop_column('feed_price_rules','last_mile_delivery')
    # An automatic rollback cannot reinstate historically revoked approvals.
