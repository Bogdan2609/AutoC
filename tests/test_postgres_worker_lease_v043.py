"""Disposable PostgreSQL acceptance, never point at the production database."""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.accounts.models import utc
from app.modules.commerce.models import Job
from app.workers.job_lease_v043 import heartbeat_once


@pytest.mark.asyncio
async def test_pg_heartbeat_generation_and_terminal_fence():
    url=os.getenv('AUTOCODEX_TEST_PG_URL', '')
    if not url:
        pytest.skip('Provide AUTOCODEX_TEST_PG_URL for disposable localhost PostgreSQL')
    if (not url.startswith('postgresql+psycopg://') or
        ('@127.0.0.1:' not in url and '@localhost:' not in url)):
        pytest.fail('Only disposable local PostgreSQL URL allowed')
    engine=create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Job.__table__.create, checkfirst=True)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        identifier=uuid4()
        async with factory() as session:
            session.add(Job(id=identifier,kind='search',payload={},dedup_key='v043-leasetest:'+str(identifier),
                            status='running',attempts=3,due_at=utc()+timedelta(seconds=30)))
            await session.commit()
        try:
            assert await heartbeat_once(factory,identifier,3,'search') is True
            assert await heartbeat_once(factory,identifier,2,'search') is False
            async with factory() as session:
                await session.execute(update(Job).where(Job.id==identifier).values(status='done'))
                await session.commit()
            assert await heartbeat_once(factory,identifier,3,'search') is False
        finally:
            async with factory() as session:
                await session.execute(delete(Job).where(Job.id==identifier))
                await session.commit()
    finally:
        await engine.dispose()
