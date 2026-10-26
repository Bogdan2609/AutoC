"""Versioned daily supplier files. Never represent API-verified or owned stock."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utc():
    return datetime.now(timezone.utc)


class FeedSource(Base):
    __tablename__ = 'feed_sources'
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    active_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    max_age_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    commercial_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_kind: Mapped[str] = mapped_column(String(16), nullable=False, default='unknown')
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='RUB')
    tax_status: Mapped[str] = mapped_column(String(16), nullable=False, default='unknown')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc, nullable=False)


class FeedBatch(Base):
    __tablename__ = 'feed_batches'
    __table_args__ = (
        UniqueConstraint('supplier_code', 'sha256', name='uq_feed_batch_supplier_checksum'),
        CheckConstraint("status IN ('uploaded','queued','processing','staged','published','failed','rejected')", name='feed_batch_status'),
        Index('ix_feed_batch_supplier_date', 'supplier_code', 'created_at'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('feed_sources.code', ondelete='RESTRICT'), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default='uploaded', nullable=False)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Internal generated relative filename, never supplied by the client.
    stored_name: Mapped[str] = mapped_column(String(80), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Files do not reliably report generation timestamp. Only supplied by operator.
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_matched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors_sample: Mapped[str] = mapped_column(Text, default='', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedOffer(Base):
    __tablename__ = 'feed_offers'
    __table_args__ = (
        UniqueConstraint('batch_id', 'identity_hash', name='uq_feed_offer_version_key'),
        CheckConstraint('purchase_price > 0 AND stock_min >= 0 AND minimum >= 1 AND multiple >= 1', name='feed_positive_terms'),
        Index('ix_feed_offers_product_version', 'product_id', 'batch_id'),
        Index('ix_feed_offers_match', 'batch_id', 'brand_norm', 'article_norm'),
        Index('ix_feed_offers_scope', 'batch_id', 'warehouse_code'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('feed_batches.id', ondelete='CASCADE'), nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_line: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_sku: Mapped[str] = mapped_column(String(150), default='', nullable=False)
    brand: Mapped[str] = mapped_column(String(160), nullable=False)
    brand_norm: Mapped[str] = mapped_column(String(160), nullable=False)
    article: Mapped[str] = mapped_column(String(110), nullable=False)
    article_norm: Mapped[str] = mapped_column(String(110), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(140), nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    stock_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_min: Mapped[int] = mapped_column(Integer, nullable=False)
    is_lower_bound: Mapped[bool] = mapped_column(Boolean, nullable=False)
    minimum: Mapped[int] = mapped_column(Integer, nullable=False)
    multiple: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_days: Mapped[int | None] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id', ondelete='SET NULL'), nullable=True)


class FeedWarehouseMap(Base):
    __tablename__ = 'feed_warehouse_mappings'
    __table_args__ = (UniqueConstraint('supplier_code','warehouse_code','account_id','destination_id',name='uq_feed_warehouse_account_destination'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('feed_sources.code', ondelete='RESTRICT'), nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(140), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_accounts.id', ondelete='RESTRICT'), nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_destinations.id', ondelete='RESTRICT'), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc, nullable=False)


class FeedBrandAlias(Base):
    __tablename__ = 'feed_brand_aliases'
    __table_args__ = (UniqueConstraint('supplier_code','brand_norm',name='uq_feed_brand_supplier_alias'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('feed_sources.code', ondelete='RESTRICT'), nullable=False)
    brand_norm: Mapped[str] = mapped_column(String(160), nullable=False)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('brands.id', ondelete='RESTRICT'), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FeedPriceRule(Base):
    """Explicitly reviewed purchase-to-retail cost model for ONE account/destination/warehouse."""
    __tablename__ = 'feed_price_rules'
    __table_args__ = (
        UniqueConstraint('supplier_code','account_id','destination_id','warehouse_code',name='uq_feed_price_scope'),
        CheckConstraint('markup_percent >= 0 AND markup_percent <= 1000 AND sbp_percent >= 0 AND sbp_percent < 100',name='feed_price_percentage_bounds'),
        CheckConstraint('supplier_delivery >= 0 AND last_mile_delivery >= 0 AND packaging >= 0 AND other_costs >= 0 AND tax_reserve >= 0 AND sbp_fixed >= 0 AND min_contribution >= 0',name='feed_price_nonnegative_costs'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('feed_sources.code',ondelete='RESTRICT'),nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=False)
    destination_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_destinations.id',ondelete='RESTRICT'),nullable=False)
    warehouse_code: Mapped[str] = mapped_column(String(140),nullable=False)
    version: Mapped[int] = mapped_column(Integer,default=1,nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean,default=False,nullable=False)
    tax_status_verified: Mapped[str] = mapped_column(String(16),nullable=False)
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(8,2),nullable=False)
    supplier_delivery: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    last_mile_delivery: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False,server_default="0")
    packaging: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    other_costs: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    tax_reserve: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    sbp_percent: Mapped[Decimal] = mapped_column(Numeric(7,3),nullable=False)
    sbp_fixed: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    min_contribution: Mapped[Decimal] = mapped_column(Numeric(14,2),nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(300),nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc,nullable=False)


class FeedCatalogSync(Base):
    """Exactly one durable catalog sync per validated imported feed batch."""
    __tablename__ = 'feed_catalog_syncs'
    __table_args__ = (CheckConstraint("status IN ('queued','processing','completed','failed','paused')",name='feed_sync_status'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('feed_batches.id',ondelete='RESTRICT'),unique=True,nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False,default='queued')
    stage: Mapped[str] = mapped_column(String(20),nullable=False,default='scan')
    cursor: Mapped[str | None] = mapped_column(String(36))
    scanned: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    created_drafts: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    reviewed: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    updated_proposals: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    linked: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    step: Mapped[int] = mapped_column(Integer,default=0,nullable=False)
    taxonomy_revision: Mapped[int] = mapped_column(Integer,nullable=False)
    message: Mapped[str] = mapped_column(Text,default='',nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc,nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
