"""F5 shadow-only source documents and tax register preview rows.

No foreign keys to live orders, bank payments, customer receipts, or F3 journal.
Every reference is synthetic and starts with SIM:. These ARE NOT statutory books.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class ShadowTaxBook(Base):
    __tablename__='finance_f5_sim_books'
    __table_args__=(
        UniqueConstraint('book_ref',name='uq_f5_book_ref'),
        CheckConstraint("book_ref LIKE 'SIM:F5:BOOK:%'",name='sim_only'),
        CheckConstraint("actor_role IN ('PLATFORM','SELLER')",name='actor_role'),
        CheckConstraint("tax_profile_status = 'UNRESOLVED'",name='unresolved'),
        CheckConstraint('period_start <= period_end',name='period'),
        CheckConstraint("currency='RUB'",name='currency'),
    )
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    book_ref:Mapped[str]=mapped_column(String(160),nullable=False)
    actor_role:Mapped[str]=mapped_column(String(10),nullable=False)
    tax_profile_status:Mapped[str]=mapped_column(String(16),nullable=False,default='UNRESOLVED')
    period_start:Mapped[date]=mapped_column(Date,nullable=False)
    period_end:Mapped[date]=mapped_column(Date,nullable=False)
    currency:Mapped[str]=mapped_column(String(3),nullable=False,default='RUB')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)


class ShadowTaxDocument(Base):
    __tablename__='finance_f5_sim_documents'
    __table_args__=(
        UniqueConstraint('document_ref',name='uq_f5_document_ref'),
        CheckConstraint("document_ref LIKE 'SIM:F5:DOC:%'",name='sim_only'),
        CheckConstraint("kind IN ('SALE','PURCHASE','AGENT_REPORT','ADVANCE','REFUND','EXPENSE')",name='kind'),
    )
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    document_ref:Mapped[str]=mapped_column(String(160),nullable=False)
    document_date:Mapped[date]=mapped_column(Date,nullable=False)
    kind:Mapped[str]=mapped_column(String(14),nullable=False)
    memo:Mapped[str]=mapped_column(String(300),nullable=False,default='SIMULATION ONLY')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)


class ShadowTaxRow(Base):
    __tablename__='finance_f5_sim_rows'
    __table_args__=(
        UniqueConstraint('request_key',name='uq_f5_row_key'),
        UniqueConstraint('book_id','document_id','event_kind',name='uq_f5_book_doc_event'),
        CheckConstraint("request_key LIKE 'SIM:F5:ROW:%'",name='sim_only'),
        CheckConstraint("event_kind IN ('OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE')",name='event_kind'),
        CheckConstraint("vat_scheme IN ('UNRESOLVED','STANDARD','USN_5','USN_7','EXEMPT')",name='vat_scheme'),
        CheckConstraint("tax_class IN ('STANDARD_GOODS','REDUCED_10','ZERO','EXEMPT')",name='tax_class'),
        CheckConstraint('gross_amount > 0',name='positive'),
    )
    id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    book_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('finance_f5_sim_books.id',name='fk_f5_row_book',ondelete='RESTRICT'),nullable=False)
    document_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('finance_f5_sim_documents.id',name='fk_f5_row_document',ondelete='RESTRICT'),nullable=False)
    request_key:Mapped[str]=mapped_column(String(160),nullable=False)
    event_kind:Mapped[str]=mapped_column(String(24),nullable=False)
    operation_date:Mapped[date]=mapped_column(Date,nullable=False)
    gross_amount:Mapped[Decimal]=mapped_column(Numeric(20,2),nullable=False)
    vat_scheme:Mapped[str]=mapped_column(String(16),nullable=False,default='UNRESOLVED')
    tax_class:Mapped[str]=mapped_column(String(20),nullable=False,default='STANDARD_GOODS')
    vat_amount:Mapped[Decimal|None]=mapped_column(Numeric(20,2),nullable=True)
    net_amount:Mapped[Decimal|None]=mapped_column(Numeric(20,2),nullable=True)
    correction_of_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('finance_f5_sim_rows.id',name='fk_f5_row_corrects',ondelete='RESTRICT'),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)
