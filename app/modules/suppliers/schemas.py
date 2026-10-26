import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class SupplierCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    name: str = Field(min_length=2, max_length=160)


class SupplierItemCreate(BaseModel):
    supplier_id: uuid.UUID
    product_id: uuid.UUID | None = None
    external_key: str = Field(min_length=1, max_length=150)
    raw_brand: str = Field(min_length=1, max_length=160)
    raw_article: str = Field(min_length=1, max_length=110)


class SnapshotCreate(BaseModel):
    supplier_item_id: uuid.UUID
    purchase_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: Literal["RUB"] = "RUB"
    tax_status: Literal["included", "excluded", "unknown"] = "unknown"
    quantity: int | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0)
    observed_at: datetime | None = None


class PublicOfferOut(BaseModel):
    snapshot_id: uuid.UUID
    supplier_item_id: uuid.UUID
    supplier_code: str
    currency: str
    retail_price: Decimal | None
    quantity: int | None
    lead_time_days: int | None
    observed_at: datetime
    fresh: bool
