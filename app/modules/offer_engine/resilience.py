"""Per-account circuit breakers and bounded retry for supplier READ operations.

Do not wrap order submission, cancellation, checkout, payment or refund in
bounded_read. An ambiguous external WRITE requires reconciliation, not retry.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Awaitable, Callable, TypeVar

import httpx

from .supplier_errors import NetworkError, Unavailable, UpstreamBusinessError

T = TypeVar('T')


def classify_http(response: httpx.Response) -> None:
    # No exception / response body attached: URLs may include supplier secrets.
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
    base_delay: float = 0.15,
    sleeper=asyncio.sleep, jitter=random.uniform,
) -> T:
    if attempts < 1 or total_timeout <= 0 or base_delay < 0:
        raise ValueError('Invalid supplier read budget')
    probe = breaker.enter()
    finished = False

    def finish(*, success: bool, retryable: bool = True) -> None:
        nonlocal finished
        if not finished:
            breaker.finish(probe, success=success, retryable=retryable)
            finished = True

    async def task() -> T:
        for attempt in range(attempts):
            try:
                value = await operation()
            except (httpx.TimeoutException, httpx.TransportError, TimeoutError):
                failure = NetworkError('Supplier transport failed')
            except NetworkError as error:
                failure = error
            except Unavailable:
                finish(success=False, retryable=False)
                raise
            except Exception:
                finish(success=False, retryable=False)
                raise
            else:
                finish(success=True)
                return value
            if attempt == attempts - 1 or probe:
                finish(success=False)
                raise failure from None
            await sleeper(base_delay * (2 ** attempt) + jitter(0, base_delay))
        raise AssertionError('Unreachable')

    try:
        return await asyncio.wait_for(task(), timeout=total_timeout)
    except asyncio.CancelledError:
        # Prevent a cancelled half-open probe from blocking this account forever.
        finish(success=False)
        raise
    except TimeoutError:
        finish(success=False)
        raise NetworkError('Supplier read budget exceeded') from None


# A breaker is shared only within its account/destination. A broken account A
# must not open the circuit of another account B of the same supplier.
BREAKERS: dict[tuple[str, str], CircuitBreaker] = {}


def protected_search(adapter_class):
    original = adapter_class.search

    @wraps(original)
    async def search(self, brand, article, config=None):
        # This value is an internal UUID-based scope, *never* a credential.
        # Legacy/default provider calls intentionally have their own scope.
        scope = getattr(config, 'supplier_account_scope', None) if config is not None else None
        key = (self.code, str(scope) if scope is not None else 'default')
        breaker = BREAKERS.setdefault(key, CircuitBreaker())
        return await bounded_read(
            lambda: original(self, brand, article, config), breaker=breaker)

    adapter_class.search = search
    return adapter_class
