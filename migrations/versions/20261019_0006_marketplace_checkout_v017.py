"""Operator-only multi-supplier quote requests and immutable catalog snapshots.

Revision ID: 20261019_0006
Revises: 20261019_0005
"""
from alembic import op
import sqlalchemy as sa

revision='20261019_0006'
down_revision='20261019_0005'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('cart_lines',sa.Column('line_kind',sa.String(24),nullable=False,server_default='legacy'))
    op.add_column('cart_lines',sa.Column('product_id',sa.Uuid(),nullable=True))
    op.add_column('cart_lines',sa.Column('quoted_unit_retail',sa.Numeric(14,2),nullable=True))
    op.add_column('cart_lines',sa.Column('quote_metadata',sa.JSON(),nullable=True))
    op.create_foreign_key('fk_v017_cart_product','cart_lines','products',['product_id'],['id'],ondelete='RESTRICT')
    op.add_column('customer_orders',sa.Column('total_is_final',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.add_column('customer_orders',sa.Column('request_fingerprint',sa.String(64),nullable=True))
    op.add_column('customer_orders',sa.Column('request_details',sa.JSON(),nullable=True))
    op.add_column('customer_orders',sa.Column('request_expires_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('order_lines',sa.Column('product_id',sa.Uuid(),nullable=True))
    op.add_column('order_lines',sa.Column('commercial_snapshot',sa.JSON(),nullable=True))
    op.create_foreign_key('fk_v017_order_product','order_lines','products',['product_id'],['id'],ondelete='RESTRICT')
    op.create_index('ix_v017_marketplace_requests','customer_orders',['status','created_at'])


def downgrade():
    op.drop_index('ix_v017_marketplace_requests',table_name='customer_orders')
    op.drop_constraint('fk_v017_order_product','order_lines',type_='foreignkey')
    op.drop_column('order_lines','commercial_snapshot')
    op.drop_column('order_lines','product_id')
    op.drop_column('customer_orders','request_expires_at')
    op.drop_column('customer_orders','request_details')
    op.drop_column('customer_orders','request_fingerprint')
    op.drop_column('customer_orders','total_is_final')
    op.drop_constraint('fk_v017_cart_product','cart_lines',type_='foreignkey')
    op.drop_column('cart_lines','quote_metadata')
    op.drop_column('cart_lines','quoted_unit_retail')
    op.drop_column('cart_lines','product_id')
    op.drop_column('cart_lines','line_kind')
