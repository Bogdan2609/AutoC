"""Admin-only read-only preproduction acceptance manifest.

Never turns on provider writes, changes feature flags, or reads credentials.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.preproduction_v044.assessment import GATES, SCHEMA

router = APIRouter(prefix='/api/v1/backoffice/preproduction-v044',
                   tags=['preproduction-v044'], dependencies=[Depends(require_admin)])


@router.get('/gates')
async def preproduction_gates():
    return {
        'schema': SCHEMA, 'read_only': True,
        'live_money_decision': 'NO_GO',
        'automated_go_possible': False,
        'gates': [{'id': g.code, 'name': g.title, 'kind': g.kind,
                   'required_evidence': g.evidence, 'status': 'EVIDENCE_NOT_VERIFIED'}
                  for g in GATES],
        'note': 'No stored external evidence or approvals are inferred from installed software.',
    }
