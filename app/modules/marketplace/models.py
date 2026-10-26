"""Marketplace identity, sellable offers, individual used listings and fulfillment network.

Do not conflate a wholesaler Supplier with a legal Merchant. The shared Product
catalog has no owner; a SellerOffer has exactly one owning Merchant.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def now():
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = 'merchants'
    __table_args__ = (CheckConstraint("status IN ('draft','active','suspended')", name='status'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_platform_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='draft', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Store(Base):
    __tablename__ = 'stores'
    __table_args__ = (UniqueConstraint('merchant_id','slug', name='uq_store_merchant_slug'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id', ondelete='RESTRICT'), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default='active')


class StoreMembership(Base):
    __tablename__ = 'store_memberships'
    __table_args__ = (UniqueConstraint('store_id','user_id',name='uq_store_user'),
                      CheckConstraint("role IN ('owner','manager','inventory','viewer')",name='role'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('stores.id',ondelete='CASCADE'),index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    role: Mapped[str] = mapped_column(String(20),default='viewer')


class FulfillmentNode(Base):
    __tablename__ = 'fulfillment_nodes'
    __table_args__ = (CheckConstraint("kind IN ('warehouse','pickup','warehouse_pickup')",name='kind'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'), index=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('stores.id',ondelete='SET NULL'),nullable=True)
    code: Mapped[str] = mapped_column(String(60),unique=True)
    name: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(24))
    city: Mapped[str] = mapped_column(String(120),index=True)
    address: Mapped[str] = mapped_column(String(320))
    # Operational recipient for supplier courier deliveries; never buyer PII.
    receiving_contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    receiving_contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    receiving_contact_comment: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    timezone_name: Mapped[str] = mapped_column(String(64),default='Europe/Moscow')
    active: Mapped[bool] = mapped_column(Boolean,default=True)


class DeliveryRoute(Base):
    __tablename__ = 'delivery_routes'
    __table_args__ = (UniqueConstraint('source_node_id','target_node_id',name='uq_route_nodes'),
                      CheckConstraint('transit_days >= 0 AND handling_days >= 0',name='nonnegative_days'),
                      CheckConstraint('fee >= 0',name='nonnegative_fee'),
                      CheckConstraint('source_node_id <> target_node_id', name='distinct_nodes'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    source_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('fulfillment_nodes.id',ondelete='CASCADE'))
    target_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('fulfillment_nodes.id',ondelete='CASCADE'))
    transit_days: Mapped[int] = mapped_column(Integer,default=1)
    handling_days: Mapped[int] = mapped_column(Integer,default=0)
    fee: Mapped[Decimal] = mapped_column(Numeric(14,2),default=Decimal('0.00'))
    active: Mapped[bool] = mapped_column(Boolean,default=True)
    # timetable/cutoff not yet implemented; estimates are preliminary calendar-day estimates.


class SupplierAccount(Base):
    __tablename__ = 'supplier_accounts'
    __table_args__ = (UniqueConstraint('supplier_id','label',name='uq_supplier_account_label'),
                      UniqueConstraint('supplier_id','credential_profile',name='uq_supplier_credential_profile'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('suppliers.id',ondelete='RESTRICT'),index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'),index=True)
    label: Mapped[str] = mapped_column(String(120))
    # Reference to environment variables, NOT the API key or password.
    credential_profile: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    read_enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    # Two-step, explicitly armed real order permission. Default OFF.
    procurement_enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    requests_per_hour: Mapped[int] = mapped_column(Integer,default=30)
    timeout_seconds: Mapped[int] = mapped_column(Integer,default=20)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class SupplierDestination(Base):
    __tablename__ = 'supplier_destinations'
    __table_args__ = (UniqueConstraint('account_id','node_id',name='uq_account_destination_node'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('supplier_accounts.id',ondelete='CASCADE'),index=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('fulfillment_nodes.id',ondelete='RESTRICT'))
    external_address_id: Mapped[str | None] = mapped_column(String(100),nullable=True)
    external_delivery_id: Mapped[str | None] = mapped_column(String(100),nullable=True)
    # Optional organization binding. When absent, a unique company in the live
    # GetCheckoutDetails CompanyList can be selected automatically.
    external_requisite_id: Mapped[str | None] = mapped_column(String(80),nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    verified: Mapped[bool] = mapped_column(Boolean,default=False)


class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('categories.id',ondelete='RESTRICT'),nullable=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(160),unique=True)


class ProductCategory(Base):
    __tablename__ = 'product_categories'
    __table_args__ = (UniqueConstraint('product_id','category_id',name='uq_product_category'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='CASCADE'))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('categories.id',ondelete='CASCADE'))


class ProductIdentifier(Base):
    __tablename__ = 'product_identifiers'
    __table_args__ = (UniqueConstraint('product_id','kind','value_norm',name='uq_product_identifier'),
                      Index('ix_product_identifier_value','kind','value_norm'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[str] = mapped_column(String(130))
    value_norm: Mapped[str] = mapped_column(String(130))


class ProductRelation(Base):
    __tablename__ = 'product_relations'
    __table_args__ = (UniqueConstraint('product_id','related_product_id','relation',name='uq_product_relation'),
                      CheckConstraint('product_id <> related_product_id',name='different_products'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='CASCADE'))
    related_product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('products.id',ondelete='CASCADE'))
    relation: Mapped[str] = mapped_column(String(24),default='related')
    verified: Mapped[bool] = mapped_column(Boolean,default=False)


class UsedPart(Base):
    __tablename__ = 'used_parts'
    __table_args__ = (CheckConstraint("condition IN ('used_good','used_fair','damaged','for_parts')",name='condition'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'),index=True)
    original_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id',ondelete='SET NULL'),index=True,nullable=True)
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str] = mapped_column(Text,default='')
    condition: Mapped[str] = mapped_column(String(24))
    mileage_km: Mapped[int | None] = mapped_column(Integer,nullable=True)
    defects: Mapped[str] = mapped_column(Text,default='')
    origin_note: Mapped[str] = mapped_column(String(400),default='')
    photos: Mapped[list] = mapped_column(JSON,default=list)
    status: Mapped[str] = mapped_column(String(20),default='draft')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class SellerOffer(Base):
    __tablename__ = 'seller_offers'
    __table_args__ = (
        UniqueConstraint('store_id','sku',name='uq_seller_store_sku'),
        CheckConstraint('(product_id IS NOT NULL AND used_part_id IS NULL) OR (product_id IS NULL AND used_part_id IS NOT NULL)',name='one_sellable_identity'),
        CheckConstraint('price >= 0',name='nonnegative_price'),
        CheckConstraint("source_kind IN ('own_stock','supplier')",name='source_kind'),
        Index('ix_seller_offer_product_status','product_id','status'),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'),index=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('stores.id',ondelete='RESTRICT'))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=True)
    used_part_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('used_parts.id',ondelete='RESTRICT'),nullable=True)
    supplier_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=True)
    supplier_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('supplier_items.id',ondelete='RESTRICT'),nullable=True)
    sku: Mapped[str] = mapped_column(String(120))
    source_kind: Mapped[str] = mapped_column(String(24),default='own_stock')
    price: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(24),default='draft')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class InventoryBalance(Base):
    __tablename__ = 'inventory_balances'
    __table_args__ = (UniqueConstraint('seller_offer_id','node_id',name='uq_inventory_offer_node'),
                      CheckConstraint('on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand',name='inventory_bounds'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    seller_offer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('seller_offers.id',ondelete='RESTRICT'),index=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('fulfillment_nodes.id',ondelete='RESTRICT'))
    on_hand: Mapped[int] = mapped_column(Integer,default=0)
    reserved: Mapped[int] = mapped_column(Integer,default=0)


class InventoryMovement(Base):
    __tablename__ = 'inventory_movements'
    __table_args__ = (UniqueConstraint('idempotency_key',name='uq_inventory_movement_key'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    balance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('inventory_balances.id',ondelete='RESTRICT'))
    delta_on_hand: Mapped[int] = mapped_column(Integer,default=0)
    delta_reserved: Mapped[int] = mapped_column(Integer,default=0)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class SellerOrder(Base):
    __tablename__ = 'seller_orders'
    __table_args__ = (UniqueConstraint('customer_order_id','merchant_id',name='uq_order_merchant'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    customer_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('customer_orders.id',ondelete='RESTRICT'),index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('merchants.id',ondelete='RESTRICT'))
    status: Mapped[str] = mapped_column(String(30),default='pending')
    total: Mapped[Decimal] = mapped_column(Numeric(14,2),default=Decimal('0.00'))
