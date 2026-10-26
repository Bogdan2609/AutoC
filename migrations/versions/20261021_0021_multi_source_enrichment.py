"""v0.29 candidate-first, multi-supplier enrichment; no automatic publication."""
from alembic import op
import sqlalchemy as sa
revision='20261021_0021'
down_revision='20261021_0020'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('multi_source_proposals',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('product_id',sa.Uuid(),sa.ForeignKey('products.id',ondelete='SET NULL')),
        sa.Column('title',sa.String(300),nullable=False),
        sa.Column('category_code',sa.String(80)),
        sa.Column('category_id',sa.Uuid(),sa.ForeignKey('catalog_categories.id',ondelete='SET NULL')),
        sa.Column('attributes',sa.JSON(),nullable=False),
        sa.Column('evidence',sa.JSON(),nullable=False),
        sa.Column('conflicts',sa.JSON(),nullable=False),
        sa.Column('quality',sa.Integer(),nullable=False),
        sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('rule_version',sa.String(20),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('review_note',sa.String(500),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('applied_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('candidate_id',name='uq_multi_source_candidate'),
        sa.CheckConstraint("status IN ('proposed','review','approved','applied','rejected')",name='ck_multi_source_proposals_multi_source_proposal_status'))
    op.create_index('ix_multi_source_proposal_status','multi_source_proposals',['status','candidate_id'])
    op.create_table('multi_source_runs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('active_key',sa.String(20),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('parameters',sa.JSON(),nullable=False),
        sa.Column('affected',sa.Integer(),nullable=False),
        sa.Column('reviewed',sa.Integer(),nullable=False),
        sa.Column('message',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('analyze','approve_safe','prepare_drafts','apply')",name='ck_multi_source_runs_multi_source_run_kind'),
        sa.CheckConstraint("status IN ('queued','processing','completed','failed')",name='ck_multi_source_runs_multi_source_run_status'))
    op.create_index('uq_multi_source_active_run','multi_source_runs',['active_key'],unique=True,
                    postgresql_where=sa.text("status IN ('queued','processing')"),
                    sqlite_where=sa.text("status IN ('queued','processing')"))
    op.create_table('multi_source_decisions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('proposal_id',sa.Uuid(),sa.ForeignKey('multi_source_proposals.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('old_status',sa.String(16),nullable=False),
        sa.Column('new_status',sa.String(16),nullable=False),
        sa.Column('origin',sa.String(24),nullable=False),
        sa.Column('note',sa.String(500),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_multi_source_decision_proposal','multi_source_decisions',['proposal_id','created_at'])


def downgrade():
    op.drop_index('ix_multi_source_decision_proposal',table_name='multi_source_decisions')
    op.drop_table('multi_source_decisions')
    op.drop_index('uq_multi_source_active_run',table_name='multi_source_runs')
    op.drop_table('multi_source_runs')
    op.drop_index('ix_multi_source_proposal_status',table_name='multi_source_proposals')
    op.drop_table('multi_source_proposals')
