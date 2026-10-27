"""Bounded, non-destructive audit of legacy inventory and supplier offer history.

Only static DB reads: no release, no deletion, no supplier calls. Snapshot pruning
requires separate reviewed operational tooling after PostgreSQL race acceptance.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.commerce.models import CartLine, OrderLine
from app.modules.marketplace.models import InventoryBalance
from app.modules.marketplace.procurement import SupplierPurchaseAttempt
from app.modules.suppliers.models import OfferSnapshot

router = APIRouter(prefix='/api/v1/admin/marketplace/data-integrity',
                   tags=['marketplace-commercial-integrity'],
                   dependencies=[Depends(require_admin)])


def stale_snapshot_candidates(*, older_than: datetime):
    """Protect ALL hard references and latest row per exact account/destination.

    No deletes here. Do not use count(distinct) on huge snapshot histories:
    ordering is indexed by the observed timestamp and returned list is bounded.
    """
    latest = aliased(OfferSnapshot)
    newer = select(latest.id).where(
        latest.supplier_item_id == OfferSnapshot.supplier_item_id,
        latest.supplier_account_id.is_not_distinct_from(OfferSnapshot.supplier_account_id),
        latest.supplier_destination_id.is_not_distinct_from(OfferSnapshot.supplier_destination_id),
        or_(latest.observed_at > OfferSnapshot.observed_at,
            and_(latest.observed_at == OfferSnapshot.observed_at, latest.id > OfferSnapshot.id)),
    ).correlate(OfferSnapshot).exists()
    in_orders = select(OrderLine.id).where(OrderLine.snapshot_id == OfferSnapshot.id).correlate(OfferSnapshot).exists()
    in_carts = select(CartLine.id).where(CartLine.validated_snapshot_id == OfferSnapshot.id).correlate(OfferSnapshot).exists()
    in_purchases = select(SupplierPurchaseAttempt.id).where(SupplierPurchaseAttempt.snapshot_id == OfferSnapshot.id).correlate(OfferSnapshot).exists()
    return select(OfferSnapshot).where(
        OfferSnapshot.observed_at < older_than,
        newer, ~in_orders, ~in_carts, ~in_purchases,
    ).order_by(OfferSnapshot.observed_at, OfferSnapshot.id)


@router.get('/offer-history')
async def offer_history(days: int = Query(default=180, ge=90, le=3650),
                        limit: int = Query(default=50, ge=1, le=100),
                        db: AsyncSession = Depends(get_session)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items = (await db.scalars(stale_snapshot_candidates(older_than=cutoff).limit(limit))).all()
    return {'read_only': True, 'deletion_enabled': False,
            'older_than_days': days, 'candidate_count_at_most': len(items),
            'limit': limit,
            'candidates': [{'id':str(s.id), 'supplier_item_id':str(s.supplier_item_id),
                            'observed_at':s.observed_at.isoformat() if s.observed_at else None}
                           for s in items],
            'note': 'Candidate != authorization to delete; no supplier snapshot is modified.'}


@router.get('/legacy-reservations')
async def legacy_reservations(limit: int = Query(default=100, ge=1, le=200),
                              db: AsyncSession = Depends(get_session)):
    """Bounded retrospective audit of historical v017 reservation_keys.

    We DO NOT release here: legacy mismatched balances require operator review.
    Scans at most 5000 most recent nonempty commercial snapshots per invocation.
    """
    rows = (await db.scalars(select(OrderLine).where(OrderLine.commercial_snapshot.is_not(None))
                             .order_by(OrderLine.id.desc()).limit(5000))).all()
    selected = []
    for line in rows:
        meta = line.commercial_snapshot or {}
        if meta.get('source_kind') == 'own_stock' and meta.get('reservation_key'):
            selected.append(line)
            if len(selected) >= limit:
                break
    from app.modules.marketplace.legacy_reservations_v0421 import analyze_legacy_lines
    entries = await analyze_legacy_lines(db, selected)
    return {'read_only':True,'scanned_at_most':5000,'result_limit':limit,
            'may_be_incomplete':len(rows)==5000 or len(selected)>=limit,
            'items':entries,'automatic_release':False,
            'note':'Review inventory movement history and exact order before any release.'}
