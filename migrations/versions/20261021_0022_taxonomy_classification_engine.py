"""v0.30 database-defined taxonomy, versioned rules and immutable audit."""
from alembic import op
import sqlalchemy as sa
revision='20261021_0022'
down_revision='20261021_0021'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('classification_groups',
        sa.Column('code',sa.String(50),primary_key=True),
        sa.Column('name',sa.String(160),nullable=False),
        sa.Column('parent_code',sa.String(50),sa.ForeignKey('classification_groups.code',ondelete='RESTRICT')),
        sa.Column('active',sa.Boolean(),nullable=False))
    op.create_table('classification_types',
        sa.Column('code',sa.String(80),primary_key=True),
        sa.Column('group_code',sa.String(50),sa.ForeignKey('classification_groups.code',ondelete='RESTRICT'),nullable=False),
        sa.Column('category_name',sa.String(180),nullable=False),
        sa.Column('title_stem',sa.String(180),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False))
    op.create_table('classification_patterns',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('key',sa.String(100),nullable=False,unique=True),
        sa.Column('type_code',sa.String(80),sa.ForeignKey('classification_types.code',ondelete='RESTRICT'),nullable=False),
        sa.Column('positive_regex',sa.String(300),nullable=False),
        sa.Column('negative_regex',sa.String(300)),
        sa.Column('priority',sa.Integer(),nullable=False),
        sa.Column('supplier',sa.String(32),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.Column('requires_review',sa.Boolean(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('priority BETWEEN 0 AND 1000',name='classification_pattern_priority'))
    op.create_index('ix_classification_patterns_active_priority','classification_patterns',['active','priority'])
    op.create_table('classification_revisions',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('classification_rule_audit',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('action',sa.String(32),nullable=False),
        sa.Column('subject',sa.String(130),nullable=False),
        sa.Column('before',sa.Text(),nullable=False),
        sa.Column('after',sa.Text(),nullable=False),
        sa.Column('origin',sa.String(24),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_classification_rule_audit_revision','classification_rule_audit',['revision'])


def downgrade():
    op.drop_index('ix_classification_rule_audit_revision',table_name='classification_rule_audit')
    op.drop_table('classification_rule_audit')
    op.drop_table('classification_revisions')
    op.drop_index('ix_classification_patterns_active_priority',table_name='classification_patterns')
    op.drop_table('classification_patterns')
    op.drop_table('classification_types')
    op.drop_table('classification_groups')
