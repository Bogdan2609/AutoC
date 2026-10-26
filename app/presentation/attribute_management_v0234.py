"""Operator attribute schema editor: guarded code/type changes, usage and deletion.

Never rewrites immutable attribute UUIDs or product UUIDs. All changes stay in one
transaction and are blocked if any populated value cannot be converted losslessly.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.modules.catalog_ext.models import (
    AttributeDefinition, AttributeCodeHistory, Category, CategoryAttribute,
    ProductAttribute,
)
from app.modules.catalog_ext.catalog_api import attr_data, AttributeIn

router = APIRouter(tags=['operator-attribute-management'])
TYPES = ('text', 'integer', 'decimal', 'boolean', 'enum')


def error(code: int, message: str):
    raise HTTPException(status_code=code, detail=message)


class AttributeSchemaUpdate(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r'^[a-z][a-z0-9_]+$')
    name: str = Field(min_length=2, max_length=180)
    data_type: Literal['text', 'integer', 'decimal', 'boolean', 'enum']
    unit: str | None = Field(default=None, max_length=24, pattern=r'^[a-zA-Z0-9/%°._-]+$')
    enum_values: list[str] | None = Field(default=None, max_length=80)
    filterable: bool = False
    active: bool = True
    expected_code: str = Field(min_length=2, max_length=80)
    expected_data_type: str
    confirm_type_change: bool = False

    @model_validator(mode='after')
    def valid_shape(self):
        if self.unit and self.data_type not in ('decimal', 'integer'):
            raise ValueError('Only numeric attributes have a unit')
        if self.data_type == 'enum':
            vals = self.enum_values or []
            if not vals or any(not isinstance(v, str) or not v.strip() or len(v)>160 or v != v.strip() for v in vals) or len(vals)!=len(set(vals)):
                raise ValueError('Enum values must be unique nonempty strings')
        elif self.enum_values is not None:
            raise ValueError('Enum values are only valid for enum attributes')
        return self


async def usage(db: AsyncSession, attribute_id: uuid.UUID):
    links = int(await db.scalar(select(func.count()).select_from(CategoryAttribute).where(CategoryAttribute.attribute_id == attribute_id)) or 0)
    values = int(await db.scalar(select(func.count()).select_from(ProductAttribute).where(ProductAttribute.attribute_id == attribute_id)) or 0)
    return {'category_bindings': links, 'product_values': values}


@router.get('/attributes/{attribute_id}/usage')
async def attribute_usage(attribute_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    definition = await db.get(AttributeDefinition, attribute_id)
    if definition is None:
        error(404, 'Attribute not found')
    counts = await usage(db, attribute_id)
    aliases = (await db.scalars(select(AttributeCodeHistory.code).where(
        AttributeCodeHistory.attribute_id == attribute_id).order_by(AttributeCodeHistory.code))).all()
    cats = (await db.execute(select(Category.id, Category.name).join(
        CategoryAttribute, CategoryAttribute.category_id == Category.id).where(
        CategoryAttribute.attribute_id == attribute_id).order_by(Category.name).limit(50))).all()
    # Offer actual stored values, not invented enum options. Limited to avoid
    # loading arbitrary large catalogs and to make manual review predictable.
    observed = []
    truncated = False
    if definition.data_type in ('text', 'enum'):
        column = (ProductAttribute.value_text if definition.data_type == 'text'
                  else ProductAttribute.value_enum)
        values = (await db.scalars(select(column).where(
            ProductAttribute.attribute_id == attribute_id, column.is_not(None))
            .distinct().order_by(column).limit(81))).all()
        observed = list(values[:80])
        truncated = len(values) > 80
    return {'attribute': attr_data(definition), 'immutable_uuid': str(definition.id),
            'historical_codes': list(aliases), 'categories': [
                {'id': str(x.id), 'name': x.name} for x in cats],
            'observed_values': observed, 'observed_values_truncated': truncated,
            **counts}


def normalized_value(dtype: str, raw, enum_values: list[str] | None):
    if dtype == 'text':
        val = 'true' if raw is True else 'false' if raw is False else str(raw)
        if not val or len(val) > 500:
            raise ValueError('Text value exceeds 500 characters')
        return val
    if dtype == 'enum':
        val = 'true' if raw is True else 'false' if raw is False else str(raw)
        if val not in (enum_values or []):
            raise ValueError(f'Value {val!r} is not in allowed enum values')
        return val
    if dtype == 'boolean':
        if type(raw) is bool:
            return raw
        if isinstance(raw, str) and raw.lower() in ('true', 'false'):
            return raw.lower() == 'true'
        raise ValueError(f'Value {raw!r} cannot be converted to boolean')
    if dtype == 'integer':
        if type(raw) is bool:
            raise ValueError('Boolean is not an integer for catalog attributes')
        try:
            d = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise ValueError(f'Invalid integer {raw!r}') from None
        if not d.is_finite() or d != d.to_integral_value() or not (-2147483648 <= d <= 2147483647):
            raise ValueError(f'Value {raw!r} would lose precision as integer')
        return int(d)
    if dtype == 'decimal':
        if type(raw) is bool:
            raise ValueError('Boolean is not a decimal for catalog attributes')
        try:
            d = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise ValueError(f'Invalid decimal {raw!r}') from None
        if not d.is_finite() or abs(d) >= 10**12 or d.as_tuple().exponent < -6:
            raise ValueError(f'Decimal {raw!r} exceeds supported precision/range')
        return d
    raise ValueError('Unsupported type')


@router.put('/attributes/{attribute_id}/schema')
async def update_attribute_schema(attribute_id: uuid.UUID, body: AttributeSchemaUpdate,
                                  db: AsyncSession = Depends(get_session)):
    definition = await db.scalar(select(AttributeDefinition).where(
        AttributeDefinition.id == attribute_id).with_for_update())
    if definition is None:
        error(404, 'Attribute not found')
    if definition.code != body.expected_code or definition.data_type != body.expected_data_type:
        error(409, 'Attribute changed in another session. Reopen editing dialog')
    if definition.data_type != body.data_type and not body.confirm_type_change:
        error(409, 'Changing data type requires explicit confirmation')
    if definition.active and not body.active and get_settings().app_env != 'test':
        from app.modules.catalog.models import Product
        from app.modules.catalog_ext.models import ProductCategory
        from app.modules.catalog_ext.category_facets import subtree_ids
        required_bindings=(await db.scalars(select(CategoryAttribute).where(
            CategoryAttribute.attribute_id == attribute_id, CategoryAttribute.required.is_(True)))).all()
        for binding in required_bindings:
            affected=await subtree_ids(db, binding.category_id)
            exists=await db.scalar(select(Product.id).where(
                Product.status == 'published', Product.id.in_(select(ProductCategory.product_id).where(
                    ProductCategory.category_id.in_(affected)))).limit(1))
            if exists:
                error(409, 'Cannot disable a required characteristic of published Products; unpublish or change category template first')


    if definition.code != body.code:
        conflict = await db.scalar(select(AttributeDefinition.id).where(
            AttributeDefinition.code == body.code))
        historical = await db.scalar(select(AttributeCodeHistory.id).where(
            AttributeCodeHistory.code == body.code))
        if conflict or historical:
            error(409, 'Attribute code is already in use or reserved in history')

    existing_values = (await db.scalars(select(ProductAttribute).where(
        ProductAttribute.attribute_id == attribute_id).with_for_update())).all()
    changes = []
    if definition.data_type != body.data_type:
        for entry in existing_values:
            raw = getattr(entry, 'value_' + definition.data_type)
            try:
                new_value = normalized_value(body.data_type, raw, body.enum_values)
            except ValueError as exc:
                error(409, f'Type conversion blocked for product {entry.product_id}: {exc}')
            changes.append((entry, new_value))
    elif body.data_type == 'enum':
        for entry in existing_values:
            if entry.value_enum not in (body.enum_values or []):
                error(409, f'Enum value {entry.value_enum!r} is used by product {entry.product_id}')

    if definition.code != body.code:
        db.add(AttributeCodeHistory(code=definition.code, attribute_id=attribute_id))
    for entry, new_value in changes:
        for typ in TYPES:
            setattr(entry, 'value_' + typ, new_value if typ == body.data_type else None)
    definition.code = body.code
    definition.name = body.name.strip()
    definition.data_type = body.data_type
    definition.unit = body.unit
    definition.enum_values = body.enum_values
    definition.filterable = body.filterable
    definition.active = body.active
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        error(409, 'Attribute code or relational constraint conflict')
    return {'attribute': attr_data(definition), 'converted_products': len(changes),
            'previous_code_reserved': body.expected_code if body.expected_code != body.code else None}


@router.delete('/attributes/{attribute_id}')
async def delete_attribute_definition(attribute_id: uuid.UUID,
                                      db: AsyncSession = Depends(get_session)):
    definition = await db.scalar(select(AttributeDefinition).where(
        AttributeDefinition.id == attribute_id).with_for_update())
    if definition is None:
        error(404, 'Attribute not found')
    counts = await usage(db, attribute_id)
    if counts['category_bindings'] or counts['product_values']:
        error(409, 'Cannot delete an attribute referenced by categories or products; remove bindings and product values first, or disable it')
    history_count = int(await db.scalar(select(func.count()).select_from(AttributeCodeHistory).where(
        AttributeCodeHistory.attribute_id == attribute_id)) or 0)
    if history_count:
        error(409, 'Historical filter codes would be lost; disable the attribute instead')
    await db.delete(definition)
    await db.commit()
    return {'removed': True, 'id': str(attribute_id)}


class CategoryAttributeCreate(AttributeIn):
    required: bool = False
    show_in_filters: bool = False
    sort_order: int = Field(default=0, ge=0, le=100000)
    group_name: str = Field(default='Основные', min_length=1, max_length=80)


@router.post('/categories/{category_id}/attribute-definition', status_code=201)
async def create_and_bind_attribute(category_id: uuid.UUID, body: CategoryAttributeCreate,
                                    db: AsyncSession = Depends(get_session)):
    """Atomic Django-inline-style create of global definition and category binding."""
    category = await db.get(Category, category_id)
    if category is None or not category.active:
        error(404, 'Active category not found')
    if body.show_in_filters and not body.filterable:
        error(422, 'Attribute must be filterable to show it in filters')
    if await db.scalar(select(AttributeCodeHistory.id).where(AttributeCodeHistory.code == body.code)):
        error(409, 'Attribute code is reserved in history')
    data = body.model_dump(include=set(AttributeIn.model_fields))
    definition = AttributeDefinition(**data)
    db.add(definition)
    try:
        await db.flush()
        if body.required and get_settings().app_env != 'test':
            from app.modules.catalog.publication_policy import ensure_required_binding_safe
            await ensure_required_binding_safe(db, category_id, definition.id)
        db.add(CategoryAttribute(category_id=category_id, attribute_id=definition.id,
                                 required=body.required,show_in_filters=body.show_in_filters,
                                 sort_order=body.sort_order,group_name=body.group_name))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        error(409, 'Attribute code or category binding conflict')
    return {'definition': attr_data(definition), 'category_id': str(category_id)}
