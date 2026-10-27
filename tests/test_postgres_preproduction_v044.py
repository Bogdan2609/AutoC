"""Acceptance-level PG SKIP LOCKED and stale-generation verification.

Only disposable, explicitly named PostgreSQL database. Does not drop DB tables,
no access to production, bank, supplier, or real buyer data.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.accounts.models import utc
from app.modules.commerce.models import Job
from app.workers.job_lease_v043 import heartbeat_once


@pytest.mark.asyncio
async def test_disposable_postgres_skipped_locked_and_stale_heartbeat():
    url = os.environ.get('AUTOCODEX_TEST_PG_URL', '')
    if not url:
        pytest.skip('Set AUTOCODEX_TEST_PG_URL for disposable autoc_test_v044')
    parsed = urlsplit(url.replace('postgresql+psycopg://','postgresql://',1))
    if (not url.startswith('postgresql+psycopg://') or
        parsed.hostname not in ('127.0.0.1', 'localhost') or
        parsed.path != '/autoc_test_v044'):
        pytest.fail('Database must be disposable local PostgreSQL named autoc_test_v044')
    engine = create_async_engine(url, pool_pre_ping=True)
    record_id = uuid4()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Job.__table__.create, checkfirst=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(Job(id=record_id,kind='search',payload={},
                            dedup_key='v044-acceptance:'+str(record_id),
                            status='running',attempts=2,due_at=utc()+timedelta(minutes=1)))
            await session.commit()
        try:
            async with sessions() as holder:
                async with holder.begin():
                    locked = await holder.scalar(select(Job).where(Job.id==record_id).with_for_update())
                    assert locked is not None
                    async with sessions() as competitor:
                        # A second worker must skip the row, not wait for it.
                        missing = await asyncio.wait_for(
                            competitor.scalar(select(Job).where(Job.id==record_id)
                                              .with_for_update(skip_locked=True)), 5)
                        assert missing is None
            assert await heartbeat_once(sessions, record_id, 1, 'search') is False
            assert await heartbeat_once(sessions, record_id, 2, 'search') is True
            async with sessions() as session:
                await session.execute(update(Job).where(Job.id==record_id)
                                      .values(status='manual_review', attempts=3))
                await session.commit()
            assert await heartbeat_once(sessions, record_id, 2, 'search') is False
        finally:
            async with sessions() as session:
                await session.execute(delete(Job).where(Job.id==record_id))
                await session.commit()
    finally:
        await engine.dispose()
