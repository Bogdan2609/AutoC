import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime,ForeignKey,Numeric,String,Uuid,UniqueConstraint,Index,text
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
from app.modules.accounts.models import utc
class Payment(Base):
 __tablename__='sbp_payments'
 __table_args__=(Index('uq_successful_sbp_payment_per_order','order_id',unique=True,
     postgresql_where=text("status = 'succeeded'"),sqlite_where=text("status = 'succeeded'")),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 order_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('customer_orders.id'),index=True)
 provider_id:Mapped[str|None]=mapped_column(String(150),unique=True)
 status:Mapped[str]=mapped_column(String(32),default='creating')
 amount:Mapped[Decimal]=mapped_column(Numeric(14,2))
 payment_url:Mapped[str|None]=mapped_column(String(2048))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
class Refund(Base):
 __tablename__='sbp_refunds'
 __table_args__=(UniqueConstraint('replacement_id',name='uq_refund_replacement'),)
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 replacement_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('replacements.id'))
 payment_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('sbp_payments.id'))
 provider_id:Mapped[str|None]=mapped_column(String(150),unique=True)
 amount:Mapped[Decimal]=mapped_column(Numeric(14,2))
 status:Mapped[str]=mapped_column(String(32),default='pending')
