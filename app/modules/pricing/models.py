from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.catalog.models import utcnow


class PricePolicy(Base):
    __tablename__ = "price_policies"
    __table_args__ = (CheckConstraint("markup_percent >= 0 AND markup_percent <= 1000", name="markup_bounds"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

Index("uq_active_price_policy", PricePolicy.active, unique=True, postgresql_where=text("active = true"), sqlite_where=text("active = 1"))
