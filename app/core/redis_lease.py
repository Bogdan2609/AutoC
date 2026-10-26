"""Token-owned Redis coalescing lease; never delete/extend another worker's key.

Not a financial exactly-once primitive: PostgreSQL state and provider GET are the
source of truth. Redis is only advisory request coalescing. A failed claim is
reported as retryable, never acknowledged as successful settlement.
"""
import asyncio
import secrets
from contextlib import suppress

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


class RedisLease:
    def __init__(self, redis, key: str, token: str, ttl: int):
        self.redis, self.key, self.token, self.ttl = redis, key, token, ttl
        self._task: asyncio.Task | None = None

    @classmethod
    async def acquire(cls, redis, key: str, ttl: int = 60):
        token = secrets.token_hex(32)
        if not await redis.set(key, token, nx=True, ex=ttl):
            return None
        lease = cls(redis, key, token, ttl)
        lease._task = asyncio.create_task(lease._heartbeat())
        return lease

    async def _heartbeat(self):
        try:
            while True:
                await asyncio.sleep(max(1, self.ttl // 3))
                if not await self.redis.eval(_RENEW, 1, self.key, self.token, self.ttl):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Expiry alone never authorizes a second financial side effect.
            return

    async def release(self):
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.redis.eval(_RELEASE, 1, self.key, self.token)
        except Exception:
            # TTL bounds stale lease lifetime if Redis is temporarily unavailable.
            pass
