"""v0.28 catalog enrichment proposals, operator audits, durable work runs."""
from alembic import op
import sqlalchemy as sa
revision='20261021_0020'
down_revision='20261021_0019'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('catalog_enrichment_proposals',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('product_id',sa.Uuid(),sa.ForeignKey('products.id',ondelete='CASCADE'),nullable=False),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',name='fk_enrich_candidate',ondelete='RESTRICT'),nullable=False),
        sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('title',sa.String(300),nullable=False),
        sa.Column('category_code',sa.String(80)),
        sa.Column('category_id',sa.Uuid(),sa.ForeignKey('catalog_categories.id',ondelete='SET NULL')),
        sa.Column('attributes',sa.JSON(),nullable=False),
        sa.Column('evidence',sa.JSON(),nullable=False),
        sa.Column('flags',sa.JSON(),nullable=False),
        sa.Column('quality',sa.Integer(),nullable=False),
        sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('rule_version',sa.String(20),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('review_note',sa.String(500),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('applied_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('product_id',name='uq_enrichment_product'),
        sa.CheckConstraint("status IN ('proposed','review','approved','applied','rejected')",name='ck_catalog_enrichment_proposals_enrichment_proposal_status'))
    op.create_index('ix_enrichment_proposal_batch_state','catalog_enrichment_proposals',['batch_id','status','id'])
    op.create_table('catalog_enrichment_runs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('batch_id',sa.Uuid(),sa.ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('parameters',sa.JSON(),nullable=False),
        sa.Column('affected',sa.Integer(),nullable=False),
        sa.Column('reviewed',sa.Integer(),nullable=False),
        sa.Column('message',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True)))
    op.create_index('ix_enrichment_run_batch_created','catalog_enrichment_runs',['batch_id','created_at'])
    op.create_index('uq_enrichment_one_active_batch','catalog_enrichment_runs',['batch_id'],
                    unique=True,postgresql_where=sa.text("status IN ('queued','processing')"),
                    sqlite_where=sa.text("status IN ('queued','processing')"))
    op.create_table('catalog_enrichment_decisions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('proposal_id',sa.Uuid(),sa.ForeignKey('catalog_enrichment_proposals.id',name='fk_enrich_audit_proposal',ondelete='CASCADE'),nullable=False),
        sa.Column('old_status',sa.String(16),nullable=False),
        sa.Column('new_status',sa.String(16),nullable=False),
        sa.Column('note',sa.String(500),nullable=False),
        sa.Column('origin',sa.String(24),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_enrichment_decision_proposal_date','catalog_enrichment_decisions',['proposal_id','created_at'])


def downgrade():
    op.drop_index('ix_enrichment_decision_proposal_date',table_name='catalog_enrichment_decisions')
    op.drop_table('catalog_enrichment_decisions')
    op.drop_index('uq_enrichment_one_active_batch',table_name='catalog_enrichment_runs')
    op.drop_index('ix_enrichment_run_batch_created',table_name='catalog_enrichment_runs')
    op.drop_table('catalog_enrichment_runs')
    op.drop_index('ix_enrichment_proposal_batch_state',table_name='catalog_enrichment_proposals')
    op.drop_table('catalog_enrichment_proposals')
