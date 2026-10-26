"""SQLite fast HTTP tests; optional disposable PostgreSQL tests using TEST_DATABASE_URL."""
import os
import socket

# Fail-closed test config takes precedence over any real .env present on disk.
os.environ["APP_ENV"]="test"
os.environ["SUPPLIER_API_ENABLED"]="false"
os.environ["SUPPLIER_PROCUREMENT_ENABLED"]="false"
os.environ["REAL_SUPPLIER_CHECKOUT_ENABLED"]="false"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.db import Base, get_session
from app.main import app
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.pricing import models as pricing_models  # noqa: F401
from app.modules.suppliers import models as suppliers_models  # noqa: F401
from app.modules.accounts import models as accounts_models  # noqa: F401
from app.modules.finance import models as finance_models  # noqa: F401
from app.modules.finance import pricing_models as finance_pricing_models  # noqa: F401
from app.modules.finance import ledger_models as finance_ledger_models  # noqa: F401
from app.modules.finance import payment_sim_models as finance_f4_models  # noqa: F401
from app.modules.finance import register_models as finance_f5_models  # noqa: F401
from app.modules.commerce import models as commerce_models  # noqa: F401
from app.modules.commerce import sale_lifecycle  # noqa: F401
from app.modules.payments import models as payments_models  # noqa: F401
from app.modules.offer_engine import models as cache_models  # noqa: F401


from app.modules.marketplace import models as marketplace_models  # noqa: F401
from app.modules.supplier_feeds import models as supplier_feed_models  # noqa: F401
from app.modules.catalog_bulk import models as catalog_bulk_models  # noqa: F401
from app.modules.catalog_enrichment import models as enrichment_models  # noqa: F401
from app.modules.multi_enrichment import models as multi_enrichment_models  # noqa: F401
from app.modules.multi_enrichment import taxonomy_db as classification_models  # noqa: F401
from app.modules.draft_materialization import models as draft_materialization_models  # noqa: F401
from app.modules.marketplace import procurement as marketplace_procurement  # noqa: F401

class AsyncSessionFacade:
    """Minimal async façade on SQLite's real sync SQLAlchemy Session for smoke tests.

    This is intentionally test-only. Production always uses real AsyncSession.
    """

    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params) if params is not None else self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def get(self, entity, identity):
        return self.session.get(entity, identity)

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, entity):
        self.session.refresh(entity)

    async def flush(self):
        self.session.flush()

    async def delete(self, entity):
        self.session.delete(entity)

    def add(self, entity):
        self.session.add(entity)

    def add_all(self, entities):
        self.session.add_all(entities)


@pytest.fixture(autouse=True)
def prohibit_external_network(monkeypatch):
    """Regression tests never reach a real supplier, payment gateway or Internet host."""
    original=socket.socket.connect
    original_ex=socket.socket.connect_ex
    def safe_connect(self,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            raise AssertionError('External network prohibited in tests')
        return original(self,address)
    def safe_connect_ex(self,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            raise AssertionError('External network prohibited in tests')
        return original_ex(self,address)
    monkeypatch.setattr(socket.socket,'connect',safe_connect)
    monkeypatch.setattr(socket.socket,'connect_ex',safe_connect_ex)

@pytest.fixture
def admin_headers():
    return {"X-Admin-Token": "test-token"}


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-token")
    get_settings.cache_clear()
    pg_url = os.environ.get("TEST_DATABASE_URL")
    if pg_url:
        if not pg_url.startswith("postgresql+") or not (
            "localhost" in pg_url or "127.0.0.1" in pg_url
        ):
            pytest.fail("TEST_DATABASE_URL must be local disposable PostgreSQL")
        engine = create_async_engine(pg_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_session():
            async with factory() as session:
                yield session

        async def clean_up():
            await engine.dispose()
    else:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)

        async def override_session():
            with Session(engine, expire_on_commit=False) as session:
                yield AsyncSessionFacade(session)

        async def clean_up():
            engine.dispose()

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        await clean_up()
        get_settings.cache_clear()
