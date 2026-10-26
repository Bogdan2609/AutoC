import uuid
from datetime import datetime,timezone
from sqlalchemy import Boolean,DateTime,ForeignKey,String,Uuid
from sqlalchemy.orm import Mapped,mapped_column
from app.core.db import Base

def utc():return datetime.now(timezone.utc)
class User(Base):
 __tablename__='users'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 email:Mapped[str]=mapped_column(String(254),unique=True,nullable=False)
 password_hash:Mapped[str]=mapped_column(String(256),nullable=False)
 display_name:Mapped[str]=mapped_column(String(160),default='')
 is_active:Mapped[bool]=mapped_column(Boolean,default=True)
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utc)
class Session(Base):
 __tablename__='login_sessions'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 user_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
 digest:Mapped[str]=mapped_column(String(64),unique=True)
 expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
 revoked:Mapped[bool]=mapped_column(Boolean,default=False)
class Address(Base):
 __tablename__='customer_addresses'
 id:Mapped[uuid.UUID]=mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
 user_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
 contact:Mapped[str]=mapped_column(String(160))
 phone:Mapped[str]=mapped_column(String(30))
 address:Mapped[str]=mapped_column(String(400))
