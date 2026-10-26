"""Explicit feed price policies and resumable source-scoped catalog sync."""
from alembic import op
import sqlalchemy as sa

revision='20261022_0025'
down_revision='20261021_0024'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('feed_price_rules',
       sa.Column('id',sa.Uuid(),primary_key=True),
       sa.Column('supplier_code',sa.String(32),sa.ForeignKey('feed_sources.code',ondelete='RESTRICT'),nullable=False),
       sa.Column('account_id',sa.Uuid(),sa.ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=False),
       sa.Column('destination_id',sa.Uuid(),sa.ForeignKey('supplier_destinations.id',ondelete='RESTRICT'),nullable=False),
       sa.Column('warehouse_code',sa.String(140),nullable=False),
       sa.Column('version',sa.Integer(),nullable=False),sa.Column('approved',sa.Boolean(),nullable=False),
       sa.Column('tax_status_verified',sa.String(16),nullable=False),
       sa.Column('markup_percent',sa.Numeric(8,2),nullable=False),
       sa.Column('supplier_delivery',sa.Numeric(14,2),nullable=False),
       sa.Column('packaging',sa.Numeric(14,2),nullable=False),
       sa.Column('other_costs',sa.Numeric(14,2),nullable=False),
       sa.Column('tax_reserve',sa.Numeric(14,2),nullable=False),
       sa.Column('sbp_percent',sa.Numeric(7,3),nullable=False),
       sa.Column('sbp_fixed',sa.Numeric(14,2),nullable=False),
       sa.Column('min_contribution',sa.Numeric(14,2),nullable=False),
       sa.Column('evidence_reference',sa.String(300),nullable=False),
       sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
       sa.UniqueConstraint('supplier_code','account_id','destination_id','warehouse_code',name='uq_feed_price_scope'),
       sa.CheckConstraint('markup_percent >= 0 AND markup_percent <= 1000 AND sbp_percent >= 0 AND sbp_percent < 100',name='feed_price_percentage_bounds'),
       sa.CheckConstraint('supplier_delivery >= 0 AND packaging >= 0 AND other_costs >= 0 AND tax_reserve >= 0 AND sbp_fixed >= 0 AND min_contribution >= 0',name='feed_price_nonnegative_costs'))
    op.create_table('feed_catalog_syncs',
       sa.Column('id',sa.Uuid(),primary_key=True),
       sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False,unique=True),
       sa.Column('status',sa.String(16),nullable=False),sa.Column('stage',sa.String(20),nullable=False),
       sa.Column('cursor',sa.String(36)),sa.Column('scanned',sa.Integer(),nullable=False),
       sa.Column('created_drafts',sa.Integer(),nullable=False),sa.Column('reviewed',sa.Integer(),nullable=False),
       sa.Column('updated_proposals',sa.Integer(),nullable=False),sa.Column('linked',sa.Integer(),nullable=False),
       sa.Column('step',sa.Integer(),nullable=False),sa.Column('taxonomy_revision',sa.Integer(),nullable=False),
       sa.Column('message',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
       sa.Column('finished_at',sa.DateTime(timezone=True)),
       sa.CheckConstraint("status IN ('queued','processing','completed','failed','paused')",name='feed_sync_status'))


def downgrade():
    op.drop_table('feed_catalog_syncs')
    op.drop_table('feed_price_rules')
