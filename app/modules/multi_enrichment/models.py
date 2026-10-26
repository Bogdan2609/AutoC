"""Persistent v0.29 proposals, source evidence snapshots and review decisions."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON,
                        String, Text, UniqueConstraint, Uuid, text)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class MultiSourceProposal(Base):
    __tablename__ = 'multi_source_proposals'
    __table_args__ = (
        UniqueConstraint('candidate_id', name='uq_multi_source_candidate'),
        CheckConstraint("status IN ('proposed','review','approved','applied','rejected')", name='multi_source_proposal_status'),
        Index('ix_multi_source_proposal_status', 'status', 'candidate_id'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('catalog_bulk_candidates.id', ondelete='RESTRICT'), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id', ondelete='SET NULL'))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category_code: Mapped[str | None] = mapped_column(String(80))
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('catalog_categories.id', ondelete='SET NULL'))
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conflicts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='proposed')
    review_note: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MultiSourceRun(Base):
    __tablename__ = 'multi_source_runs'
    __table_args__ = (
        CheckConstraint("kind IN ('analyze','approve_safe','prepare_drafts','apply')", name='multi_source_run_kind'),
        CheckConstraint("status IN ('queued','processing','completed','failed')", name='multi_source_run_status'),
        Index('uq_multi_source_active_run', 'active_key', unique=True,
              postgresql_where=text("status IN ('queued','processing')"),
              sqlite_where=text("status IN ('queued','processing')")),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    active_key: Mapped[str] = mapped_column(String(20), nullable=False, default='global')
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='queued')
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MultiSourceDecision(Base):
    __tablename__ = 'multi_source_decisions'
    __table_args__ = (Index('ix_multi_source_decision_proposal', 'proposal_id', 'created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('multi_source_proposals.id', ondelete='RESTRICT'), nullable=False)
    old_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
