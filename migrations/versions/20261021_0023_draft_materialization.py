"""v0.31 controlled draft materialization with preflight and audit."""
from alembic import op
import sqlalchemy as sa
revision='20261021_0023'
down_revision='20261021_0022'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('catalog_draft_runs',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('kind',sa.String(20),nullable=False),
        sa.Column('active_key',sa.String(20),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('parameters',sa.JSON(),nullable=False),
        sa.Column('scanned',sa.Integer(),nullable=False),
        sa.Column('eligible',sa.Integer(),nullable=False),
        sa.Column('created',sa.Integer(),nullable=False),
        sa.Column('reviewed',sa.Integer(),nullable=False),
        sa.Column('message',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('preflight','materialize')",name='draft_run_kind'),
        sa.CheckConstraint("status IN ('queued','processing','completed','failed')",name='draft_run_status'))
    op.create_index('uq_draft_active_run','catalog_draft_runs',['active_key'],unique=True,
                    postgresql_where=sa.text("status IN ('queued','processing')"),
                    sqlite_where=sa.text("status IN ('queued','processing')"))
    op.create_table('catalog_draft_preflights',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',ondelete='RESTRICT',name='fk_draft_pf_candidate'),nullable=False),
        sa.Column('proposal_id',sa.Uuid(),sa.ForeignKey('multi_source_proposals.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('rule_version',sa.String(20),nullable=False),
        sa.Column('rule_revision',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('reasons',sa.JSON(),nullable=False),
        sa.Column('product_id',sa.Uuid(),sa.ForeignKey('products.id',ondelete='SET NULL')),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('materialized_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('candidate_id',name='uq_draft_preflight_candidate'),
        sa.CheckConstraint("status IN ('eligible','review','materialized')",name='draft_preflight_status'))
    op.create_index('ix_draft_preflight_status_candidate','catalog_draft_preflights',['status','candidate_id'])
    op.create_table('catalog_draft_audit',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('candidate_id',sa.Uuid(),sa.ForeignKey('catalog_bulk_candidates.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('proposal_id',sa.Uuid(),sa.ForeignKey('multi_source_proposals.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('run_id',sa.Uuid(),sa.ForeignKey('catalog_draft_runs.id',ondelete='SET NULL')),
        sa.Column('event',sa.String(40),nullable=False),
        sa.Column('details',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_draft_audit_candidate_time','catalog_draft_audit',['candidate_id','created_at'])


def downgrade():
    op.drop_index('ix_draft_audit_candidate_time',table_name='catalog_draft_audit')
    op.drop_table('catalog_draft_audit')
    op.drop_index('ix_draft_preflight_status_candidate',table_name='catalog_draft_preflights')
    op.drop_table('catalog_draft_preflights')
    op.drop_index('uq_draft_active_run',table_name='catalog_draft_runs')
    op.drop_table('catalog_draft_runs')
