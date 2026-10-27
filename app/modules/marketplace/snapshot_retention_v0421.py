"""Manual, narrowly scoped, bounded retention of unreferenced offer history.

Default is READ-ONLY. Database FK constraints are an additional final guard:
concurrent creation of an OrderLine, CartLine or PurchaseAttempt never silently
loses its referenced snapshot. Latest observation per item/account/destination
is preserved, including unscoped legacy identities.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from app.modules.suppliers.models import OfferSnapshot
from app.modules.marketplace.data_integrity import stale_snapshot_candidates

MIN_RETENTION_DAYS = 180
MAX_BATCH = 100


def cutoff_for_days(days: int, *, now: datetime | None = None) -> datetime:
    if type(days) is not int or not MIN_RETENTION_DAYS <= days <= 3650:
        raise ValueError('Retention must be 180..3650 days')
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError('Retention now must be timezone-aware')
    return instant - timedelta(days=days)


def candidate_ids(cutoff: datetime, limit: int, *, lock: bool = False):
    if cutoff.tzinfo is None or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError('Invalid snapshot retention bounds')
    statement = stale_snapshot_candidates(older_than=cutoff).with_only_columns(OfferSnapshot.id).limit(limit)
    if lock:
        # PostgreSQL SKIP LOCKED prevents competing maintenance sessions from
        # selecting the same offer. No lock is held on any buyer order.
        statement = statement.with_for_update(of=OfferSnapshot, skip_locked=True)
    return statement


async def retention_batch(db, *, days: int = 180, limit: int = 50,
                          apply: bool = False, approved: bool = False):
    """One transaction, no external network; caller owns committing/rollback.

    No mutations in preview. Apply requires an explicit operator approval,
    then rechecks original eligibility in DELETE as defense in depth.
    """
    cutoff = cutoff_for_days(days)
    if apply and not approved:
        raise PermissionError('Explicit operator approval is required')
    if apply:
        # Snapshot retention relies on PostgreSQL FK locking and SKIP LOCKED.
        # Refuse SQLite or an unrecognized test facade even when confirmed.
        try:
            dialect = db.get_bind().dialect.name
        except (AttributeError, RuntimeError):
            dialect = None
        if dialect != 'postgresql':
            raise PermissionError('Retention deletion requires PostgreSQL')
    ids = (await db.scalars(candidate_ids(cutoff, limit, lock=apply))).all()
    if not apply or not ids:
        return {'dry_run': not apply, 'candidates': [str(v) for v in ids],
                'deleted': 0, 'days': days, 'limit': limit}
    eligible_ids = stale_snapshot_candidates(older_than=cutoff).with_only_columns(OfferSnapshot.id)
    stmt = (delete(OfferSnapshot).where(OfferSnapshot.id.in_(ids),
                                      OfferSnapshot.id.in_(eligible_ids))
            .returning(OfferSnapshot.id))
    removed = (await db.scalars(stmt)).all()
    # FK RESTRICT on order/cart/attempt links prevents concurrent lost evidence.
    return {'dry_run': False, 'candidates': [str(v) for v in ids],
            'deleted': len(removed), 'days': days, 'limit': limit}
