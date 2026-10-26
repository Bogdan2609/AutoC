"""Brand authority, category taxonomy, typed product facts and governed media.

Revision ID: 20261019_0008
Revises: 20261019_0007
No existing product/brand/offer/order rows are rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision='20261019_0008'
down_revision='20261019_0007'
branch_labels=None
depends_on=None


def uid(name, *args, **kw):
    return sa.Column(name,sa.Uuid(as_uuid=True),*args,**kw)


def upgrade():
    op.create_table('brand_profiles',
        uid('brand_id',sa.ForeignKey('brands.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('legal_name',sa.String(240)),sa.Column('website',sa.String(300)),
        sa.Column('country_code',sa.String(2)),sa.Column('description',sa.Text()),
        sa.Column('verified',sa.Boolean(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('brand_aliases',
        uid('id',primary_key=True),
        uid('brand_id',sa.ForeignKey('brands.id',ondelete='CASCADE'),nullable=False),
        uid('supplier_id',sa.ForeignKey('suppliers.id',ondelete='RESTRICT')),
        sa.Column('alias',sa.String(160),nullable=False),
        sa.Column('alias_norm',sa.String(160),nullable=False),
        sa.Column('scope_key',sa.String(300),nullable=False),
        sa.Column('verified',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('scope_key',name='uq_brand_alias_scope_key'))
    op.create_index('ix_brand_aliases_brand_id','brand_aliases',['brand_id'])
    op.create_table('catalog_categories',
        uid('id',primary_key=True),
        uid('parent_id',sa.ForeignKey('catalog_categories.id',ondelete='RESTRICT')),
        sa.Column('name',sa.String(180),nullable=False),
        sa.Column('slug',sa.String(200),nullable=False,unique=True),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.CheckConstraint('parent_id IS NULL OR parent_id <> id',name='category_not_own_parent'))
    op.create_index('ix_catalog_categories_parent_id','catalog_categories',['parent_id'])
    op.create_table('catalog_product_categories',
        uid('id',primary_key=True),
        uid('product_id',sa.ForeignKey('products.id',ondelete='CASCADE'),nullable=False),
        uid('category_id',sa.ForeignKey('catalog_categories.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('is_primary',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('product_id','category_id',name='uq_catalog_product_category'))
    op.create_index('ix_catalog_product_categories_product_id','catalog_product_categories',['product_id'])
    op.create_index('ix_catalog_product_categories_category_id','catalog_product_categories',['category_id'])
    op.create_index('uq_catalog_one_primary_per_product','catalog_product_categories',['product_id'],unique=True,
        postgresql_where=sa.text('is_primary = true'),sqlite_where=sa.text('is_primary = 1'))
    op.create_table('catalog_attribute_definitions',
        uid('id',primary_key=True),
        sa.Column('code',sa.String(80),nullable=False,unique=True),
        sa.Column('name',sa.String(180),nullable=False),
        sa.Column('data_type',sa.String(12),nullable=False),
        sa.Column('unit',sa.String(24)),sa.Column('enum_values',sa.JSON()),
        sa.Column('filterable',sa.Boolean(),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.CheckConstraint("data_type IN ('text','integer','decimal','boolean','enum')",name='catalog_attr_type'))
    op.create_table('catalog_category_attributes',
        uid('id',primary_key=True),
        uid('category_id',sa.ForeignKey('catalog_categories.id',ondelete='CASCADE'),nullable=False),
        uid('attribute_id',sa.ForeignKey('catalog_attribute_definitions.id',ondelete='RESTRICT',name='fk_catalog_category_attr_def'),nullable=False),
        sa.Column('required',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('category_id','attribute_id',name='uq_catalog_category_attribute'))
    op.create_index('ix_catalog_category_attributes_category_id','catalog_category_attributes',['category_id'])
    op.create_table('catalog_product_attributes',
        uid('id',primary_key=True),
        uid('product_id',sa.ForeignKey('products.id',ondelete='CASCADE'),nullable=False),
        uid('attribute_id',sa.ForeignKey('catalog_attribute_definitions.id',ondelete='RESTRICT',name='fk_catalog_product_attr_def'),nullable=False),
        sa.Column('value_text',sa.String(500)),sa.Column('value_integer',sa.Integer()),
        sa.Column('value_decimal',sa.Numeric(18,6)),sa.Column('value_boolean',sa.Boolean()),
        sa.Column('value_enum',sa.String(160)),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('product_id','attribute_id',name='uq_catalog_product_attribute'),
        sa.CheckConstraint('((CASE WHEN value_text IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_integer IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_decimal IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_boolean IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_enum IS NOT NULL THEN 1 ELSE 0 END)) = 1',name='catalog_one_typed_value'))
    op.create_index('ix_catalog_product_attributes_product_id','catalog_product_attributes',['product_id'])
    op.create_table('catalog_product_media',
        uid('id',primary_key=True),
        uid('product_id',sa.ForeignKey('products.id',ondelete='CASCADE')),
        uid('brand_id',sa.ForeignKey('brands.id',ondelete='CASCADE')),
        sa.Column('kind',sa.String(12),nullable=False),
        sa.Column('mime_type',sa.String(70),nullable=False),
        sa.Column('original_filename',sa.String(180),nullable=False),
        sa.Column('storage_key',sa.String(180),nullable=False,unique=True),
        sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('size_bytes',sa.Integer(),nullable=False),
        sa.Column('width',sa.Integer()),sa.Column('height',sa.Integer()),
        sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('alt_text',sa.String(250)),
        sa.Column('status',sa.String(12),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint("kind IN ('image','document','logo')",name='catalog_media_kind'),
        sa.CheckConstraint("status IN ('draft','approved','rejected')",name='catalog_media_status'),
        sa.CheckConstraint('(product_id IS NOT NULL AND brand_id IS NULL) OR (product_id IS NULL AND brand_id IS NOT NULL)',name='catalog_media_one_owner'),
        sa.CheckConstraint('size_bytes > 0',name='catalog_media_positive_size'))
    op.create_index('ix_catalog_media_owner_product_position','catalog_product_media',['product_id','position'])


def downgrade():
    op.drop_index('ix_catalog_media_owner_product_position',table_name='catalog_product_media')
    op.drop_table('catalog_product_media')
    op.drop_index('ix_catalog_product_attributes_product_id',table_name='catalog_product_attributes')
    op.drop_table('catalog_product_attributes')
    op.drop_index('ix_catalog_category_attributes_category_id',table_name='catalog_category_attributes')
    op.drop_table('catalog_category_attributes')
    op.drop_table('catalog_attribute_definitions')
    op.drop_index('uq_catalog_one_primary_per_product',table_name='catalog_product_categories')
    op.drop_index('ix_catalog_product_categories_category_id',table_name='catalog_product_categories')
    op.drop_index('ix_catalog_product_categories_product_id',table_name='catalog_product_categories')
    op.drop_table('catalog_product_categories')
    op.drop_index('ix_catalog_categories_parent_id',table_name='catalog_categories')
    op.drop_table('catalog_categories')
    op.drop_index('ix_brand_aliases_brand_id',table_name='brand_aliases')
    op.drop_table('brand_aliases')
    op.drop_table('brand_profiles')
