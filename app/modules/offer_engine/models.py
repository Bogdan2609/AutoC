import uuid
from datetime import datetime
from sqlalchemy import Boolean,DateTime,ForeignKey,Integer,JSON,String,Uuid,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base
from app.modules.accounts.models import utc
class OfferDetail(Base):
 __tablename__='offer_details'
 snapshot_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('supplier_offer_snapshots.id',ondelete='CASCADE'),primary_key=True)
 external_token:Mapped[str]=mapped_column(String(512))
 warehouse:Mapped[str]=mapped_column(String(120),default='')
 pack_multiple:Mapped[int]=mapped_column(Integer,default=1)
 min_qty:Mapped[int]=mapped_column(Integer,default=1)
 verified_live:Mapped[bool]=mapped_column(Boolean,default=False)
 metadata_public:Mapped[dict]=mapped_column(JSON,default=dict)
class CacheState(Base):
 __tablename__='offer_cache_states'
 __table_args__=(UniqueConstraint('supplier_code','lookup_key',name='uq_offer_cache_key'),Index('ix_offer_cache_due','expires_at','last_demand_at'))
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 product_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('products.id',ondelete='SET NULL'),nullable=True,index=True)
 last_requested_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 last_demand_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 demand_count:Mapped[int]=mapped_column(Integer,default=0)
 last_success_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 last_finished_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
 last_duration_ms:Mapped[int|None]=mapped_column(Integer,nullable=True)
 failure_count:Mapped[int]=mapped_column(Integer,default=0)
 supplier_code:Mapped[str]=mapped_column(String(40))
 lookup_key:Mapped[str]=mapped_column(String(320))
 status:Mapped[str]=mapped_column(String(30),default='pending')
 expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
 result_count:Mapped[int]=mapped_column(Integer,default=0)
 last_completed_job_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
 error_type:Mapped[str|None]=mapped_column(String(80))
class SupplierLog(Base):
 __tablename__='supplier_api_logs'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 code:Mapped[str]=mapped_column(String(40),index=True)
 supplier_account_id:Mapped[uuid.UUID|None]=mapped_column(ForeignKey('supplier_accounts.id',ondelete='RESTRICT'),nullable=True,index=True)
 action:Mapped[str]=mapped_column(String(40))
 outcome:Mapped[str]=mapped_column(String(30))
 duration_ms:Mapped[int]=mapped_column(Integer,default=0)
 error_type:Mapped[str|None]=mapped_column(String(80))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
 lookup_key:Mapped[str|None]=mapped_column(String(320),nullable=True)
 result_count:Mapped[int|None]=mapped_column(Integer,nullable=True)

class SupplierRuntime(Base):
    """Editable operational rules, never stores API credentials."""
    __tablename__ = 'supplier_runtime'
    code:Mapped[str]=mapped_column(String(40),primary_key=True)
    enabled:Mapped[bool]=mapped_column(Boolean,default=True)
    positive_ttl_seconds:Mapped[int]=mapped_column(Integer,default=300)
    empty_ttl_seconds:Mapped[int]=mapped_column(Integer,default=120)
    timeout_seconds:Mapped[int]=mapped_column(Integer,default=25)
    min_interval_seconds:Mapped[int]=mapped_column(Integer,default=2)
    next_request_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
    last_changed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
    consecutive_failures:Mapped[int]=mapped_column(Integer,default=0)
    circuit_open_until:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
