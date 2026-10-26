"""Bounded retries and a circuit breaker for idempotent supplier READ operations.

NEVER use with create_order, checkout, refund or other external writes: an
ambiguous timeout requires durable reconciliation, not a second submit.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar
import httpx
from .supplier_errors import NetworkError, UpstreamBusinessError, Unavailable

T = TypeVar('T')


def classify_http(response: httpx.Response) -> None:
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise NetworkError('Supplier temporarily unavailable')
    if response.status_code >= 400:
        raise UpstreamBusinessError('Supplier request rejected')


@dataclass
class CircuitBreaker:
    threshold: int = 4
    reset_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    probe_inflight: bool = False

    def enter(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at < self.reset_seconds or self.probe_inflight:
            raise NetworkError('Supplier circuit is open')
        self.probe_inflight = True
        return True

    def finish(self, probe: bool, *, success: bool, retryable: bool = True) -> None:
        if probe:
            self.probe_inflight = False
        if success or not retryable:
            self.failures = 0
            self.opened_at = None
        elif probe:
            self.failures = self.threshold
            self.opened_at = time.monotonic()
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


async def bounded_read(
    operation: Callable[[], Awaitable[T]], *, breaker: CircuitBreaker,
    attempts: int = 3, total_timeout: float = 18.0,
    base_delay: float = 0.15, sleeper=asyncio.sleep, jitter=random.uniform,
) -> T:
    if attempts < 1 or total_timeout <= 0:
        raise ValueError('Invalid supplier read budget')
    probe = breaker.enter()
    async def task() -> T:
        for attempt in range(attempts):
            try:
                result = await operation()
                breaker.finish(probe, success=True)
                return result
            except (httpx.TimeoutException, httpx.TransportError, TimeoutError) as exc:
                failure = NetworkError('Supplier transport failed')
                # Never chain httpx exceptions: query URLs may contain Forum credentials.
            except NetworkError as exc:
                failure = exc
            except Unavailable:
                breaker.finish(probe, success=False, retryable=False)
                raise
            except Exception:
                breaker.finish(probe, success=False, retryable=False)
                raise
            if attempt == attempts - 1 or probe:
                breaker.finish(probe, success=False)
                raise failure from None
            await sleeper(base_delay * (2 ** attempt) + jitter(0, base_delay))
        raise AssertionError('Unreachable')
    try:
        return await asyncio.wait_for(task(), timeout=total_timeout)
    except TimeoutError as exc:
        breaker.finish(probe, success=False)
        raise NetworkError('Supplier read budget exceeded') from exc


BREAKERS: dict[str, CircuitBreaker] = {}

def protected_search(adapter_class):
    """Decorator applied to real supplier search methods, per-process per code."""
    original = adapter_class.search
    async def search(self, brand, article, config=None):
        breaker = BREAKERS.setdefault(self.code, CircuitBreaker())
        return await bounded_read(lambda: original(self, brand, article, config), breaker=breaker)
    adapter_class.search = search
    return adapter_class
