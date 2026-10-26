"""v0.23 R3: category attribute templates and published-category facets.

No supplier calls. Admin writes affect only category attribute configuration and
product typed facts. Existing product identities and offers are unchanged.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, distinct
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product, Brand
from app.modules.catalog_ext.models import (
    Category, CategoryAttribute, AttributeDefinition, ProductAttribute, ProductCategory
)

admin = APIRouter(prefix='/api/v1/backoffice/catalog-editor',
                 tags=['category-template-admin'], dependencies=[Depends(require_admin)])
public = APIRouter(prefix='/api/v1/catalog-v021', tags=['category-facets'])


def fail(status: int, message: str):
    raise HTTPException(status_code=status, detail=message)


async def chain(db: AsyncSession, category_id: uuid.UUID):
    """Root to leaf; reject cycles, even after manual SQL edits."""
    seen = set()
    result = []
    current = await db.get(Category, category_id)
    if current is None:
        fail(404, 'Category not found')
    while current is not None:
        if current.id in seen or len(result) >= 1000:
            fail(409, 'Invalid category ancestry')
        seen.add(current.id)
        result.append(current)
        current = await db.get(Category, current.parent_id) if current.parent_id else None
    return result[::-1]


async def effective_template(db: AsyncSession, category_id: uuid.UUID):
    ancestors = await chain(db, category_id)
    links = (await db.scalars(select(CategoryAttribute).where(
        CategoryAttribute.category_id.in_([node.id for node in ancestors])))).all()
    by_node = {}
    for link in links:
        by_node.setdefault(link.category_id, []).append(link)
    merged = {}
    for node in ancestors:
        for link in by_node.get(node.id, []):
            prev = merged.get(link.attribute_id)
            merged[link.attribute_id] = {
                'id': link.attribute_id,
                'source_category_id': node.id,
                'direct': node.id == category_id,
                'required': bool(link.required or (prev and prev['required'])),
                'show_in_filters': link.show_in_filters,
                'sort_order': link.sort_order,
                'group_name': link.group_name or 'Основные',
            }
    if not merged:
        return []
    defs = (await db.scalars(select(AttributeDefinition).where(
        AttributeDefinition.id.in_(list(merged))))).all()
    output = []
    for definition in defs:
        cfg = merged[definition.id]
        output.append({
            'attribute_id': str(definition.id), 'code': definition.code,
            'name': definition.name, 'data_type': definition.data_type,
            'unit': definition.unit, 'enum_values': definition.enum_values,
            'filterable': definition.filterable, 'active': definition.active,
            'required': cfg['required'], 'direct': cfg['direct'],
            'source_category_id': str(cfg['source_category_id']),
            'show_in_filters': cfg['show_in_filters'],
            'sort_order': cfg['sort_order'], 'group_name': cfg['group_name'],
        })
    return sorted(output, key=lambda x: (x['sort_order'], x['group_name'].lower(), x['name'].lower()))


class TemplateBindingUpdate(BaseModel):
    required: bool = False
    show_in_filters: bool = True
    sort_order: int = Field(default=0, ge=0, le=100000)
    group_name: str = Field(default='Основные', min_length=1, max_length=80)


@admin.put('/categories/{category_id}/template/{attribute_id}')
async def put_category_template_binding(category_id: uuid.UUID, attribute_id: uuid.UUID,
                                        body: TemplateBindingUpdate,
                                        db: AsyncSession = Depends(get_session)):
    category = await db.get(Category, category_id)
    definition = await db.get(AttributeDefinition, attribute_id)
    if not category or not definition:
        fail(404, 'Category or attribute not found')
    if not category.active or not definition.active:
        fail(409, 'Inactive category or attribute')
    if body.show_in_filters and not definition.filterable:
        fail(422, 'Enable filterable on the attribute definition first')
    from app.core.config import get_settings
    if body.required and get_settings().app_env != 'test':
        from app.modules.catalog.publication_policy import ensure_required_binding_safe
        await ensure_required_binding_safe(db, category_id, attribute_id)
    row = await db.scalar(select(CategoryAttribute).where(
        CategoryAttribute.category_id == category_id,
        CategoryAttribute.attribute_id == attribute_id).with_for_update())
    if row is None:
        # Also supports explicit per-child overrides of inherited configuration.
        row = CategoryAttribute(category_id=category_id, attribute_id=attribute_id)
        db.add(row)
    row.required = body.required
    row.show_in_filters = body.show_in_filters
    row.sort_order = body.sort_order
    row.group_name = body.group_name.strip()
    await db.commit()
    return {'category_id': str(category_id), 'attribute_id': str(attribute_id),
            **body.model_dump(exclude={'group_name'}), 'group_name': row.group_name}


@admin.get('/categories/{category_id}/template')
async def category_template(category_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    category = await db.get(Category, category_id)
    if category is None:
        fail(404, 'Category not found')
    return {'category_id': str(category_id), 'items': await effective_template(db, category_id)}


@admin.get('/products/{product_id}/attribute-form')
async def product_attribute_form(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    product = await db.get(Product, product_id)
    if not product:
        fail(404, 'Product not found')
    assignments = (await db.scalars(select(ProductCategory).where(
        ProductCategory.product_id == product_id))).all()
    # The primary category drives the form; secondary categories may add attributes.
    assignments.sort(key=lambda x: (not x.is_primary, str(x.category_id)))
    merged = {}
    for membership in assignments:
        for item in await effective_template(db, membership.category_id):
            prev = merged.get(item['attribute_id'])
            if prev is None:
                merged[item['attribute_id']] = dict(item)
            else:
                prev['required'] = prev['required'] or item['required']
    vals = (await db.scalars(select(ProductAttribute).where(
        ProductAttribute.product_id == product_id))).all()
    for row in vals:
        item = merged.get(str(row.attribute_id))
        if item is None:
            continue
        raw = getattr(row, 'value_' + item['data_type'])
        item['value'] = str(raw) if isinstance(raw, Decimal) else raw
    items = list(merged.values())
    for item in items:
        item.setdefault('value', None)
    items.sort(key=lambda x: (x['sort_order'], x['group_name'].lower(), x['name'].lower()))
    return {'product_id': str(product_id), 'primary_category_id': (str(next((r.category_id for r in assignments if r.is_primary), '')) or None),
        'items': items, 'missing_required': [x['code'] for x in items
            if x['required'] and x['active'] and x['value'] is None]}


class AttributeValueItem(BaseModel):
    attribute_id: uuid.UUID
    value: str | int | float | bool


class BatchAttributeValues(BaseModel):
    items: list[AttributeValueItem] = Field(min_length=1, max_length=100)


@admin.put('/products/{product_id}/attribute-values')
async def batch_attribute_values(product_id: uuid.UUID, body: BatchAttributeValues,
                                 db: AsyncSession = Depends(get_session)):
    """Atomic, typed, category-authorized upsert. No empty-value clearing."""
    from app.modules.catalog_ext.catalog_api import applicable_attrs
    if not await db.get(Product, product_id):
        fail(404, 'Product not found')
    allowed = await applicable_attrs(db, product_id)
    if len({r.attribute_id for r in body.items}) != len(body.items):
        fail(422, 'Duplicate attribute in batch')
    changes = []
    for item in body.items:
        definition = await db.get(AttributeDefinition, item.attribute_id)
        if not definition or not definition.active or item.attribute_id not in allowed:
            fail(409, 'Attribute is not active or is not assigned to this product category')
        v = item.value
        typed = dict(value_text=None, value_integer=None, value_decimal=None,
                     value_boolean=None, value_enum=None)
        if definition.data_type == 'text':
            if not isinstance(v, str) or not v.strip() or len(v) > 500:
                fail(422, 'Invalid text')
            typed['value_text'] = v.strip()
        elif definition.data_type == 'enum':
            if not isinstance(v, str) or v not in (definition.enum_values or []):
                fail(422, 'Invalid enum value')
            typed['value_enum'] = v
        elif definition.data_type == 'integer':
            if type(v) is not int or not -2147483648 <= v <= 2147483647:
                fail(422, 'Invalid integer')
            typed['value_integer'] = v
        elif definition.data_type == 'boolean':
            if type(v) is not bool:
                fail(422, 'Invalid boolean')
            typed['value_boolean'] = v
        elif definition.data_type == 'decimal':
            if isinstance(v, bool) or not isinstance(v, (str, int, float)):
                fail(422, 'Invalid decimal')
            try:
                d = Decimal(str(v))
            except InvalidOperation:
                fail(422, 'Invalid decimal')
            if not d.is_finite() or abs(d) >= 10**12 or d.as_tuple().exponent < -6:
                fail(422, 'Invalid decimal')
            typed['value_decimal'] = d
        existing = await db.scalar(select(ProductAttribute).where(
            ProductAttribute.product_id == product_id,
            ProductAttribute.attribute_id == item.attribute_id).with_for_update())
        if existing is None:
            existing = ProductAttribute(product_id=product_id, attribute_id=item.attribute_id)
            db.add(existing)
        for k, v in typed.items():
            setattr(existing, k, v)
        changes.append(definition.code)
    await db.commit()
    return {'product_id': str(product_id), 'saved': changes, 'count': len(changes)}


async def subtree_ids(db: AsyncSession, category_id: uuid.UUID):
    cte = select(Category.id).where(Category.id == category_id).cte('tree', recursive=True)
    cte = cte.union_all(select(Category.id).join(
        cte, Category.parent_id == cte.c.id).where(Category.active == True))
    return select(cte.c.id)


@public.get('/categories/{category_id}/facets')
async def category_facets(category_id: uuid.UUID,
                          db: AsyncSession = Depends(get_session)):
    """Facet configuration + observed published values; never drafts or supplier history."""
    category = await db.get(Category, category_id)
    if category is None or not category.active:
        fail(404, 'Category not found')
    definitions = [x for x in await effective_template(db, category_id)
                   if x['active'] and x['filterable'] and x['show_in_filters']]
    definitions=definitions[:80]
    if not definitions:
        return {'category_id':str(category_id),'facets':[],'total_facets':0}
    subtree = await subtree_ids(db, category_id)
    ids = [uuid.UUID(x['attribute_id']) for x in definitions]
    scoped = (select(ProductAttribute)
        .join(Product,Product.id==ProductAttribute.product_id)
        .join(Brand,Brand.id==Product.brand_id)
        .where(Product.status=='published',Brand.name_norm!='CATALOG FIXTURE',
               ProductAttribute.attribute_id.in_(ids),
               Product.id.in_(select(ProductCategory.product_id).where(
                   ProductCategory.category_id.in_(subtree)))))
    # One count query for all attributes: an accurate DISTINCT product count,
    # independent of the truncation of top-valued options.
    facts = scoped.subquery()
    counts = dict((await db.execute(select(facts.c.attribute_id,
        func.count(distinct(facts.c.product_id))).group_by(facts.c.attribute_id))).all())
    detail={}
    by_type={}
    for x in definitions:
        by_type.setdefault(x['data_type'],[]).append(uuid.UUID(x['attribute_id']))
    for dtype, dtype_ids in by_type.items():
        col=getattr(ProductAttribute,'value_'+dtype)
        stmt=(scoped.where(ProductAttribute.attribute_id.in_(dtype_ids),col.is_not(None)))
        if dtype in ('integer','decimal'):
            qry=(stmt.with_only_columns(ProductAttribute.attribute_id,
                func.min(col),func.max(col)).group_by(ProductAttribute.attribute_id))
            for attr_id,minimum,maximum in (await db.execute(qry)).all():
                detail[attr_id]={'min':str(minimum) if minimum is not None else None,
                    'max':str(maximum) if maximum is not None else None,'options':[]}
        else:
            grouped=(stmt.with_only_columns(ProductAttribute.attribute_id.label('attr'),
                col.label('value'),func.count(distinct(ProductAttribute.product_id)).label('n'))
                .group_by(ProductAttribute.attribute_id,col)).subquery()
            ranked=(select(grouped.c.attr,grouped.c.value,grouped.c.n,
                func.row_number().over(partition_by=grouped.c.attr,
                    order_by=(grouped.c.n.desc(),grouped.c.value)).label('rank'),
                func.count().over(partition_by=grouped.c.attr).label('option_count'))).subquery()
            rows=(await db.execute(select(ranked.c.attr,ranked.c.value,
                ranked.c.n,ranked.c.option_count).where(ranked.c.rank<=50)
                .order_by(ranked.c.attr,ranked.c.rank))).all()
            for attr_id,value,count,option_count in rows:
                slot=detail.setdefault(attr_id,{'options':[], 'truncated':option_count>50})
                slot['options'].append({'value':value,'count':count})
    facets=[]
    for item in definitions:
        attr_id=uuid.UUID(item['attribute_id'])
        default={'options':[],'min':None,'max':None} if item['data_type'] in ('integer','decimal') else {'options':[],'truncated':False}
        facets.append({**item, **(detail.get(attr_id,default)), 'products':counts.get(attr_id,0)})
    return {'category_id':str(category_id), 'facets': facets, 'total_facets':len(definitions)}


class ProductAttributeOperation(BaseModel):
    """An explicit, optimistic, field-scoped change from the product modal."""
    attribute_id: uuid.UUID
    action: Literal['set', 'remove']
    value: str | int | float | bool | None = None
    expected_value: str | int | float | bool | None = None


class ProductAttributeChangeSet(BaseModel):
    changes: list[ProductAttributeOperation] = Field(min_length=1, max_length=100)


def current_attribute_value(entry: ProductAttribute | None, definition: AttributeDefinition):
    if entry is None:
        return None
    value = getattr(entry, 'value_' + definition.data_type)
    return str(value) if isinstance(value, Decimal) else value


@admin.patch('/products/{product_id}/attribute-changes')
async def change_product_attributes(product_id: uuid.UUID, body: ProductAttributeChangeSet,
                                    db: AsyncSession = Depends(get_session)):
    """Atomic set/remove with optimistic collision detection and no implicit deletion.

    The old bulk PUT endpoint remains available for compatibility. A client must
    explicitly select each operation; expected_value detects stale modals.
    """
    from app.modules.catalog_ext.catalog_api import applicable_attrs
    product = await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        fail(404, 'Product not found')
    ids = [x.attribute_id for x in body.changes]
    if len(ids) != len(set(ids)):
        fail(422, 'Duplicate attribute operation in one request')
    allowed = await applicable_attrs(db, product_id)
    definitions = {x.id: x for x in (await db.scalars(select(AttributeDefinition).where(
        AttributeDefinition.id.in_(ids)))).all()}
    existing = {x.attribute_id: x for x in (await db.scalars(select(ProductAttribute).where(
        ProductAttribute.product_id == product_id,
        ProductAttribute.attribute_id.in_(ids)).with_for_update())).all()}
    prepared = []
    for op in body.changes:
        definition = definitions.get(op.attribute_id)
        if definition is None or op.attribute_id not in allowed:
            fail(409, 'Characteristic is not assigned to a category of this Product')
        row = existing.get(op.attribute_id)
        before = current_attribute_value(row, definition)
        if before != op.expected_value:
            # Normalize numbers for exact but representation-independent comparisons:
            # Decimal('5.000000') is shown to the UI as '5.000000'.
            if not (definition.data_type == 'decimal' and before is not None and
                    op.expected_value is not None and
                    str(before) == str(op.expected_value)):
                fail(409, 'Attribute value was changed by another editor; reopen the dialog')
        if op.action == 'remove':
            if row is None:
                fail(409, 'Cannot remove a value that does not exist')
            if product.status == 'published' and allowed[op.attribute_id]:
                fail(409, 'Unpublish the Product before deleting a required characteristic')
            if op.value is not None:
                fail(422, 'Remove operation must not contain a value')
            prepared.append((op, definition, row, None))
            continue
        if not definition.active:
            fail(409, 'Inactive characteristic cannot receive a value')
        if op.value is None:
            fail(422, 'Set operation requires a value; use remove explicitly')
        from app.presentation.attribute_management_v0234 import normalized_value
        try:
            typed = normalized_value(definition.data_type, op.value, definition.enum_values)
        except ValueError as exc:
            fail(422, str(exc))
        # Input must honor the selected type; avoid boolean masquerading as 1
        # and strings masquerading as booleans, except explicit true/false UI values.
        if definition.data_type == 'text' and type(op.value) is not str:
            fail(422, 'Text requires a string')
        if definition.data_type == 'integer' and type(op.value) is not int:
            fail(422, 'Integer requires a number without a fraction')
        if definition.data_type == 'boolean' and type(op.value) is not bool:
            fail(422, 'Boolean requires true or false')
        if definition.data_type == 'enum' and type(op.value) is not str:
            fail(422, 'Enum value must be a selected option')
        prepared.append((op, definition, row, typed))

    codes = []
    for op, definition, row, typed in prepared:
        if op.action == 'remove':
            await db.delete(row)
        else:
            if row is None:
                row = ProductAttribute(product_id=product_id, attribute_id=op.attribute_id)
                db.add(row)
            for dtype in ('text', 'integer', 'decimal', 'boolean', 'enum'):
                setattr(row, 'value_' + dtype, typed if dtype == definition.data_type else None)
        codes.append({'code': definition.code, 'operation': op.action})
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        fail(409, 'Catalog constraint violation; changes rolled back')
    return {'product_id': str(product_id), 'changed': codes, 'count': len(codes)}


@admin.get('/products/{product_id}/readiness')
async def product_readiness(product_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    """Read-only operator checks; never change publication state automatically."""
    from app.modules.catalog.models import Brand
    from app.modules.catalog_ext.models import ProductMedia
    from app.modules.master_catalog.models import ProductComposition, ComponentLine
    p = await db.get(Product, product_id)
    if not p: fail(404, 'Product not found')
    brand = await db.get(Brand, p.brand_id)
    cats = (await db.scalars(select(ProductCategory).where(
        ProductCategory.product_id == product_id))).all()
    from app.modules.catalog.publication_policy import publication_issues
    issues = await publication_issues(db, p)
    cats = (await db.scalars(select(ProductCategory).where(ProductCategory.product_id == product_id))).all()
    if brand and brand.name_norm == 'CATALOG FIXTURE' and p.status == 'published':
        issues.append({'severity':'error', 'code':'published_qa_fixture',
                       'message':'Демонстрационный QA-товар уже опубликован в старом каталоге.'})
    comp = await db.get(ProductComposition, product_id)
    if comp and comp.kind == 'factory_kit' and comp.verified:
        count = int(await db.scalar(select(func.count()).select_from(ComponentLine).where(
            ComponentLine.parent_product_id == product_id)) or 0)
        if count < 2:
            issues.append({'severity':'warning','code':'short_kit_composition',
                           'message':'Заводской комплект подтверждён, но состав содержит меньше двух позиций.'})
    images = int(await db.scalar(select(func.count()).select_from(ProductMedia).where(
        ProductMedia.product_id == product_id,
        ProductMedia.kind == 'image',ProductMedia.status == 'approved')) or 0)
    if images == 0:
        issues.append({'severity':'warning','code':'no_approved_photo',
                       'message':'Нет одобренных фотографий товара.'})
    if brand and brand.name_norm == 'NGK' and p.article_norm == 'BKR6E11' and p.status == 'published':
        issues.append({'severity':'warning','code':'ngk_fixture_review',
                       'message':'Тест NGK опубликован. Проверь вручную, нужна ли публичная карточка.'})
    return {'product_id':str(product_id),'status':p.status,'ready':not any(
        x['severity']=='error' for x in issues),'issues':issues,
        'primary_category_count':sum(1 for c in cats if c.is_primary),
        'approved_images':images}
