"""5+ account directions must be served fairly without API fanout explosion."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest

from app.modules.accounts.models import utc
from app.modules.marketplace import account_search


@pytest.mark.asyncio
async def test_six_account_destinations_rotate_after_first_four(monkeypatch):
    now = utc()
    rows = []
    states = []
    for ix in range(6):
        account, dest = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
        rows.append((account, dest, SimpleNamespace(city='Самара')))
        states.append(SimpleNamespace(
            lookup_key=account_search.account_key('TRIALLI', 'CF705', account.id, dest.id),
            status='empty', expires_at=now - timedelta(minutes=1),
            last_requested_at=now - timedelta(days=ix+1)))

    async def destinations(db, code, *, city=None):
        assert code == 'rossko'
        return rows, True

    class Db:
        async def scalars(self, stmt):
            return SimpleNamespace(all=lambda: states)
        async def execute(self, stmt):
            raise AssertionError('Unexpected extra query')

    monkeypatch.setattr(account_search, 'available_destinations', destinations)
    db = Db()
    first = await account_search.refresh_candidates(db, 'rossko', 'TRIALLI', 'CF705')
    assert len(first) == 4
    assert len({d.id for _, d, _, _, _ in first}) == 4
    for _, dest, _, _, state in first:
        state.status = 'refreshing'
        state.expires_at = utc() + timedelta(minutes=4)
    second = await account_search.refresh_candidates(db, 'rossko', 'TRIALLI', 'CF705')
    assert len(second) == 2
    assert not {d.id for _, d, _, _, _ in first} & {d.id for _, d, _, _, _ in second}


@pytest.mark.asyncio
async def test_account_refresh_pending_count_uses_all_suppliers(monkeypatch):
    calls=[]
    async def candidates(db, code, brand, article, **kwargs):
        calls.append(code)
        return [(1,)]*3 if code=='rossko' else []
    monkeypatch.setattr(account_search, 'refresh_candidates', candidates)
    assert await account_search.pending_account_refreshes(None, 'TRIALLI', 'CF705') == 3
    assert calls == ['rossko', 'adeo', 'partkom', 'forum-auto']
