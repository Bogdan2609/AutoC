"""Individual admin staff roles and an append-only mutation-attempt audit trail.

No existing admin tokens or login sessions are changed.  Deployment is backwards
compatible until ADMIN_LEGACY_TOKEN_ENABLED=false is explicitly configured.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261023_0034'
down_revision = '20261023_0033'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('admin_staff',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(32), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('user_id', name='uq_admin_staff_user_id'),
    )
    op.create_table('admin_staff_audit',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('actor_user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_kind', sa.String(30), nullable=False),
        sa.Column('action', sa.String(12), nullable=False),
        sa.Column('path', sa.String(400), nullable=False),
        sa.Column('scope', sa.String(40), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_admin_staff_audit_occurred_at', 'admin_staff_audit', ['occurred_at'])


def downgrade():
    op.drop_index('ix_admin_staff_audit_occurred_at', table_name='admin_staff_audit')
    op.drop_table('admin_staff_audit')
    op.drop_table('admin_staff')
