"""Master catalog: families, compositions, factory packaging and sale lots.

Revision ID: 20261019_0007
Revises: 20261019_0006
"""
from alembic import op
import sqlalchemy as sa

revision='20261019_0007'
down_revision='20261019_0006'
branch_labels=None
depends_on=None


def uid(name,*args,**kw):
    return sa.Column(name,sa.Uuid(),*args,**kw)


def upgrade():
    op.create_table('product_families',
        uid('id',primary_key=True),sa.Column('name',sa.String(220),nullable=False),
        sa.Column('slug',sa.String(220),nullable=False,unique=True),
        sa.Column('kind',sa.String(24),nullable=False),sa.Column('axes',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("kind IN ('variant','kit_line','related_line')",name='family_kind'))
    op.create_table('product_family_members',
        uid('id',primary_key=True),uid('family_id',sa.ForeignKey('product_families.id',ondelete='CASCADE'),nullable=False),
        uid('product_id',sa.ForeignKey('products.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('axis_values',sa.JSON(),nullable=False),sa.Column('variant_key',sa.String(500),nullable=False),
        sa.UniqueConstraint('family_id','variant_key',name='uq_family_variant_key'),
        sa.UniqueConstraint('product_id',name='uq_product_one_primary_family'))
    op.create_index('ix_product_family_members_family_id','product_family_members',['family_id'])
    op.create_table('product_compositions',
        uid('product_id',sa.ForeignKey('products.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('kind',sa.String(24),nullable=False),sa.Column('verified',sa.Boolean(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint("kind IN ('factory_kit','assembled_plan')",name='composition_kind'))
    op.create_table('product_component_lines',
        uid('id',primary_key=True),
        uid('parent_product_id',sa.ForeignKey('product_compositions.product_id',name='fk_component_parent',ondelete='CASCADE'),nullable=False),
        uid('component_product_id',sa.ForeignKey('products.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('position',sa.Integer(),nullable=False),sa.Column('name',sa.String(220),nullable=False),
        sa.Column('external_part_number',sa.String(140),nullable=True),
        sa.Column('quantity',sa.Numeric(12,3),nullable=False),sa.Column('unit',sa.String(12),nullable=False),
        sa.Column('sale_status',sa.String(20),nullable=False),
        sa.UniqueConstraint('parent_product_id','position',name='uq_component_position'),
        sa.CheckConstraint('quantity > 0',name='positive_component_quantity'),
        sa.CheckConstraint('parent_product_id <> component_product_id',name='distinct_component'),
        sa.CheckConstraint("unit IN ('pcs','kg','l','m')",name='component_unit'),
        sa.CheckConstraint("sale_status IN ('unknown','not_listed','independent')",name='component_sale_status'))
    op.create_index('ix_product_component_lines_parent_product_id','product_component_lines',['parent_product_id'])
    op.create_table('factory_packages',
        uid('id',primary_key=True),uid('outer_product_id',sa.ForeignKey('products.id',ondelete='RESTRICT'),nullable=False),
        uid('inner_product_id',sa.ForeignKey('products.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('units_per_package',sa.Integer(),nullable=False),
        sa.Column('kind',sa.String(24),nullable=False),sa.Column('verified',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('outer_product_id',name='uq_factory_package_outer'),
        sa.CheckConstraint('outer_product_id <> inner_product_id',name='different_package_products'),
        sa.CheckConstraint('units_per_package > 1',name='package_more_than_one'),
        sa.CheckConstraint("kind IN ('retail_pack','box','case','pallet')",name='package_kind'))
    op.create_index('ix_factory_packages_outer_product_id','factory_packages',['outer_product_id'])
    op.create_index('ix_factory_packages_inner_product_id','factory_packages',['inner_product_id'])
    op.create_table('offer_sale_terms',
        uid('id',primary_key=True),sa.Column('scope_key',sa.String(260),nullable=False),
        uid('seller_offer_id',sa.ForeignKey('seller_offers.id',ondelete='RESTRICT'),nullable=True),
        uid('supplier_item_id',sa.ForeignKey('supplier_items.id',ondelete='RESTRICT'),nullable=True),
        uid('supplier_account_id',sa.ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=True),
        uid('supplier_destination_id',sa.ForeignKey('supplier_destinations.id',name='fk_sale_supplier_destination',ondelete='RESTRICT'),nullable=True),
        sa.Column('warehouse_id',sa.String(140),nullable=True),
        sa.Column('minimum_units',sa.Integer(),nullable=False),sa.Column('step_units',sa.Integer(),nullable=False),
        sa.Column('units_per_package',sa.Integer(),nullable=False),
        sa.Column('order_unit',sa.String(12),nullable=False),
        sa.Column('status',sa.String(12),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=True),
        sa.UniqueConstraint('scope_key',name='uq_sale_scope_key'),
        sa.CheckConstraint('minimum_units >= 1 AND step_units >= 1 AND units_per_package >= 1',name='positive_sale_units'),
        sa.CheckConstraint("status IN ('draft','active')",name='sale_terms_status'),
        sa.CheckConstraint("order_unit IN ('piece','pack')",name='sale_terms_unit'),
        sa.CheckConstraint('((seller_offer_id IS NOT NULL AND supplier_item_id IS NULL AND supplier_account_id IS NULL AND supplier_destination_id IS NULL AND warehouse_id IS NULL) OR (seller_offer_id IS NULL AND supplier_item_id IS NOT NULL AND supplier_account_id IS NOT NULL AND supplier_destination_id IS NOT NULL AND warehouse_id IS NOT NULL))',name='sale_terms_valid_scope'))


def downgrade():
    op.drop_table('offer_sale_terms')
    op.drop_index('ix_factory_packages_inner_product_id',table_name='factory_packages')
    op.drop_index('ix_factory_packages_outer_product_id',table_name='factory_packages')
    op.drop_table('factory_packages')
    op.drop_index('ix_product_component_lines_parent_product_id',table_name='product_component_lines')
    op.drop_table('product_component_lines')
    op.drop_table('product_compositions')
    op.drop_index('ix_product_family_members_family_id',table_name='product_family_members')
    op.drop_table('product_family_members')
    op.drop_table('product_families')
