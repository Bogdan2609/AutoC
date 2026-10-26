"""Append-only operator/customer workflow events; no changes to payments."""
from alembic import op
import sqlalchemy as sa

revision = '20261022_0027'
down_revision = '20261022_0026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('order_workflow_events',
        sa.Column('id',sa.Uuid(as_uuid=True),primary_key=True),
        sa.Column('order_id',sa.Uuid(as_uuid=True),
                  sa.ForeignKey('customer_orders.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('event_type',sa.String(50),nullable=False),
        sa.Column('actor_type',sa.String(20),nullable=False),
        sa.Column('evidence',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_order_workflow_event_order_created','order_workflow_events',
                    ['order_id','created_at'])


def downgrade():
    # Deliberately preserve financial/audit evidence: explicit backup and
    # manual archival are required before a destructive downgrade.
    raise RuntimeError('Order workflow events are append-only; destructive downgrade disabled')
