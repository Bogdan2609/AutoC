"""Pin buyer order UUID to supplier purchase attempts for ROSSKO Part.comment.

Historic attempts retain null and are NOT cleared or replayed.  The confirm
path derives the UUID solely from a verified paid OrderLine, never operator input.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261023_0033'
down_revision = '20261023_0032'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('supplier_purchase_attempts', sa.Column('buyer_order_id', sa.Uuid(),
                  sa.ForeignKey('customer_orders.id', ondelete='RESTRICT'), nullable=True))
    op.add_column('supplier_purchase_attempts', sa.Column('contact_comment', sa.String(500),
                  nullable=False, server_default=''))


def downgrade():
    op.drop_column('supplier_purchase_attempts', 'contact_comment')
    op.drop_column('supplier_purchase_attempts', 'buyer_order_id')
