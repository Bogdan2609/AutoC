"""Dedicated admin sessions. Old employee cookies expire during deployment.

Existing customer login_sessions and domain tables stay untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = '20261023_0035'
down_revision = '20261023_0034'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('admin_login_sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('digest', name='uq_admin_login_sessions_digest'))
    op.create_index('ix_admin_login_sessions_user_id', 'admin_login_sessions', ['user_id'])

def downgrade():
    op.drop_index('ix_admin_login_sessions_user_id', table_name='admin_login_sessions')
    op.drop_table('admin_login_sessions')
