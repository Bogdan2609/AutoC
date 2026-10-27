"""Conservative v0.44 release-evidence assessment.

Evidence is an inventory of observed artifacts, not an authorization to pay,
refund or buy parts. No external provider calls, subprocesses, or DB writes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SCHEMA = 'autocodex.preproduction.v044.1'


@dataclass(frozen=True)
class Gate:
    code: str
    title: str
    kind: str
    evidence: str


GATES = (
    Gate('G44-01A', 'Docker build, health, ready and frontend', 'technical', 'Build logs, compose ps, /readyz and frontend smoke'),
    Gate('G44-01B', 'Alembic upgrade from anonymized v0.39 copy + rollback drill', 'technical', 'Disposable DB migration and rollback evidence'),
    Gate('G44-01C', 'Full SQLite unit/HTTP regression', 'technical', 'JUnit XML, test count and commit'),
    Gate('G44-01D', 'PostgreSQL contention, leases and lock order', 'technical', 'Disposable PG 18 race tests and worker crash exercise'),
    Gate('G44-02A', 'SBP YooKassa sandbox create/return/webhook/reconciliation', 'external', 'Isolated sandbox transactions: success, canceled, unknown, replay'),
    Gate('G44-02B', 'SBP refunds including partial/uncertain and fiscal receipts', 'external', 'Provider-verified sandbox refund and receipt evidence'),
    Gate('G44-03A', 'ROSSKO account/test mode, courier and one-shot checkout', 'external', 'Portal mode, read-only checkout details, verified courier address and idempotency'),
    Gate('G44-03B', 'ADEO/PARTKOM/FORUM-AUTO courier and order contracts', 'external', 'Documented create/readback/cancel behavior and explicit courier destination'),
    Gate('G44-04A', 'Seller, supplier contracts and banking prerequisites', 'legal', 'Verified seller entity, contracts, SBP terms and approved account setup'),
    Gate('G44-04B', 'Fiscalization, VAT, privacy and refund policy', 'legal', '54-FZ applicability, receipt operator, tax regime, personal data requirements'),
    Gate('G44-05A', 'Ingress/TLS/RBAC/rate limits/security/secret rotation', 'technical', 'Staging deployment security and adversarial HTTP checks'),
    Gate('G44-05B', 'Performance, supplier quotas and data integrity at scale', 'technical', 'Query plans, DB growth, feed import, quotas, p95/p99, consistency'),
    Gate('G44-05C', 'Backup/restore, loss-of-ack and chaos/worker restart', 'technical', 'Backup restoration to disposable instance, chaos and reconciliation drill'),
    Gate('G44-06A', 'Operator dashboard, alerts and manual-resolution runbooks', 'technical', 'Account/operator role acceptance and monitor screenshots, redacted'),
    Gate('G44-06B', 'Human preproduction acceptance and staged activation plan', 'governance', 'Signed authorization by responsible business/security/ops owners'),
)

REQUIRED = frozenset(g.code for g in GATES)
_MAX_EVIDENCE = 256


def sha256_file(path: Path, *, limit: int = 12 * 1024 * 1024) -> str:
    """Hash bounded evidence without dumping it to logs. Larger files are not accepted."""
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Evidence file missing or exceeds 12 MiB')
    h = hashlib.sha256()
    with path.open('rb') as f:
        while block := f.read(64 * 1024):
            h.update(block)
    return h.hexdigest()


def junit_summary(path: Path) -> dict[str, Any]:
    """Read JUnit only from explicitly supplied artifacts; never launch tests."""
    if path.stat().st_size > 12 * 1024 * 1024:
        raise ValueError('JUnit exceeds 12 MiB')
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError('Invalid JUnit XML') from exc
    if root.tag not in ('testsuite', 'testsuites'):
        raise ValueError('JUnit root must be testsuite(s)')
    suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
    if not suites:
        raise ValueError('JUnit contains no test suites')
    count = 0
    failed = 0
    skipped = 0
    for suite in suites:
        if suite.tag != 'testsuite':
            raise ValueError('Unexpected JUnit structure')
        for case in suite.findall('.//testcase'):
            count += 1
            failed += int(bool(case.find('failure') is not None or case.find('error') is not None))
            skipped += int(case.find('skipped') is not None)
    if count < 1:
        raise ValueError('JUnit has no testcases')
    return {'tests': count, 'passed': count - failed - skipped,
            'failed': failed, 'skipped': skipped, 'sha256': sha256_file(path)}


def empty_evidence() -> list[dict[str, Any]]:
    return [dict(code=g.code, title=g.title, category=g.kind, evidence_required=g.evidence,
                 evidence_files=[], status='MISSING_EVIDENCE') for g in GATES]


def assess(evidence: dict[str, list[dict[str, str]]] | None = None,
           *, junit: Path | None = None, source_version: str | None = None) -> dict[str, Any]:
    """Record *presence* of evidence only; never self-authorize a GO.

    Evidence format: {gate_code: [{name, sha256, note?}]}; file bytes were
    independently hashed by the collector. Arbitrary statuses are ignored.
    A human must review facts, scope and contracts outside this report.
    """
    evidence = evidence or {}
    if (not isinstance(evidence, dict) or len(evidence) > _MAX_EVIDENCE or
            set(evidence) - REQUIRED):
        raise ValueError('Invalid evidence mapping')
    report = empty_evidence()
    for row in report:
        items = evidence.get(row['code'], [])
        if not isinstance(items, list) or len(items) > 20:
            raise ValueError('Invalid evidence list')
        validated = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('Invalid evidence item')
            name = item.get('name', '')
            digest = item.get('sha256', '')
            if (not isinstance(name, str) or len(name) > 160 or
                    not isinstance(digest, str) or len(digest) != 64 or
                    any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('Invalid evidence identifier/digest')
            validated.append({'name': name, 'sha256': digest})
        row['evidence_files'] = validated
        if validated:
            row['status'] = 'REQUIRES_HUMAN_VALIDATION'
    junit_data = junit_summary(junit) if junit else None
    if junit_data and junit_data['failed'] == 0 and junit_data['skipped'] == 0:
        # Only proof of a particular test run; not a claim about all other gates.
        test_row = next(r for r in report if r['code'] == 'G44-01C')
        test_row['evidence_files'].append({'name': junit.name, 'sha256': junit_data['sha256']})
        test_row['status'] = 'REQUIRES_HUMAN_VALIDATION'
    return {
        'schema': SCHEMA,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_version': source_version,
        'live_money_decision': 'NO_GO',
        'automatic_approval_possible': False,
        'safe_external_writes_allowed': False,
        'governance_note': 'A complete technical report never authorizes live money; external and legal approvals are independent.',
        'evidence_count': sum(len(x['evidence_files']) for x in report),
        'missing_gate_count': sum(x['status'] == 'MISSING_EVIDENCE' for x in report),
        'human_validation_pending_count': sum(x['status'] == 'REQUIRES_HUMAN_VALIDATION' for x in report),
        'junit': junit_data,
        'gates': report,
    }


def load_evidence_manifest(path: Path, *, base_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Evidence must be relative to evidence folder; no arbitrary reads or symlinks."""
    if path.stat().st_size > 64 * 1024:
        raise ValueError('Evidence manifest exceeds limit')
    raw = path.read_bytes()
    if len(raw) > 64 * 1024:
        raise ValueError('Evidence manifest exceeds limit')
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid evidence manifest JSON') from exc
    if not isinstance(data, dict) or set(data) - REQUIRED:
        raise ValueError('Unknown evidence gate')
    base = base_dir.resolve(strict=True)
    verified: dict[str, list[dict[str, str]]] = {}
    for code, filenames in data.items():
        if not isinstance(filenames, list) or len(filenames) > 20:
            raise ValueError('Evidence entries must be lists')
        verified[code] = []
        for name in filenames:
            if not isinstance(name, str) or not name or len(name) > 160:
                raise ValueError('Unsafe evidence name')
            candidate = Path(name)
            if candidate.is_absolute() or '..' in candidate.parts:
                raise ValueError('Unsafe evidence path')
            selected = base / candidate
            if selected.is_symlink() or not selected.resolve().is_relative_to(base):
                raise ValueError('Unsafe evidence link')
            verified[code].append({'name': name, 'sha256': sha256_file(selected)})
    return verified
