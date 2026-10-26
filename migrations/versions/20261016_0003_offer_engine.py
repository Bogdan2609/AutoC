"""offer engine v0.3 observability and operational policy

Revision ID: 20261016_0003
Revises: b0d71e4cf5ec
"""
import sqlalchemy as sa
from alembic import op
from datetime import datetime,timezone

revision = '20261016_0003'
down_revision = 'b0d71e4cf5ec'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('offer_cache_states', sa.Column('product_id',sa.Uuid(),nullable=True))
    op.add_column('offer_cache_states', sa.Column('last_completed_job_id',sa.String(length=36),nullable=True))
    op.add_column('supplier_items',sa.Column('last_search_token',sa.String(length=36),nullable=True))
    op.add_column('offer_cache_states', sa.Column('last_requested_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('offer_cache_states', sa.Column('last_demand_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('offer_cache_states', sa.Column('demand_count',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('offer_cache_states', sa.Column('last_success_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('offer_cache_states', sa.Column('last_finished_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('offer_cache_states', sa.Column('last_duration_ms',sa.Integer(),nullable=True))
    op.add_column('offer_cache_states', sa.Column('failure_count',sa.Integer(),nullable=False,server_default='0'))
    with op.batch_alter_table('offer_cache_states') as batch:
        batch.create_foreign_key('fk_offer_cache_states_product_id_products','products',['product_id'],['id'],ondelete='SET NULL')
    op.create_index('ix_offer_cache_states_product_id','offer_cache_states',['product_id'])
    op.create_index('ix_offer_cache_due','offer_cache_states',['expires_at','last_demand_at'])
    op.add_column('supplier_api_logs',sa.Column('lookup_key',sa.String(length=320),nullable=True))
    op.add_column('supplier_api_logs',sa.Column('result_count',sa.Integer(),nullable=True))
    op.create_table('supplier_runtime',
        sa.Column('code',sa.String(length=40),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('positive_ttl_seconds',sa.Integer(),nullable=False),
        sa.Column('empty_ttl_seconds',sa.Integer(),nullable=False),
        sa.Column('timeout_seconds',sa.Integer(),nullable=False),
        sa.Column('min_interval_seconds',sa.Integer(),nullable=False),
        sa.Column('next_request_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('last_changed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('consecutive_failures',sa.Integer(),nullable=False),
        sa.Column('circuit_open_until',sa.DateTime(timezone=True),nullable=True),
    )
    now=datetime.now(timezone.utc)
    runtime_table=sa.table('supplier_runtime',sa.column('code',sa.String),sa.column('enabled',sa.Boolean),
        sa.column('positive_ttl_seconds',sa.Integer),sa.column('empty_ttl_seconds',sa.Integer),
        sa.column('timeout_seconds',sa.Integer),sa.column('min_interval_seconds',sa.Integer),
        sa.column('next_request_at',sa.DateTime(timezone=True)),sa.column('last_changed_at',sa.DateTime(timezone=True)),
        sa.column('consecutive_failures',sa.Integer),sa.column('circuit_open_until',sa.DateTime(timezone=True)))
    op.bulk_insert(runtime_table,[{'code':code,'enabled':True,'positive_ttl_seconds':300,
        'empty_ttl_seconds':120,'timeout_seconds':25,'min_interval_seconds':2,
        'next_request_at':now,'last_changed_at':now,'consecutive_failures':0,
        'circuit_open_until':None} for code in ('rossko','adeo','partkom','forum-auto')])


def downgrade():
    op.drop_table('supplier_runtime')
    op.drop_column('supplier_items','last_search_token')
    op.drop_column('offer_cache_states','last_completed_job_id')
    op.drop_column('supplier_api_logs','result_count')
    op.drop_column('supplier_api_logs','lookup_key')
    op.drop_index('ix_offer_cache_due',table_name='offer_cache_states')
    op.drop_index('ix_offer_cache_states_product_id',table_name='offer_cache_states')
    with op.batch_alter_table('offer_cache_states') as batch:
        batch.drop_constraint('fk_offer_cache_states_product_id_products',type_='foreignkey')
    for col in ('failure_count','last_duration_ms','last_finished_at','last_success_at','last_requested_at','last_demand_at','demand_count','product_id'):
        op.drop_column('offer_cache_states',col)
