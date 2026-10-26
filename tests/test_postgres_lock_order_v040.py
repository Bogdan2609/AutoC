"""Real PostgreSQL row-lock contention with independent async sessions.

Requires TEST_DATABASE_URL pointing to a disposable LOCAL PostgreSQL database.
The shared `client` fixture drops and recreates that database schema.
"""
import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.accounts.models import User
from app.modules.commerce.models import Order


@pytest.mark.asyncio
async def test_same_user_checkout_lock_serializes_idempotency_key(client):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Real PostgreSQL required (disposable TEST_DATABASE_URL)')
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    email = f'v040-race-{user_id}@example.test'
    key = f'v040-race-{user_id}'
    try:
        async with sessions() as setup:
            setup.add(User(id=user_id, email=email, password_hash='test', display_name='Test buyer'))
            await setup.commit()
        async with sessions() as writer1, sessions() as writer2:
            first = await writer1.scalar(select(User).where(User.id == user_id).with_for_update())
            assert first is not None
            acquired = asyncio.Event()

            async def second_checkout():
                assert await writer2.scalar(select(User).where(User.id == user_id)
                                            .with_for_update()) is not None
                acquired.set()
                return await writer2.scalar(select(Order).where(
                    Order.user_id == user_id, Order.idempotency_key == key))

            competing = asyncio.create_task(second_checkout())
            await asyncio.sleep(0.2)
            assert not acquired.is_set(), 'Second transaction bypassed user row lock'
            writer1.add(Order(user_id=user_id, idempotency_key=key, total=Decimal('5.00'),
                              status='awaiting_sbp', contact='Test buyer', phone='+79990000000',
                              shipping_address='Test address, building 1'))
            await writer1.commit()
            existing = await asyncio.wait_for(competing, timeout=5)
            assert existing is not None and existing.idempotency_key == key
            await writer2.rollback()
    finally:
        await engine.dispose()
