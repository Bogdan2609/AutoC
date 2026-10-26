"""F3 shadow-only ledger. Never attached to bank, checkout, procurement or payouts."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric,
                        String, Uuid, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class LedgerBook(Base):
    __tablename__ = 'finance_ledger_books'
    __table_args__ = (
        UniqueConstraint('code', name='uq_f3_book_code'),
        CheckConstraint("state = 'shadow'", name='shadow_only'),
        CheckConstraint("currency = 'RUB'", name='rub_only'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_legal_entities.id', ondelete='RESTRICT'), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default='RUB')
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='shadow')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class LedgerAccount(Base):
    __tablename__ = 'finance_ledger_accounts'
    __table_args__ = (
        UniqueConstraint('book_id', 'code', name='uq_f3_account_code'),
        CheckConstraint("kind IN ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name='kind'),
        CheckConstraint("settlement_role IN ('NONE','SELLER_PAYABLE','SUPPLIER_PAYABLE','RECEIVABLE')", name='settlement_role'),
        CheckConstraint("(settlement_role='NONE' OR (settlement_role IN ('SELLER_PAYABLE','SUPPLIER_PAYABLE') AND kind='LIABILITY') OR (settlement_role='RECEIVABLE' AND kind='ASSET'))", name='settlement_kind'),
        Index('ix_f3_account_book_kind', 'book_id', 'kind'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_ledger_books.id', ondelete='RESTRICT'), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    settlement_role: Mapped[str] = mapped_column(String(20), nullable=False, default='NONE')
    counterparty_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_legal_entities.id', name='fk_f3_account_counterparty', ondelete='RESTRICT'), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class LedgerEntry(Base):
    __tablename__ = 'finance_ledger_entries'
    __table_args__ = (
        UniqueConstraint('book_id', 'idempotency_key', name='uq_f3_entry_idempotency'),
        UniqueConstraint('book_id', 'source_ref', 'event_kind', name='uq_f3_entry_source'),
        UniqueConstraint('reverses_entry_id', name='uq_f3_entry_single_reverse'),
        CheckConstraint("source_ref LIKE 'SIM:%'", name='sim_ref_only'),
        CheckConstraint('length(request_sha256) = 64', name='hash_length'),
        Index('ix_f3_entry_book_date', 'book_id', 'occurred_at'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_ledger_books.id', ondelete='RESTRICT'), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    memo: Mapped[str] = mapped_column(String(300), nullable=False)
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('finance_ledger_entries.id', name='fk_f3_entry_reversal', ondelete='RESTRICT'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class LedgerLine(Base):
    __tablename__ = 'finance_ledger_lines'
    __table_args__ = (
        CheckConstraint("side IN ('DEBIT','CREDIT')", name='side'),
        CheckConstraint('amount > 0', name='positive'),
        Index('ix_f3_line_account', 'account_id'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_ledger_entries.id', ondelete='RESTRICT'), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('finance_ledger_accounts.id', ondelete='RESTRICT'), nullable=False)
    side: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
