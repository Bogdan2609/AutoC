"""Optional REAL PostgreSQL two-connection race test on a disposable DB ONLY.

Set AUTOCODEX_TEST_PG_URL explicitly to a local database whose name starts
with autoc_test_. Main application DATABASE_URL is never used.
"""
import os
import uuid
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.getenv('AUTOCODEX_TEST_PG_URL', '')


def safe_url(url):
    if not url:
        return False
    value=urlparse(url)
    return (value.scheme == 'postgresql+psycopg' and value.hostname in
            ('localhost','127.0.0.1') and value.path.startswith('/autoc_test_')
            and value.path.count('/')==1)


@pytest.mark.asyncio
async def test_disposable_postgres_two_connection_skip_locked():
    if not URL:
        pytest.skip('Set AUTOCODEX_TEST_PG_URL for disposable PostgreSQL acceptance')
    if not safe_url(URL):
        pytest.fail('Refusing non-local or non-disposable PostgreSQL database')
    engine=create_async_engine(URL,pool_size=2,max_overflow=0)
    schema='autoc_test_lock_'+uuid.uuid4().hex[:16]
    try:
        async with engine.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
            await c.execute(text(f'CREATE TABLE "{schema}".stock (id int PRIMARY KEY, available int NOT NULL)'))
            await c.execute(text(f'INSERT INTO "{schema}".stock VALUES (1, 1)'))
        async with engine.connect() as a, engine.connect() as b:
            txa=await a.begin()
            txb=await b.begin()
            try:
                held=(await a.execute(text(f'SELECT id FROM "{schema}".stock WHERE id=1 FOR UPDATE'))).scalar_one()
                skipped=(await b.execute(text(f'SELECT id FROM "{schema}".stock WHERE id=1 FOR UPDATE SKIP LOCKED'))).scalar_one_or_none()
                assert held==1 and skipped is None
                await a.execute(text(f'UPDATE "{schema}".stock SET available=0 WHERE id=1'))
                await txa.commit()
                after=(await b.execute(text(f'SELECT available FROM "{schema}".stock WHERE id=1'))).scalar_one()
                assert after==0
                await txb.commit()
            finally:
                if txa.is_active:await txa.rollback()
                if txb.is_active:await txb.rollback()
    finally:
        async with engine.begin() as c:
            await c.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
