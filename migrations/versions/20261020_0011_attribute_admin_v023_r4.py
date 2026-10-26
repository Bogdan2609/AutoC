"""Attribute code rename history for safely editable filter identifiers.
Revision ID: 20261020_0011
Revises: 20261020_0010
"""
from alembic import op
import sqlalchemy as sa

revision = '20261020_0011'
down_revision = '20261020_0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('catalog_attribute_code_history',
        sa.Column('id',sa.Uuid(),nullable=False,primary_key=True),
        sa.Column('code',sa.String(80),nullable=False),
        sa.Column('attribute_id',sa.Uuid(),sa.ForeignKey('catalog_attribute_definitions.id',ondelete='CASCADE'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_catalog_attribute_code_history_code','catalog_attribute_code_history',['code'],unique=True)
    op.create_index('ix_catalog_attribute_code_history_attribute_id','catalog_attribute_code_history',['attribute_id'])


def downgrade():
    op.drop_index('ix_catalog_attribute_code_history_attribute_id',table_name='catalog_attribute_code_history')
    op.drop_index('ix_catalog_attribute_code_history_code',table_name='catalog_attribute_code_history')
    op.drop_table('catalog_attribute_code_history')
