"""Destination-bound supplier quotes and explicit per-account purchasing permission.

Revision ID: 20261019_0005
Revises: 20261017_0004
"""
from alembic import op
import sqlalchemy as sa

revision='20261019_0005'
down_revision='20261017_0004'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('supplier_accounts',sa.Column('procurement_enabled',sa.Boolean(),nullable=False,server_default=sa.false()))
    with op.batch_alter_table('supplier_offer_snapshots') as batch:
        batch.add_column(sa.Column('supplier_destination_id',sa.Uuid(),nullable=True))
        batch.create_foreign_key('fk_offer_snapshots_destination',
            'supplier_destinations',['supplier_destination_id'],['id'],ondelete='RESTRICT')
        batch.create_index('ix_supplier_offer_snapshots_supplier_destination_id',['supplier_destination_id'])
    with op.batch_alter_table('cart_lines') as batch:
        batch.add_column(sa.Column('supplier_destination_id',sa.Uuid(),nullable=True))
        batch.add_column(sa.Column('validated_snapshot_id',sa.Uuid(),nullable=True))
        batch.add_column(sa.Column('validated_at',sa.DateTime(timezone=True),nullable=True))
        batch.create_foreign_key('fk_cart_line_supplier_dest','supplier_destinations',
                                 ['supplier_destination_id'],['id'])
        batch.create_foreign_key('fk_cart_line_snapshot','supplier_offer_snapshots',
                                 ['validated_snapshot_id'],['id'])
    op.create_table('supplier_purchase_attempts',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('client_ref',sa.String(100),nullable=False,unique=True),
        sa.Column('supplier_account_id',sa.Uuid(),sa.ForeignKey('supplier_accounts.id',name='fk_purchase_account',ondelete='RESTRICT'),nullable=False),
        sa.Column('supplier_destination_id',sa.Uuid(),sa.ForeignKey('supplier_destinations.id',name='fk_purchase_destination',ondelete='RESTRICT'),nullable=False),
        sa.Column('snapshot_id',sa.Uuid(),sa.ForeignKey('supplier_offer_snapshots.id',name='fk_purchase_snapshot',ondelete='RESTRICT'),nullable=False),
        sa.Column('quantity',sa.Integer(),nullable=False),
        sa.Column('approved_unit_price',sa.Numeric(14,2),nullable=False),
        sa.Column('contact_name',sa.String(160),nullable=False),
        sa.Column('contact_phone',sa.String(40),nullable=False),
        sa.Column('payment_id',sa.String(40),nullable=False),
        sa.Column('requisite_id',sa.String(80),nullable=True),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('remote_order_ids',sa.JSON(),nullable=False),
        sa.Column('error_code',sa.String(80),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('sent_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('completed_at',sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint('quantity > 0',name='purchase_attempt_quantity'),
        sa.CheckConstraint('approved_unit_price > 0',name='purchase_attempt_price'),
        sa.CheckConstraint("status IN ('prepared','dispatch_unknown','accepted','rejected','manual_review')",name='purchase_attempt_status'),
    )
    op.create_index('ix_supplier_purchase_attempts_supplier_account_id','supplier_purchase_attempts',['supplier_account_id'])


def downgrade():
    op.drop_index('ix_supplier_purchase_attempts_supplier_account_id',table_name='supplier_purchase_attempts')
    op.drop_table('supplier_purchase_attempts')
    with op.batch_alter_table('supplier_offer_snapshots') as batch:
        batch.drop_index('ix_supplier_offer_snapshots_supplier_destination_id')
        batch.drop_constraint('fk_offer_snapshots_destination',type_='foreignkey')
        batch.drop_column('supplier_destination_id')
    op.drop_column('supplier_accounts','procurement_enabled')
    with op.batch_alter_table('cart_lines') as batch:
        batch.drop_constraint('fk_cart_line_snapshot',type_='foreignkey')
        batch.drop_constraint('fk_cart_line_supplier_dest',type_='foreignkey')
        batch.drop_column('validated_at')
        batch.drop_column('validated_snapshot_id')
        batch.drop_column('supplier_destination_id')
