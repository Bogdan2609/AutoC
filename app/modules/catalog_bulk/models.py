"""Persistent identity candidates and their independently auditable source batches.

A candidate is *not* a product, and neither approval nor materialization publishes a product.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint, Uuid, JSON)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class CatalogCandidate(Base):
    __tablename__ = 'catalog_bulk_candidates'
    __table_args__ = (
        UniqueConstraint('brand_norm','article_norm',name='uq_bulk_candidate_identity'),
        CheckConstraint("status IN ('new','review','approved','materialized','blocked')",name='bulk_candidate_status'),
        Index('ix_bulk_candidate_status', 'status','brand_norm','article_norm'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    brand_norm: Mapped[str] = mapped_column(String(160),nullable=False)
    article_norm: Mapped[str] = mapped_column(String(110),nullable=False)
    sample_brand: Mapped[str] = mapped_column(String(160),nullable=False)
    sample_article: Mapped[str] = mapped_column(String(110),nullable=False)
    sample_description: Mapped[str] = mapped_column(String(300),nullable=False,default='')
    status: Mapped[str] = mapped_column(String(16),nullable=False,default='new')
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id',ondelete='SET NULL'))
    review_note: Mapped[str] = mapped_column(Text,nullable=False,default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now,onupdate=now)


class CatalogCandidateBatch(Base):
    __tablename__ = 'catalog_bulk_candidate_batches'
    __table_args__ = (UniqueConstraint('candidate_id','batch_id',name='uq_bulk_candidate_batch'),
                      Index('ix_bulk_candidate_batch_reverse','batch_id','candidate_id'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('catalog_bulk_candidates.id',name='fk_bulk_source_candidate',ondelete='CASCADE'),nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('feed_batches.id',ondelete='RESTRICT'),nullable=False)
    offers_count: Mapped[int] = mapped_column(Integer,nullable=False)
    description_variants: Mapped[int] = mapped_column(Integer,nullable=False)
    sku_variants: Mapped[int] = mapped_column(Integer,nullable=False)
    sample_description: Mapped[str] = mapped_column(String(300),nullable=False,default='')


class CatalogBulkRun(Base):
    __tablename__ = 'catalog_bulk_runs'
    __table_args__ = (CheckConstraint("kind IN ('scan','approve_safe','materialize','relink')",name='bulk_run_kind'),
                      CheckConstraint("status IN ('queued','processing','completed','failed')",name='bulk_run_status'),
                      Index('ix_bulk_run_batch_date','batch_id','created_at'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16),nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False,default='queued')
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('feed_batches.id',ondelete='RESTRICT'))
    parameters: Mapped[dict] = mapped_column(JSON,nullable=False,default=dict)
    affected: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    linked: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    message: Mapped[str] = mapped_column(Text,nullable=False,default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogDecisionAudit(Base):
    __tablename__ = 'catalog_bulk_decision_audit'
    __table_args__ = (Index('ix_bulk_decision_candidate_date','candidate_id','created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('catalog_bulk_candidates.id',name='fk_bulk_audit_candidate',ondelete='RESTRICT'),nullable=False)
    old_status: Mapped[str] = mapped_column(String(16),nullable=False)
    new_status: Mapped[str] = mapped_column(String(16),nullable=False)
    note: Mapped[str] = mapped_column(String(500),nullable=False,default='')
    origin: Mapped[str] = mapped_column(String(32),nullable=False,default='admin_api')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now)
