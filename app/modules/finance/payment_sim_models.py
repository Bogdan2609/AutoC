"""F4 shadow-only payments, refund intentions, receipt plans and reconciliation.

These tables intentionally have NO foreign keys to live orders, payments or bank accounts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (CheckConstraint, DateTime, ForeignKey, Index, Numeric, String,
                        Uuid, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class ShadowPayment(Base):
    __tablename__ = 'finance_f4_sim_payments'
    __table_args__ = (
        UniqueConstraint('sim_order_ref', name='uq_f4_sim_order'),
        UniqueConstraint('idempotency_key', name='uq_f4_payment_key'),
        CheckConstraint("sim_order_ref LIKE 'SIM:ORDER:%'", name='sim_order_only'),
        CheckConstraint("currency = 'RUB'", name='rub_only'),
        CheckConstraint("provider = 'MOCK_SBP'", name='mock_only'),
        CheckConstraint("sale_model IN ('OWN','AGENCY')", name='sale_model'),
        CheckConstraint("status IN ('CREATED','PENDING','SUCCEEDED','CANCELED')", name='status'),
        CheckConstraint('amount > 0', name='amount_positive'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sim_order_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sale_model: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='RUB')
    provider: Mapped[str] = mapped_column(String(12), nullable=False, default='MOCK_SBP')
    status: Mapped[str] = mapped_column(String(12), nullable=False, default='CREATED')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShadowEvent(Base):
    __tablename__ = 'finance_f4_sim_events'
    __table_args__ = (UniqueConstraint('payment_id', 'event_key', name='uq_f4_event_key'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_f4_sim_payments.id', name='fk_f4_sim_payment', ondelete='RESTRICT'), nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    new_status: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShadowRefund(Base):
    __tablename__ = 'finance_f4_sim_refunds'
    __table_args__ = (
        UniqueConstraint('request_key', name='uq_f4_refund_key'),
        CheckConstraint('amount > 0', name='amount_positive'),
        CheckConstraint("status IN ('REQUESTED','SUCCEEDED','REJECTED')", name='status'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_f4_sim_payments.id', name='fk_f4_refund_payment', ondelete='RESTRICT'), nullable=False)
    request_key: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default='REQUESTED')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShadowReceiptPlan(Base):
    __tablename__ = 'finance_f4_sim_receipt_plans'
    __table_args__ = (
        UniqueConstraint('request_key', name='uq_f4_receipt_key'),
        CheckConstraint("kind IN ('PREPAYMENT','OFFSET','REFUND')", name='kind'),
        CheckConstraint("state = 'PREVIEW_ONLY'", name='preview_only'),
        CheckConstraint('amount > 0', name='amount_positive'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_f4_sim_payments.id', name='fk_f4_receipt_payment', ondelete='RESTRICT'), nullable=False)
    request_key: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='PREVIEW_ONLY')
    vat_note: Mapped[str] = mapped_column(String(120), nullable=False, default='UNCONFIRMED')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShadowBankLine(Base):
    __tablename__ = 'finance_f4_sim_bank_lines'
    __table_args__ = (
        UniqueConstraint('external_ref', name='uq_f4_bank_external_ref'),
        CheckConstraint("external_ref LIKE 'SIM:BANK:%'", name='sim_bank_only'),
        CheckConstraint("direction IN ('IN','OUT')", name='direction'),
        CheckConstraint('amount > 0', name='positive'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20,2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ShadowBankMatch(Base):
    __tablename__ = 'finance_f4_sim_bank_matches'
    __table_args__ = (
        UniqueConstraint('bank_line_id', name='uq_f4_match_one_bank_line'),
        UniqueConstraint('payment_id', name='uq_f4_match_one_payment'),
        UniqueConstraint('refund_id', name='uq_f4_match_one_refund'),
        CheckConstraint('(payment_id IS NOT NULL AND refund_id IS NULL) OR (payment_id IS NULL AND refund_id IS NOT NULL)', name='exactly_one_source'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_f4_sim_bank_lines.id', name='fk_f4_match_line', ondelete='RESTRICT'), nullable=False)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_f4_sim_payments.id', name='fk_f4_match_payment', ondelete='RESTRICT'), nullable=True)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_f4_sim_refunds.id', name='fk_f4_match_refund', ondelete='RESTRICT'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
