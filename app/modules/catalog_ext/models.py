"""v0.21: brand authority, category classification, typed facts, governed media.

Product, Brand and supplier quotation identities remain in their existing tables.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, text,
                        JSON, Numeric, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


def utcnow():
    return datetime.now(timezone.utc)


class BrandProfile(Base):
    __tablename__ = 'brand_profiles'
    brand_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey('brands.id',ondelete='CASCADE'),primary_key=True)
    legal_name: Mapped[str | None] = mapped_column(String(240))
    website: Mapped[str | None] = mapped_column(String(300))
    country_code: Mapped[str | None] = mapped_column(String(2))
    description: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=utcnow,onupdate=utcnow)


class BrandAlias(Base):
    __tablename__ = 'brand_aliases'
    __table_args__ = (UniqueConstraint('scope_key', name='uq_brand_alias_scope_key'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('brands.id',ondelete='CASCADE'),nullable=False,index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True),ForeignKey('suppliers.id',ondelete='RESTRICT'))
    alias: Mapped[str] = mapped_column(String(160),nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(160),nullable=False)
    scope_key: Mapped[str] = mapped_column(String(300),nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)


class Category(Base):
    __tablename__ = 'catalog_categories'
    __table_args__ = (CheckConstraint('parent_id IS NULL OR parent_id <> id',name='category_not_own_parent'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_categories.id',ondelete='RESTRICT'),index=True)
    name: Mapped[str] = mapped_column(String(180),nullable=False)
    slug: Mapped[str] = mapped_column(String(200),nullable=False,unique=True)
    position: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    active: Mapped[bool] = mapped_column(Boolean,nullable=False,default=True)


class ProductCategory(Base):
    __tablename__ = 'catalog_product_categories'
    __table_args__ = (UniqueConstraint('product_id','category_id',name='uq_catalog_product_category'),
        Index('uq_catalog_one_primary_per_product','product_id',unique=True,
              postgresql_where=text('is_primary = true'),sqlite_where=text('is_primary = 1')))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('products.id',ondelete='CASCADE'),nullable=False,index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_categories.id',ondelete='RESTRICT'),nullable=False,index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)


class AttributeDefinition(Base):
    __tablename__ = 'catalog_attribute_definitions'
    __table_args__ = (CheckConstraint("data_type IN ('text','integer','decimal','boolean','enum')",name='catalog_attr_type'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80),nullable=False,unique=True)
    name: Mapped[str] = mapped_column(String(180),nullable=False)
    data_type: Mapped[str] = mapped_column(String(12),nullable=False)
    unit: Mapped[str | None] = mapped_column(String(24))
    enum_values: Mapped[list | None] = mapped_column(JSON)
    filterable: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    active: Mapped[bool] = mapped_column(Boolean,nullable=False,default=True)


class AttributeCodeHistory(Base):
    """Reserved historical filter codes; immutable FK to AttributeDefinition identity."""
    __tablename__ = 'catalog_attribute_code_history'
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80),nullable=False,unique=True,index=True)
    attribute_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_attribute_definitions.id',ondelete='CASCADE',name='fk_attr_code_history_def'),nullable=False,index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)


class CategoryAttribute(Base):
    __tablename__ = 'catalog_category_attributes'
    __table_args__ = (UniqueConstraint('category_id','attribute_id',name='uq_catalog_category_attribute'),
                      CheckConstraint('sort_order >= 0',name='ck_category_attribute_sort_order'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_categories.id',ondelete='CASCADE'),nullable=False,index=True)
    attribute_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_attribute_definitions.id',ondelete='RESTRICT',name='fk_catalog_category_attr_def'),nullable=False)
    required: Mapped[bool] = mapped_column(Boolean,nullable=False,default=False)
    show_in_filters: Mapped[bool] = mapped_column(Boolean,nullable=False,default=True,server_default=text('true'))
    sort_order: Mapped[int] = mapped_column(Integer,nullable=False,default=0,server_default=text('0'))
    group_name: Mapped[str] = mapped_column(String(80),nullable=False,default='Основные',server_default=text("'Основные'"))


class ProductAttribute(Base):
    __tablename__ = 'catalog_product_attributes'
    __table_args__ = (UniqueConstraint('product_id','attribute_id',name='uq_catalog_product_attribute'),
        CheckConstraint('((CASE WHEN value_text IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_integer IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_decimal IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_boolean IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN value_enum IS NOT NULL THEN 1 ELSE 0 END)) = 1',name='catalog_one_typed_value'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('products.id',ondelete='CASCADE'),nullable=False,index=True)
    attribute_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),ForeignKey('catalog_attribute_definitions.id',ondelete='RESTRICT',name='fk_catalog_product_attr_def'),nullable=False)
    value_text: Mapped[str | None] = mapped_column(String(500))
    value_integer: Mapped[int | None] = mapped_column(Integer)
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(18,6))
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_enum: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=utcnow,onupdate=utcnow)


class ProductMedia(Base):
    __tablename__ = 'catalog_product_media'
    __table_args__ = (CheckConstraint("kind IN ('image','document','logo')",name='catalog_media_kind'),
        CheckConstraint("status IN ('draft','approved','rejected')",name='catalog_media_status'),
        CheckConstraint('(product_id IS NOT NULL AND brand_id IS NULL) OR (product_id IS NULL AND brand_id IS NOT NULL)',name='catalog_media_one_owner'),
        CheckConstraint('size_bytes > 0',name='catalog_media_positive_size'),
        Index('ix_catalog_media_owner_product_position','product_id','position'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True),ForeignKey('products.id',ondelete='CASCADE'))
    brand_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True),ForeignKey('brands.id',ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(12),nullable=False)
    mime_type: Mapped[str] = mapped_column(String(70),nullable=False)
    original_filename: Mapped[str] = mapped_column(String(180),nullable=False)
    storage_key: Mapped[str] = mapped_column(String(180),nullable=False,unique=True)
    sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer,nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer,nullable=False,default=0)
    alt_text: Mapped[str | None] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(12),nullable=False,default='draft')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
