"""Target-database concurrency regressions. Set TEST_DATABASE_URL only for a disposable local DB.

The normal offline test suite skips this because SQLite cannot reproduce row locks.
"""
import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.accounts.models import User
from app.modules.commerce.api import open_cart
from app.modules.commerce.models import Cart


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
async def test_concurrent_first_cart_is_unique_in_postgresql(client):
    url = os.environ['TEST_DATABASE_URL']
    assert url.startswith('postgresql+') and ('localhost' in url or '127.0.0.1' in url)
    engine = create_async_engine(url, pool_size=12, max_overflow=5, pool_timeout=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = uuid.uuid4()
    try:
        async with factory() as db:
            db.add(User(id=uid, email='audit-concurrency@example.org', password_hash='not-logged-in'))
            await db.commit()

        async def add_line():
            async with factory() as db:
                cart = await open_cart(db, SimpleNamespace(id=uid))
                await db.commit()
                return cart.id

        results = await asyncio.wait_for(asyncio.gather(*(add_line() for _ in range(10))), timeout=45)
        assert len(set(results)) == 1
        async with factory() as db:
            found = (await db.scalars(select(Cart).where(Cart.user_id == uid, Cart.status == 'open'))).all()
            assert len(found) == 1
    finally:
        await engine.dispose()
