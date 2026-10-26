"""Durable preflight checks and resumable draft-only jobs."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class DraftPreflight(Base):
    __tablename__ = 'catalog_draft_preflights'
    __table_args__ = (
        UniqueConstraint('candidate_id', name='uq_draft_preflight_candidate'),
        CheckConstraint("status IN ('eligible','review','materialized')", name='draft_preflight_status'),
        Index('ix_draft_preflight_status_candidate', 'status', 'candidate_id'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('catalog_bulk_candidates.id', ondelete='RESTRICT', name='fk_draft_pf_candidate'), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('multi_source_proposals.id', ondelete='RESTRICT'), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id', ondelete='SET NULL'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DraftRun(Base):
    __tablename__ = 'catalog_draft_runs'
    __table_args__ = (
        CheckConstraint("kind IN ('preflight','materialize','auto_pipeline')", name='draft_run_kind'),
        CheckConstraint("status IN ('queued','processing','completed','failed')", name='draft_run_status'),
        Index('uq_draft_active_run', 'active_key', unique=True,
              postgresql_where=text("status IN ('queued','processing')"),
              sqlite_where=text("status IN ('queued','processing')")),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    active_key: Mapped[str] = mapped_column(String(20), nullable=False, default='global')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DraftAudit(Base):
    __tablename__ = 'catalog_draft_audit'
    __table_args__ = (Index('ix_draft_audit_candidate_time', 'candidate_id', 'created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('catalog_bulk_candidates.id', ondelete='RESTRICT'), nullable=False)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('multi_source_proposals.id', ondelete='RESTRICT'), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('catalog_draft_runs.id', ondelete='SET NULL'))
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
