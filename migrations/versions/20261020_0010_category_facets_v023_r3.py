"""Category-specific attribute presentation and faceted search metadata.

Revision ID: 20261020_0010
Revises: 20261020_0009
Additive only; existing category bindings and typed product values are preserved.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261020_0010'
down_revision = '20261020_0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('catalog_category_attributes', sa.Column(
        'show_in_filters', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('catalog_category_attributes', sa.Column(
        'sort_order', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('catalog_category_attributes', sa.Column(
        'group_name', sa.String(80), nullable=False, server_default='Основные'))
    op.create_check_constraint('ck_category_attribute_sort_order',
                               'catalog_category_attributes', 'sort_order >= 0')


def downgrade():
    op.drop_constraint('ck_category_attribute_sort_order', 'catalog_category_attributes', type_='check')
    op.drop_column('catalog_category_attributes', 'group_name')
    op.drop_column('catalog_category_attributes', 'sort_order')
    op.drop_column('catalog_category_attributes', 'show_in_filters')
