"""P1/P2 integrity and routing indexes; no changes to access secrets.

Preflight rejects inconsistent old carts rather than silently discarding cart lines.
"""
from alembic import op
import sqlalchemy as sa

revision='20261021_0017'
down_revision='20261020_0016'
branch_labels=None
depends_on=None


def _preflight():
    db=op.get_bind()
    duplicate_carts=db.execute(sa.text("""
        SELECT user_id, count(*) FROM carts WHERE status='open'
        GROUP BY user_id HAVING count(*)>1 LIMIT 1
    """)).first()
    if duplicate_carts:
        raise RuntimeError('Duplicate open carts found; merge/cart-reconcile BEFORE migration 0017')
    duplicate_lines=db.execute(sa.text("""
        SELECT cart_id,seller_offer_id,fulfillment_node_id,count(*) FROM cart_lines
        WHERE seller_offer_id IS NOT NULL AND fulfillment_node_id IS NOT NULL
        GROUP BY cart_id,seller_offer_id,fulfillment_node_id HAVING count(*)>1 LIMIT 1
    """)).first()
    if duplicate_lines:
        raise RuntimeError('Duplicate seller cart lines found; reconcile BEFORE migration 0017')


def upgrade():
    _preflight()
    op.create_index('uq_carts_user_open','carts',['user_id'],unique=True,
        postgresql_where=sa.text("status = 'open'"))
    op.create_index('uq_cart_seller_offer_node','cart_lines',
        ['cart_id','seller_offer_id','fulfillment_node_id'],unique=True,
        postgresql_where=sa.text('seller_offer_id IS NOT NULL AND fulfillment_node_id IS NOT NULL'))
    # Old CHECK name may depend on the historical naming convention. Find it,
    # and replace it regardless of the exact generated name.
    op.execute(sa.text("""
        DO $$ DECLARE old_name text; BEGIN
          SELECT c.conname INTO old_name FROM pg_constraint c
           JOIN pg_class r ON r.oid=c.conrelid
           WHERE r.relname='supplier_purchase_attempts' AND c.contype='c'
             AND pg_get_constraintdef(c.oid) LIKE '%dispatch_unknown%' LIMIT 1;
          IF old_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE supplier_purchase_attempts DROP CONSTRAINT %I',old_name);
          END IF;
        END $$
    """))
    op.create_check_constraint('ck_purchase_attempt_validating','supplier_purchase_attempts',
        "status IN ('prepared','validating','dispatch_unknown','accepted','rejected','manual_review')")
    op.create_index('ix_delivery_routes_active_source','delivery_routes',
        ['source_node_id','target_node_id'],postgresql_where=sa.text('active IS TRUE'))
    # Speeds up latest-job scope lookup in supplier details, after validating plans.
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_offer_details_search_job
        ON offer_details ((metadata_public ->> '_search_job_id'))
    """))


def downgrade():
    op.execute(sa.text('DROP INDEX IF EXISTS ix_offer_details_search_job'))
    op.drop_index('ix_delivery_routes_active_source',table_name='delivery_routes')
    op.drop_constraint('ck_purchase_attempt_validating','supplier_purchase_attempts',type_='check')
    op.create_check_constraint('purchase_attempt_status','supplier_purchase_attempts',
        "status IN ('prepared','dispatch_unknown','accepted','rejected','manual_review')")
    op.drop_index('uq_cart_seller_offer_node',table_name='cart_lines')
    op.drop_index('uq_carts_user_open',table_name='carts')
