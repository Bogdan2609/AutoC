"""Store an optional ROSSKO organization ID per verified supplier delivery destination.

Existing account/address mappings remain unchanged and operational. No ID
is invented or populated from unrelated API credentials.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261022_0031'
down_revision = '20261022_0030'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('supplier_destinations',
                  sa.Column('external_requisite_id', sa.String(length=80), nullable=True))

def downgrade():
    op.drop_column('supplier_destinations', 'external_requisite_id')
