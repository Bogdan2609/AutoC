"""Supplier-file staging, immutable versions, scoped warehouse mappings.

No real purchase/payment table is modified.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261021_0018'
down_revision = '20261021_0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('feed_sources',
        sa.Column('code',sa.String(32),primary_key=True),
        sa.Column('active_batch_id',sa.Uuid(),nullable=True),
        sa.Column('max_age_hours',sa.Integer(),nullable=False),
        sa.Column('commercial_verified',sa.Boolean(),nullable=False),
        sa.Column('price_kind',sa.String(16),nullable=False),
        sa.Column('currency',sa.String(3),nullable=False),
        sa.Column('tax_status',sa.String(16),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('feed_batches',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('supplier_code',sa.String(32),sa.ForeignKey('feed_sources.code',ondelete='RESTRICT'),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('original_name',sa.String(200),nullable=False),
        sa.Column('stored_name',sa.String(80),nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('generated_at',sa.DateTime(timezone=True)),
        sa.Column('rows_total',sa.Integer(),nullable=False),
        sa.Column('rows_valid',sa.Integer(),nullable=False),
        sa.Column('rows_rejected',sa.Integer(),nullable=False),
        sa.Column('rows_duplicates',sa.Integer(),nullable=False),
        sa.Column('rows_matched',sa.Integer(),nullable=False),
        sa.Column('errors_sample',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.Column('published_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('supplier_code','sha256',name='uq_feed_batch_supplier_checksum'),
        sa.CheckConstraint("status IN ('uploaded','queued','processing','staged','published','failed','rejected')",name='ck_feed_batches_feed_batch_status'))
    op.create_index('ix_feed_batch_supplier_date','feed_batches',['supplier_code','created_at'])
    op.create_table('feed_offers',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='CASCADE'),nullable=False),
        sa.Column('identity_hash',sa.String(64),nullable=False),
        sa.Column('source_line',sa.Integer(),nullable=False),
        sa.Column('supplier_sku',sa.String(150),nullable=False),
        sa.Column('brand',sa.String(160),nullable=False),
        sa.Column('brand_norm',sa.String(160),nullable=False),
        sa.Column('article',sa.String(110),nullable=False),
        sa.Column('article_norm',sa.String(110),nullable=False),
        sa.Column('description',sa.Text(),nullable=False),
        sa.Column('warehouse_code',sa.String(140),nullable=False),
        sa.Column('purchase_price',sa.Numeric(14,2),nullable=False),
        sa.Column('stock_raw',sa.String(32),nullable=False),
        sa.Column('stock_min',sa.Integer(),nullable=False),
        sa.Column('is_lower_bound',sa.Boolean(),nullable=False),
        sa.Column('minimum',sa.Integer(),nullable=False),
        sa.Column('multiple',sa.Integer(),nullable=False),
        sa.Column('lead_days',sa.Integer()),
        sa.Column('product_id',sa.Uuid(),sa.ForeignKey('products.id',ondelete='SET NULL')),
        sa.UniqueConstraint('batch_id','identity_hash',name='uq_feed_offer_version_key'),
        sa.CheckConstraint('purchase_price > 0 AND stock_min >= 0 AND minimum >= 1 AND multiple >= 1',name='ck_feed_offers_feed_positive_terms'))
    op.create_index('ix_feed_offers_product_version','feed_offers',['product_id','batch_id'])
    op.create_index('ix_feed_offers_match','feed_offers',['batch_id','brand_norm','article_norm'])
    op.create_index('ix_feed_offers_scope','feed_offers',['batch_id','warehouse_code'])
    op.create_table('feed_warehouse_mappings',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('supplier_code',sa.String(32),sa.ForeignKey('feed_sources.code',ondelete='RESTRICT'),nullable=False),
        sa.Column('warehouse_code',sa.String(140),nullable=False),
        sa.Column('account_id',sa.Uuid(),sa.ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('destination_id',sa.Uuid(),sa.ForeignKey('supplier_destinations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('approved',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('supplier_code','warehouse_code','account_id','destination_id',name='uq_feed_warehouse_account_destination'))
    op.create_table('feed_brand_aliases',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('supplier_code',sa.String(32),sa.ForeignKey('feed_sources.code',ondelete='RESTRICT'),nullable=False),
        sa.Column('brand_norm',sa.String(160),nullable=False),
        sa.Column('brand_id',sa.Uuid(),sa.ForeignKey('brands.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('approved',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('supplier_code','brand_norm',name='uq_feed_brand_supplier_alias'))


def downgrade():
    op.drop_table('feed_brand_aliases')
    op.drop_table('feed_warehouse_mappings')
    op.drop_index('ix_feed_offers_scope',table_name='feed_offers')
    op.drop_index('ix_feed_offers_match',table_name='feed_offers')
    op.drop_index('ix_feed_offers_product_version',table_name='feed_offers')
    op.drop_table('feed_offers')
    op.drop_index('ix_feed_batch_supplier_date',table_name='feed_batches')
    op.drop_table('feed_batches')
    op.drop_table('feed_sources')
