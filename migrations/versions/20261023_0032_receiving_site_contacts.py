"""Add supplier courier recipient contacts to merchant warehouse/PVZ nodes.

No backfill from buyer orders, accounts, or previously typed test contacts.
Existing nodes remain readable; live supplier checkout fails closed until
contacts are explicitly configured on the receiving node.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261023_0032'
down_revision = '20261022_0031'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('fulfillment_nodes', sa.Column('receiving_contact_name', sa.String(length=160), nullable=True))
    op.add_column('fulfillment_nodes', sa.Column('receiving_contact_phone', sa.String(length=40), nullable=True))
    op.add_column('fulfillment_nodes', sa.Column('receiving_contact_comment', sa.String(length=500), nullable=False, server_default=''))


def downgrade():
    op.drop_column('fulfillment_nodes', 'receiving_contact_comment')
    op.drop_column('fulfillment_nodes', 'receiving_contact_phone')
    op.drop_column('fulfillment_nodes', 'receiving_contact_name')
