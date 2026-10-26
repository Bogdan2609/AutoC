"""Durable unified fulfilment state per paid order line.

Revision ID: 20261022_0028
Revises: 20261022_0027
"""
from alembic import op
import sqlalchemy as sa

revision = '20261022_0028'
down_revision = '20261022_0027'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sale_line_executions',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('order_line_id', sa.Uuid(as_uuid=True), sa.ForeignKey('order_lines.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('source_kind', sa.String(24), nullable=False),
        sa.Column('status', sa.String(40), nullable=False),
        sa.Column('fulfillment_reference', sa.String(200), nullable=True),
        sa.Column('operator_evidence', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('order_line_id', name='uq_sale_execution_order_line'),
        sa.CheckConstraint("source_kind IN ('supplier','own_stock')", name='sale_execution_source'))
    op.create_index('ix_sale_execution_status', 'sale_line_executions', ['status'])


def downgrade():
    raise RuntimeError('Sales fulfilment evidence must not be deleted by automatic rollback')
