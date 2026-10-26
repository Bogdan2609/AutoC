"""Finance master data. No assumption that a Merchant is a legal/tax entity.

F1 deliberately does NOT create ledger entries or infer a seller's taxable income.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
                        Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class LegalEntity(Base):
    __tablename__ = 'finance_legal_entities'
    __table_args__ = (
        CheckConstraint("entity_type IN ('company','sole_proprietor','individual','self_employed')", name='entity_type'),
        CheckConstraint("verification_status IN ('draft','verified','rejected')", name='verification'),
        CheckConstraint("inn IS NULL OR length(inn) IN (10,12)", name='inn_length'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('merchants.id', ondelete='RESTRICT'), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    inn: Mapped[str | None] = mapped_column(String(12), nullable=True, unique=True)
    kpp: Mapped[str | None] = mapped_column(String(9), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(16), default='draft', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class EntityRole(Base):
    __tablename__ = 'finance_entity_roles'
    __table_args__ = (UniqueConstraint('entity_id','role',name='uq_fin_role'),
                       CheckConstraint("role IN ('platform_owner','seller','procurement_buyer','supplier','payment_recipient')", name='role'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)


class TaxProfile(Base):
    __tablename__ = 'finance_tax_profiles'
    __table_args__ = (
        UniqueConstraint('entity_id', 'valid_from', name='uq_fin_profile_entity_start'),
        CheckConstraint('valid_until IS NULL OR valid_until >= valid_from', name='valid_dates'),
        CheckConstraint("status IN ('draft','active','retired')", name='status'),
        CheckConstraint("regime IN ('OSNO','USN_INCOME','USN_PROFIT','AUSN_INCOME','AUSN_PROFIT')", name='regime'),
        CheckConstraint("vat_scheme IN ('STANDARD','USN_5','USN_7','EXEMPT','UNRESOLVED')", name='vat_scheme'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=False, index=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    vat_scheme: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default='draft')
    prior_year_income: Mapped[Decimal | None] = mapped_column(Numeric(20,2), nullable=True)
    current_year_income: Mapped[Decimal | None] = mapped_column(Numeric(20,2), nullable=True)
    external_income_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class TaxRule(Base):
    __tablename__ = 'finance_tax_rules'
    __table_args__ = (UniqueConstraint('rule_code','valid_from',name='uq_fin_tax_rule_version'),
                       CheckConstraint('valid_until IS NULL OR valid_until >= valid_from', name='valid_dates'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TaxClass(Base):
    __tablename__ = 'finance_tax_classes'
    __table_args__ = (UniqueConstraint('code', name='uq_fin_tax_class_code'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    special_evidence_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SaleContract(Base):
    __tablename__ = 'finance_sales_contracts'
    __table_args__ = (CheckConstraint("sale_model IN ('OWN','AGENCY')", name='sale_model'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_legal_entities.id', name='fk_fin_contract_seller', ondelete='RESTRICT'), nullable=False)
    agent_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_legal_entities.id', name='fk_fin_contract_agent', ondelete='RESTRICT'), nullable=True)
    sale_model: Mapped[str] = mapped_column(String(10), nullable=False)
    document_reference: Mapped[str] = mapped_column(String(250), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class ExternalIncome(Base):
    __tablename__ = 'finance_external_income'
    __table_args__ = (UniqueConstraint('entity_id', 'year', 'source_reference', name='uq_fin_external_income_ref'),
                       CheckConstraint('amount >= 0', name='amount_positive'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20,2), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TaxEvaluation(Base):
    __tablename__ = 'finance_tax_evaluations'
    __table_args__ = (UniqueConstraint('idempotency_key', name='uq_fin_tax_eval_key'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_tax_profiles.id', ondelete='RESTRICT'), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


class TaxAudit(Base):
    __tablename__ = 'finance_tax_audit_log'
    __table_args__ = (Index('ix_fin_tax_audit_created', 'created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
