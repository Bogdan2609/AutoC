"""Bulk catalog candidate identities, feed source provenance and review jobs."""
from alembic import op
import sqlalchemy as sa

revision='20261021_0019'
down_revision='20261021_0018'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('catalog_bulk_candidates',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('brand_norm',sa.String(160),nullable=False),
        sa.Column('article_norm',sa.String(110),nullable=False),
        sa.Column('sample_brand',sa.String(160),nullable=False),
        sa.Column('sample_article',sa.String(110),nullable=False),
        sa.Column('sample_description',sa.String(300),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('product_id',sa.Uuid(),sa.ForeignKey('products.id',ondelete='SET NULL')),
        sa.Column('review_note',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('brand_norm','article_norm',name='uq_bulk_candidate_identity'),
        sa.CheckConstraint("status IN ('new','review','approved','materialized','blocked')",name='ck_catalog_bulk_candidates_bulk_candidate_status'))
    op.create_index('ix_bulk_candidate_status','catalog_bulk_candidates',['status','brand_norm','article_norm'])
    op.create_table('catalog_bulk_candidate_batches',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',name='fk_bulk_source_candidate',ondelete='CASCADE'),nullable=False),
        sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('offers_count',sa.Integer(),nullable=False),
        sa.Column('description_variants',sa.Integer(),nullable=False),
        sa.Column('sku_variants',sa.Integer(),nullable=False),
        sa.Column('sample_description',sa.String(300),nullable=False),
        sa.UniqueConstraint('candidate_id','batch_id',name='uq_bulk_candidate_batch'))
    op.create_index('ix_bulk_candidate_batch_reverse','catalog_bulk_candidate_batches',['batch_id','candidate_id'])
    op.create_table('catalog_bulk_runs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('kind',sa.String(16),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='RESTRICT')),
        sa.Column('parameters',sa.JSON(),nullable=False),
        sa.Column('affected',sa.Integer(),nullable=False),
        sa.Column('linked',sa.Integer(),nullable=False),
        sa.Column('message',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True)))
    op.create_index('ix_bulk_run_batch_date','catalog_bulk_runs',['batch_id','created_at'])
    op.create_table('catalog_bulk_decision_audit',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',name='fk_bulk_audit_candidate',ondelete='RESTRICT'),nullable=False),
        sa.Column('old_status',sa.String(16),nullable=False),
        sa.Column('new_status',sa.String(16),nullable=False),
        sa.Column('note',sa.String(500),nullable=False),
        sa.Column('origin',sa.String(32),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_bulk_decision_candidate_date','catalog_bulk_decision_audit',['candidate_id','created_at'])
    # Critical for selective updating existing published or draft feed offers after review.
    op.create_index('ix_feed_offers_identity_batch','feed_offers',['brand_norm','article_norm','batch_id'])


def downgrade():
    op.drop_index('ix_bulk_decision_candidate_date',table_name='catalog_bulk_decision_audit')
    op.drop_table('catalog_bulk_decision_audit')
    op.drop_index('ix_feed_offers_identity_batch',table_name='feed_offers')
    op.drop_index('ix_bulk_run_batch_date',table_name='catalog_bulk_runs')
    op.drop_table('catalog_bulk_runs')
    op.drop_index('ix_bulk_candidate_batch_reverse',table_name='catalog_bulk_candidate_batches')
    op.drop_table('catalog_bulk_candidate_batches')
    op.drop_index('ix_bulk_candidate_status',table_name='catalog_bulk_candidates')
    op.drop_table('catalog_bulk_candidates')
