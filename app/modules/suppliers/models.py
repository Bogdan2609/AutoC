import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.catalog.models import utcnow


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))


class SupplierItem(Base):
    __tablename__ = "supplier_items"
    __table_args__ = (UniqueConstraint("supplier_id", "external_key", name="uq_supplier_external_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    external_key: Mapped[str] = mapped_column(String(150))
    raw_brand: Mapped[str] = mapped_column(String(160))
    raw_article: Mapped[str] = mapped_column(String(110))
    match_status: Mapped[str] = mapped_column(String(20), default="pending")
    last_search_token: Mapped[str | None] = mapped_column(String(36), nullable=True)


class OfferSnapshot(Base):
    __tablename__ = "supplier_offer_snapshots"
    __table_args__ = (
        CheckConstraint("purchase_price >= 0", name="non_negative_price"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="non_negative_stock"),
        Index("ix_supplier_offer_snapshots_item_observed", "supplier_item_id", "observed_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier_items.id", ondelete="RESTRICT"))
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    tax_status: Mapped[str] = mapped_column(String(20), default="unknown")
    quantity: Mapped[int | None] = mapped_column(Integer)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supplier_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier_accounts.id", name="fk_offer_snapshots_supplier_account", ondelete="RESTRICT"), nullable=True, index=True)
    supplier_destination_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("supplier_destinations.id", name="fk_offer_snapshots_destination", ondelete="RESTRICT"),
        nullable=True, index=True)
    # No automatic overwriting of historical snapshots.
