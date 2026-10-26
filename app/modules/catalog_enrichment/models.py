"""Auditable, non-public enrichment suggestions for existing catalog drafts."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Integer,
                        String, Text, JSON, UniqueConstraint, Uuid, text)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class EnrichmentProposal(Base):
    __tablename__='catalog_enrichment_proposals'
    __table_args__=(
        UniqueConstraint('product_id',name='uq_enrichment_product'),
        CheckConstraint("status IN ('proposed','review','approved','applied','rejected')",name='enrichment_proposal_status'),
        Index('ix_enrichment_proposal_batch_state','batch_id','status','id'),
    )
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('products.id',ondelete='CASCADE'),nullable=False)
    candidate_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('catalog_bulk_candidates.id',name='fk_enrich_candidate',ondelete='RESTRICT'),nullable=False)
    batch_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False)
    title:Mapped[str]=mapped_column(String(300),nullable=False)
    category_code:Mapped[str|None]=mapped_column(String(80))
    category_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('catalog_categories.id',ondelete='SET NULL'))
    attributes:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
    evidence:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
    flags:Mapped[list]=mapped_column(JSON,nullable=False,default=list)
    quality:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
    rule_version:Mapped[str]=mapped_column(String(20),nullable=False)
    status:Mapped[str]=mapped_column(String(16),nullable=False,default='proposed')
    review_note:Mapped[str]=mapped_column(String(500),nullable=False,default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow,onupdate=utcnow)
    applied_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))


class EnrichmentRun(Base):
    __tablename__='catalog_enrichment_runs'
    __table_args__=(
        CheckConstraint("kind IN ('propose','approve_safe','apply')",name='enrichment_run_kind'),
        CheckConstraint("status IN ('queued','processing','completed','failed')",name='enrichment_run_status'),
        Index('ix_enrichment_run_batch_created','batch_id','created_at'),
        Index('uq_enrichment_one_active_batch','batch_id',unique=True,
              postgresql_where=text("status IN ('queued','processing')"),
              sqlite_where=text("status IN ('queued','processing')")),
    )
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    kind:Mapped[str]=mapped_column(String(20),nullable=False)
    status:Mapped[str]=mapped_column(String(16),nullable=False,default='queued')
    batch_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False)
    parameters:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
    affected:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    reviewed:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    message:Mapped[str]=mapped_column(Text,nullable=False,default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True))


class EnrichmentDecisionAudit(Base):
    __tablename__='catalog_enrichment_decisions'
    __table_args__=(Index('ix_enrichment_decision_proposal_date','proposal_id','created_at'),)
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    proposal_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('catalog_enrichment_proposals.id',name='fk_enrich_audit_proposal',ondelete='CASCADE'),nullable=False)
    old_status:Mapped[str]=mapped_column(String(16),nullable=False)
    new_status:Mapped[str]=mapped_column(String(16),nullable=False)
    note:Mapped[str]=mapped_column(String(500),nullable=False,default='')
    origin:Mapped[str]=mapped_column(String(24),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
