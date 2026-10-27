"""PostgreSQL-backed job heartbeat and generation fencing (no schema migration).

The attempt counter is a monotonic per-job generation.  Heartbeats use their
own session and conditional UPDATE.  Loss of ownership prevents final status
updates.  This is NOT a global transaction fence for writes performed inside
legacy job handlers; such work needs its own idempotency/fencing at the domain
boundary and is conservatively excluded from blind replay.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress, nullcontext
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update

from app.modules.accounts.models import utc
from app.modules.commerce.models import Job

LONG_JOBS = frozenset({
    'feed_import', 'feed_catalog_sync', 'catalog_bulk', 'catalog_enrichment',
    'multi_enrichment', 'draft_materialization',
})
# Only read-or-resumable jobs; do NOT auto replay financial or external writes.
REPLAYABLE = frozenset({
    'search', 'catalog_bulk', 'catalog_enrichment', 'multi_enrichment',
    'draft_materialization', 'payment_reconcile', 'refund_reconcile',
})
# Feed import is intentionally *not* replayed after ambiguous in-flight writes:
# it may have already committed partial batches. Feed sync requires operator QA.
EXTERNAL_WRITE_JOBS = frozenset({'procurement', 'refund'})
MAX_ATTEMPTS = 12


def lease_duration(kind: str) -> timedelta:
    return timedelta(hours=2) if kind in LONG_JOBS else timedelta(minutes=5)


def classify_job(kind: str, status: str, *, error_type: str | None = None) -> dict:
    """Explicitly separate operational attention from infrastructure failures."""
    if status == 'manual_review':
        return {'severity': 'action_required', 'code': 'manual_review', 'retry_safe': False}
    if status == 'failed':
        return {'severity': 'error', 'code': 'job_failed', 'retry_safe': False}
    if status == 'running':
        return {'severity': 'in_progress', 'code': 'running', 'retry_safe': False}
    if status == 'pending':
        return {'severity': 'queued', 'code': 'pending', 'retry_safe': kind in REPLAYABLE}
    return {'severity': 'normal', 'code': 'completed', 'retry_safe': False}


async def heartbeat_once(factory, job_id: UUID, generation: int, kind: str) -> bool:
    """Renew only the running generation, never resurrect an expired/fenced job."""
    async with factory() as session:
        now = utc()
        result = await session.execute(update(Job).where(
            Job.id == job_id, Job.status == 'running',
            Job.attempts == generation, Job.due_at > now,
        ).values(due_at=now + lease_duration(kind)))
        await session.commit()
        return result.rowcount == 1


async def still_owner(session, job_id: UUID, generation: int) -> bool:
    """Lock the current job row before final status; no stale job overwrite."""
    # no_autoflush avoids flushing the pending state transition first.
    with getattr(session, "no_autoflush", nullcontext()):
        found = await session.scalar(select(Job.id).where(
            Job.id == job_id, Job.status == 'running',
            Job.attempts == generation, Job.due_at > utc(),
        ).with_for_update())
    return found is not None


@asynccontextmanager
async def heartbeat(factory, job_id: UUID, generation: int, kind: str, *, interval: float = 20):
    """Begin heartbeat after durable claim; stop when processing exits."""
    lost = asyncio.Event()

    async def run():
        try:
            while not lost.is_set():
                await asyncio.sleep(interval)
                if not await heartbeat_once(factory, job_id, generation, kind):
                    lost.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Unknown DB state => fail closed at the next owned-state check.
            lost.set()

    task = asyncio.create_task(run(), name=f'autoc-job-heartbeat-{job_id}')
    try:
        yield lost
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
