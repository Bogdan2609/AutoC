from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.core.db import Base
from app.modules.finance import models as finance_models  # noqa: F401
from app.modules.finance import pricing_models as finance_pricing_models  # noqa: F401
from app.modules.finance import ledger_models as finance_ledger_models  # noqa: F401
from app.modules.finance import payment_sim_models as finance_f4_models  # noqa: F401
from app.modules.finance import register_models as finance_f5_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.master_catalog import models as master_catalog_models  # noqa: F401
from app.modules.catalog_ext import models as catalog_ext_models  # noqa: F401
from app.modules.pricing import models as pricing_models  # noqa: F401
from app.modules.suppliers import models as supplier_models  # noqa: F401
from app.modules.supplier_feeds import models as supplier_feed_models  # noqa: F401
from app.modules.catalog_bulk import models as catalog_bulk_models  # noqa: F401
from app.modules.catalog_enrichment import models as catalog_enrichment_models  # noqa: F401
from app.modules.multi_enrichment import models as multi_enrichment_models  # noqa: F401
from app.modules.multi_enrichment import taxonomy_db as classification_models  # noqa: F401
from app.modules.draft_materialization import models as draft_materialization_models  # noqa: F401

from app.modules.accounts import models as account_models  # noqa: F401
from app.modules.admin_staff import models as admin_staff_models  # noqa: F401
from app.modules.commerce import models as commerce_models  # noqa: F401
from app.modules.commerce import sale_lifecycle  # noqa: F401
from app.modules.payments import models as payment_models  # noqa: F401
from app.modules.offer_engine import models as cache_models  # noqa: F401
from app.modules.marketplace import models as marketplace_models  # noqa: F401
from app.modules.marketplace import procurement as marketplace_procurement  # noqa: F401
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A synchronous psycopg connection is intentional: Alembic runs in a separate process.
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
