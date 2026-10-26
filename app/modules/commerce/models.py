import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime,ForeignKey,Integer,text,Numeric,String,JSON,Text,Uuid,UniqueConstraint,CheckConstraint,Index,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
from app.modules.accounts.models import utc
class Cart(Base):
 __tablename__='carts'
 __table_args__=(Index('uq_carts_user_open','user_id',unique=True,postgresql_where=text("status = 'open'"),sqlite_where=text("status = 'open'")),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 user_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id'),index=True)
 status:Mapped[str]=mapped_column(String(24),default='open')
 revision:Mapped[int]=mapped_column(Integer,default=0)
class CartLine(Base):
 __tablename__='cart_lines'
 __table_args__=(UniqueConstraint('cart_id','supplier_item_id',name='uq_cart_line_product_offer'),
  Index('uq_cart_seller_offer_node','cart_id','seller_offer_id','fulfillment_node_id',unique=True,
        postgresql_where=text('seller_offer_id IS NOT NULL AND fulfillment_node_id IS NOT NULL'),
        sqlite_where=text('seller_offer_id IS NOT NULL AND fulfillment_node_id IS NOT NULL')),
  CheckConstraint('quantity > 0',name='cart_line_quantity'))
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 cart_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('carts.id',ondelete='CASCADE'),index=True)
 supplier_item_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_items.id'),nullable=True)
 seller_offer_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('seller_offers.id'),nullable=True)
 fulfillment_node_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('fulfillment_nodes.id'),nullable=True)
 supplier_destination_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_destinations.id',name='fk_cart_line_supplier_dest'),nullable=True)
 validated_snapshot_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_offer_snapshots.id',name='fk_cart_line_snapshot'),nullable=True)
 validated_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 quantity:Mapped[int]=mapped_column(Integer)
 # v0.17 selection is a single supplier warehouse+account+route, not an abstract SKU.
 line_kind:Mapped[str]=mapped_column(String(24),default='legacy',nullable=False)
 product_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=True)
 quoted_unit_retail:Mapped[Decimal|None]=mapped_column(Numeric(14,2),nullable=True)
 quote_metadata:Mapped[dict|None]=mapped_column(JSON,nullable=True)
class Order(Base):
 __tablename__='customer_orders'
 __table_args__=(UniqueConstraint('user_id','idempotency_key',name='uq_order_user_key'),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 user_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id'),index=True)
 idempotency_key:Mapped[str]=mapped_column(String(100))
 total:Mapped[Decimal]=mapped_column(Numeric(14,2))
 status:Mapped[str]=mapped_column(String(40),default='awaiting_sbp')
 contact:Mapped[str]=mapped_column(String(160))
 phone:Mapped[str]=mapped_column(String(30))
 shipping_address:Mapped[str]=mapped_column(String(400))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
 # total is ONLY the known component subtotal for quote requests, never an amount due.
 total_is_final:Mapped[bool]=mapped_column(Boolean,default=True,nullable=False)
 request_fingerprint:Mapped[str|None]=mapped_column(String(64),nullable=True)
 request_details:Mapped[dict|None]=mapped_column(JSON,nullable=True)
 request_expires_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class OrderLine(Base):
 __tablename__='order_lines'
 __table_args__=(CheckConstraint('quantity>0',name='order_quantity_positive'),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 order_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('customer_orders.id'),index=True)
 supplier_item_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_items.id'),nullable=True)
 snapshot_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_offer_snapshots.id'),nullable=True)
 seller_offer_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('seller_offers.id'),nullable=True)
 merchant_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('merchants.id'),nullable=True)
 fulfillment_node_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('fulfillment_nodes.id'),nullable=True)
 title:Mapped[str]=mapped_column(String(300))
 supplier_code:Mapped[str]=mapped_column(String(40))
 quantity:Mapped[int]=mapped_column(Integer)
 unit_retail:Mapped[Decimal]=mapped_column(Numeric(14,2))
 unit_purchase:Mapped[Decimal]=mapped_column(Numeric(14,2))
 status:Mapped[str]=mapped_column(String(40),default='awaiting_sbp')
 product_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('products.id',ondelete='RESTRICT'),nullable=True)
 commercial_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
class Procurement(Base):
 __tablename__='procurements'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 order_line_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('order_lines.id'),unique=True)
 supplier_code:Mapped[str]=mapped_column(String(40))
 supplier_order_id:Mapped[str|None]=mapped_column(String(150))
 status:Mapped[str]=mapped_column(String(40),default='pending')
 error:Mapped[str|None]=mapped_column(String(300))
class Replacement(Base):
 __tablename__='replacements'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 order_line_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('order_lines.id'),index=True)
 alternative_item_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_items.id'))
 suggested_price:Mapped[Decimal|None]=mapped_column(Numeric(14,2))
 status:Mapped[str]=mapped_column(String(40),default='awaiting_customer')
 reason:Mapped[str]=mapped_column(String(300),default='Supplier cannot fulfill')
class Job(Base):
 __tablename__='jobs'
 __table_args__=(Index('ix_jobs_status_due','status','due_at'),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 dedup_key:Mapped[str]=mapped_column(String(180),unique=True)
 kind:Mapped[str]=mapped_column(String(40))
 payload:Mapped[dict]=mapped_column(JSON,default=dict)
 status:Mapped[str]=mapped_column(String(30),default='pending')
 due_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
 attempts:Mapped[int]=mapped_column(Integer,default=0)
 last_error:Mapped[str|None]=mapped_column(Text)

class OrderWorkflowEvent(Base):
    """Append-only state transition evidence, never an authorization to collect money."""
    __tablename__ = 'order_workflow_events'
    __table_args__ = (Index('ix_order_workflow_event_order_created', 'order_id', 'created_at'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('customer_orders.id', ondelete='RESTRICT'), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc, nullable=False)
