"""Strong-evidence legacy reservation reconciliation for operators, read-only.

Legacy v017 used an InventoryMovement key, not a per-reservation ledger table.
A balance's aggregate reserved quantity alone cannot prove that a particular
order still owns it. Never release stock from these reports automatically.
"""
from __future__ import annotations
from uuid import UUID
from sqlalchemy import select
from app.modules.marketplace.models import InventoryBalance, InventoryMovement
from app.modules.commerce.models import OrderLine


def release_key(order_id, line_id):
    return f'v017-release:{order_id}:{line_id}'


def classify_reserve(*, source_kind, reservation_key, balance_id, quantity,
                     reservation, release, balance, matching_line_count):
    if source_kind != 'own_stock' or not reservation_key:
        return 'not_legacy_reserve'
    if not balance_id or type(quantity) is not int or quantity <= 0 or matching_line_count != 1:
        return 'manual_review_identity_ambiguous'
    if not reservation or reservation.idempotency_key != reservation_key or \
       reservation.balance_id != balance_id or reservation.delta_reserved != quantity or \
       reservation.reason != 'reserve':
        return 'manual_review_original_evidence_invalid'
    if release is not None:
        if (release.balance_id != balance_id or release.delta_reserved != -quantity or
                release.reason not in ('v017_request_expired', 'v017_request_cancelled')):
            return 'manual_review_release_evidence_invalid'
        return 'released'
    if not balance:
        return 'manual_review_missing_balance'
    if balance.reserved < quantity:
        return 'manual_review_balance_shortfall'
    return 'unreleased_requires_review'


async def analyze_legacy_lines(db, lines):
    """Batch all movement/balance reads. No row locks, no UPDATE statements."""
    keys, balance_ids = set(), set()
    refs = {}
    for line in lines:
        meta = line.commercial_snapshot or {}
        key = meta.get('reservation_key')
        if not isinstance(key, str) or not key:
            continue
        keys.add(key)
        keys.add(release_key(line.order_id, line.id))
        try:
            balance_id = UUID(str(meta['inventory_balance_id']))
            balance_ids.add(balance_id)
        except (KeyError, ValueError, TypeError, AttributeError):
            balance_id = None
        refs[line.id] = (key, balance_id)
    movements = {x.idempotency_key: x for x in (await db.scalars(select(InventoryMovement).where(
        InventoryMovement.idempotency_key.in_(keys)))).all()} if keys else {}
    balances = {x.id: x for x in (await db.scalars(select(InventoryBalance).where(
        InventoryBalance.id.in_(balance_ids)))).all()} if balance_ids else {}
    # Key reuse can occur anywhere in old commercial snapshots; GROUP BY across
    # all lines in one query detects collisions outside the bounded report page.
    from sqlalchemy import func
    candidate_keys = {key for key, _ in refs.values()}
    usage = {}
    if candidate_keys:
        key_expression = OrderLine.commercial_snapshot['reservation_key'].as_string()
        rows = (await db.execute(select(key_expression, func.count()).where(
            key_expression.in_(candidate_keys)).group_by(key_expression))).all()
        usage = {key: int(count) for key, count in rows}
    result = []
    for line in lines:
        if line.id not in refs:
            continue
        key, bid = refs[line.id]
        status = classify_reserve(source_kind=(line.commercial_snapshot or {}).get('source_kind'),
            reservation_key=key, balance_id=bid, quantity=line.quantity,
            reservation=movements.get(key), release=movements.get(release_key(line.order_id,line.id)),
            balance=balances.get(bid), matching_line_count=usage.get(key, 0))
        result.append({'order_line_id': str(line.id), 'order_id': str(line.order_id),
                       'balance_id': str(bid) if bid else str((line.commercial_snapshot or {}).get('inventory_balance_id') or ''),
                       'quantity': line.quantity, 'status': status, 'automatic_release': False})
    return result
