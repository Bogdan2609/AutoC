"""F2 shadow pricing rules. Not wired to order placement, existing PricePolicy or actual merchant settlement."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, Uuid, UniqueConstraint, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class PricingRule(Base):
    __tablename__ = 'finance_pricing_rules'
    __table_args__ = (
        CheckConstraint("scope_kind IN ('GLOBAL','MERCHANT','SUPPLIER','CATEGORY','BRAND','PRODUCT')", name='scope'),
        CheckConstraint("state IN ('draft','simulation','retired')", name='state'),
        CheckConstraint("markup_percent >= 0 AND markup_percent <= 1000", name='markup'),
        CheckConstraint("fixed_markup >= 0 AND floor_price >= 0 AND min_contribution >= 0", name='costs'),
        CheckConstraint("valid_until IS NULL OR valid_until >= valid_from", name='dates'),
        CheckConstraint("rounding_mode IN ('EXACT_RUB','SMART_UP')",name='rounding_mode'),
        CheckConstraint('rounding_max_uplift_pct IS NULL OR (rounding_max_uplift_pct >= 0 AND rounding_max_uplift_pct <= 10)',name='rounding_cap'),
        CheckConstraint("(scope_kind='GLOBAL' AND scope_id IS NULL) OR (scope_kind<>'GLOBAL' AND scope_id IS NOT NULL)", name='scope_id'),
        UniqueConstraint('name','valid_from',name='uq_fin_pricing_name_start'),
        Index('ix_fin_pricing_scope','scope_kind','scope_id','state'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160),nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16),nullable=False,default='GLOBAL')
    scope_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True),nullable=True)
    state: Mapped[str] = mapped_column(String(16),nullable=False,default='draft')
    priority: Mapped[int] = mapped_column(Integer,nullable=False,default=100)
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(8,2),nullable=False)
    fixed_markup: Mapped[Decimal] = mapped_column(Numeric(20,2),nullable=False,default=Decimal('0'))
    floor_price: Mapped[Decimal] = mapped_column(Numeric(20,2),nullable=False,default=Decimal('0'))
    min_contribution: Mapped[Decimal] = mapped_column(Numeric(20,2),nullable=False,default=Decimal('0'))
    rounding_mode: Mapped[str] = mapped_column(String(12),nullable=False,default='EXACT_RUB')
    rounding_max_uplift_pct: Mapped[Decimal | None] = mapped_column(Numeric(5,2),nullable=True)
    rounding_tiers: Mapped[list | None] = mapped_column(JSON,nullable=True)
    valid_from: Mapped[date] = mapped_column(Date,nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date,nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now)


class FeeSchedule(Base):
    __tablename__ = 'finance_fee_schedules'
    __table_args__ = (
        CheckConstraint("fee_kind IN ('SBP','MARKETPLACE','LOGISTICS','OTHER')",name='kind'),
        CheckConstraint("state IN ('draft','simulation','retired')",name='state'),
        CheckConstraint('percent >= 0 AND percent <= 100 AND fixed_amount >= 0',name='bounds'),
        CheckConstraint('cap_amount IS NULL OR cap_amount >= 0',name='cap'),
        CheckConstraint('valid_until IS NULL OR valid_until >= valid_from',name='dates'),
        UniqueConstraint('name','valid_from',name='uq_fin_fee_name_start'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160),nullable=False)
    fee_kind: Mapped[str] = mapped_column(String(20),nullable=False)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'),nullable=True)
    state: Mapped[str] = mapped_column(String(16),nullable=False,default='draft')
    percent: Mapped[Decimal] = mapped_column(Numeric(7,3),nullable=False,default=Decimal('0'))
    fixed_amount: Mapped[Decimal] = mapped_column(Numeric(20,2),nullable=False,default=Decimal('0'))
    cap_amount: Mapped[Decimal | None] = mapped_column(Numeric(20,2),nullable=True)
    valid_from: Mapped[date] = mapped_column(Date,nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date,nullable=True)
    evidence_reference: Mapped[str] = mapped_column(String(400),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=now)
