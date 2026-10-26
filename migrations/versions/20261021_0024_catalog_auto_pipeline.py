"""v0.31.3 auto-pipeline job kind. Existing data and products unchanged."""
from alembic import op
import sqlalchemy as sa

revision='20261021_0024'
down_revision='20261021_0023'
branch_labels=None
depends_on=None


def upgrade():
    # PostgreSQL check replacement; SQLite batch recreation for isolated tests.
    with op.batch_alter_table('catalog_draft_runs') as batch:
        batch.drop_constraint('draft_run_kind',type_='check')
        batch.create_check_constraint('draft_run_kind',"kind IN ('preflight','materialize','auto_pipeline')")


def downgrade():
    # Fail rather than discard or mislabel an active/previous automated run.
    conn=op.get_bind()
    count=conn.execute(sa.text("SELECT COUNT(*) FROM catalog_draft_runs WHERE kind='auto_pipeline'")).scalar()
    if count:
        raise RuntimeError('Retain audit: auto_pipeline runs exist; cannot downgrade check safely')
    with op.batch_alter_table('catalog_draft_runs') as batch:
        batch.drop_constraint('draft_run_kind',type_='check')
        batch.create_check_constraint('draft_run_kind',"kind IN ('preflight','materialize')")
