import uuid

from pydantic import BaseModel, ConfigDict, Field


class BrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,178}$", max_length=180)


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str


class ProductCreate(BaseModel):
    brand_id: uuid.UUID
    article: str = Field(min_length=1, max_length=110)
    name: str = Field(min_length=2, max_length=300)
    description: str | None = Field(default=None, max_length=10000)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    brand_id: uuid.UUID
    article: str
    article_norm: str
    name: str
    description: str | None
    slug: str
    status: str


class SearchOut(BaseModel):
    items: list[ProductOut]
    count: int
    limit: int
    offset: int
