"""Catalog relationships and pack/sale identities, v0.18.

A family is for navigation only; a composition is product metadata; a factory
package is its own Product identity. Neither merges supplier stock nor prices.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, JSON, Numeric, String, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class ProductFamily(Base):
    __tablename__ = 'product_families'
    __table_args__ = (CheckConstraint("kind IN ('variant','kit_line','related_line')",name='family_kind'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(220),nullable=False)
    slug: Mapped[str] = mapped_column(String(220),unique=True,nullable=False)
    kind: Mapped[str] = mapped_column(String(24),nullable=False,default='variant')
    axes: Mapped[list] = mapped_column(JSON,nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,nullable=False)


class FamilyMember(Base):
    __tablename__ = 'product_family_members'
    __table_args__ = (UniqueConstraint('family_id','variant_key',name='uq_family_variant_key'),
                      UniqueConstraint('product_id',name='uq_product_one_primary_family'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('product_families.id',ondelete='CASCADE'),nullable=False,index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=False)
    axis_values: Mapped[dict] = mapped_column(JSON,nullable=False)
    variant_key: Mapped[str] = mapped_column(String(500),nullable=False)


class ProductComposition(Base):
    __tablename__ = 'product_compositions'
    __table_args__ = (CheckConstraint("kind IN ('factory_kit','assembled_plan')",name='composition_kind'),)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),primary_key=True)
    kind: Mapped[str] = mapped_column(String(24),nullable=False)
    # assembled_plan is informational in v0.18: it must never be used to derive
    # inventory or accept orders until atomic component reservation is built.
    verified: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,onupdate=now)


class ComponentLine(Base):
    __tablename__ = 'product_component_lines'
    __table_args__ = (UniqueConstraint('parent_product_id','position',name='uq_component_position'),
                      CheckConstraint('quantity > 0',name='positive_component_quantity'),
                      CheckConstraint('parent_product_id <> component_product_id',name='distinct_component'),
                      CheckConstraint("unit IN ('pcs','kg','l','m')",name='component_unit'),
                      CheckConstraint("sale_status IN ('unknown','not_listed','independent')",name='component_sale_status'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    parent_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('product_compositions.product_id',name='fk_component_parent',ondelete='CASCADE'),nullable=False,index=True)
    component_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=True)
    position: Mapped[int] = mapped_column(Integer,nullable=False)
    name: Mapped[str] = mapped_column(String(220),nullable=False)
    external_part_number: Mapped[str | None] = mapped_column(String(140),nullable=True)
    quantity: Mapped[object] = mapped_column(Numeric(12,3),nullable=False)
    unit: Mapped[str] = mapped_column(String(12),nullable=False,default='pcs')
    sale_status: Mapped[str] = mapped_column(String(20),nullable=False,default='unknown')


class FactoryPackage(Base):
    __tablename__ = 'factory_packages'
    __table_args__ = (UniqueConstraint('outer_product_id',name='uq_factory_package_outer'),
                      CheckConstraint('outer_product_id <> inner_product_id',name='different_package_products'),
                      CheckConstraint('units_per_package > 1',name='package_more_than_one'),
                      CheckConstraint("kind IN ('retail_pack','box','case','pallet')",name='package_kind'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    outer_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=False,index=True)
    inner_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=False,index=True)
    units_per_package: Mapped[int] = mapped_column(Integer,nullable=False)
    kind: Mapped[str] = mapped_column(String(24),nullable=False,default='retail_pack')
    verified: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)


class OfferSaleTerms(Base):
    __tablename__ = 'offer_sale_terms'
    __table_args__ = (
        # Seller terms are keyed by seller offer. A warehouse-specific supplier
        # rule is keyed by supplier item, account, destination and warehouse.
        UniqueConstraint('scope_key',name='uq_sale_scope_key'),
        CheckConstraint('minimum_units >= 1 AND step_units >= 1 AND units_per_package >= 1',name='positive_sale_units'),
        CheckConstraint("status IN ('draft','active')",name='sale_terms_status'),
        CheckConstraint("order_unit IN ('piece','pack')",name='sale_terms_unit'),
        CheckConstraint('((seller_offer_id IS NOT NULL AND supplier_item_id IS NULL AND supplier_account_id IS NULL AND supplier_destination_id IS NULL AND warehouse_id IS NULL) OR (seller_offer_id IS NULL AND supplier_item_id IS NOT NULL AND supplier_account_id IS NOT NULL AND supplier_destination_id IS NOT NULL AND warehouse_id IS NOT NULL))',name='sale_terms_valid_scope'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(String(260),nullable=False)
    seller_offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('seller_offers.id',ondelete='RESTRICT'),nullable=True)
    supplier_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('supplier_items.id',ondelete='RESTRICT'),nullable=True)
    supplier_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=True)
    supplier_destination_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('supplier_destinations.id',name='fk_sale_supplier_destination',ondelete='RESTRICT'),nullable=True)
    warehouse_id: Mapped[str | None] = mapped_column(String(140),nullable=True)
    minimum_units: Mapped[int] = mapped_column(Integer,nullable=False,default=1)
    step_units: Mapped[int] = mapped_column(Integer,nullable=False,default=1)
    units_per_package: Mapped[int] = mapped_column(Integer,nullable=False,default=1)
    order_unit: Mapped[str] = mapped_column(String(12),nullable=False,default='piece')
    status: Mapped[str] = mapped_column(String(12),nullable=False,default='draft')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,onupdate=now)
