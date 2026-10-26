"""v0.23 Product Workspace SEO fields. Safe additive-only migration.

Revision ID: 20261020_0009
Revises: 20261019_0008
No changes to product identity, offers, inventories or slug history.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261020_0009'
down_revision = '20261019_0008'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('products', sa.Column('seo_title', sa.String(250), nullable=True))
    op.add_column('products', sa.Column('seo_description', sa.String(500), nullable=True))
    op.add_column('products', sa.Column('seo_h1', sa.String(300), nullable=True))
    op.add_column('products', sa.Column('seo_noindex', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('catalog_product_change_log',
        sa.Column('id',sa.Uuid(as_uuid=True),primary_key=True),
        sa.Column('product_id',sa.Uuid(as_uuid=True),sa.ForeignKey('products.id',ondelete='CASCADE'),nullable=False),
        sa.Column('action',sa.String(40),nullable=False),
        sa.Column('changes',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_catalog_product_change_log_product_id','catalog_product_change_log',['product_id'])

def downgrade():
    op.drop_index('ix_catalog_product_change_log_product_id',table_name='catalog_product_change_log')
    op.drop_table('catalog_product_change_log')
    op.drop_column('products', 'seo_noindex')
    op.drop_column('products', 'seo_h1')
    op.drop_column('products', 'seo_description')
    op.drop_column('products', 'seo_title')
