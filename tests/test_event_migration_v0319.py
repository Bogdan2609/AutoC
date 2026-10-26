"""Check the new migration against an actual empty scratch SQL database."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.modules.commerce.models import OrderWorkflowEvent


def test_order_events_migration_creates_index_and_refuses_destructive_downgrade():
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261022_0027_order_workflow_events.py'
    spec = importlib.util.spec_from_file_location('migration_v0319', path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    engine = sa.create_engine('sqlite+pysqlite:///:memory:', poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables
                                               if t is not OrderWorkflowEvent.__table__])
    with engine.begin() as conn:
        mig.op = Operations(MigrationContext.configure(conn))
        mig.upgrade()
        inspected = sa.inspect(conn)
        assert 'order_workflow_events' in inspected.get_table_names()
        assert any(i['name']=='ix_order_workflow_event_order_created'
                   for i in inspected.get_indexes('order_workflow_events'))
        with pytest.raises(RuntimeError, match='append-only'):
            mig.downgrade()
    engine.dispose()
