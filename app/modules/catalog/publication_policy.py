"""Central, read-only publication validator shared by operator readiness and publish.

Catalog is intentionally independent from supplier stock/price.  A publishable
catalog identity does not guarantee any sellable offer.
"""
from __future__ import annotations

from sqlalchemy import select
from app.modules.catalog.models import Brand, Product
from app.modules.catalog_ext.models import (Category, ProductCategory,
    CategoryAttribute, AttributeDefinition, ProductAttribute)
from app.modules.master_catalog.models import ProductComposition


def _issue(code: str, message: str, severity: str = 'error') -> dict:
    return {'code': code, 'message': message, 'severity': severity}


async def publication_issues(db, product: Product) -> list[dict]:
    """Return deterministic issues; never mutate rows or call external APIs.

    Product row should be locked by caller before a production publication.
    Other catalog mutations use a matching Product lock when changing links.
    """
    issues = []
    if not product.name or len(product.name.strip()) < 2:
        issues.append(_issue('name', 'Название товара должно содержать не менее двух символов.'))
    if not product.article_norm or not product.slug:
        issues.append(_issue('identity', 'Не заполнены нормализованный артикул или slug.'))
    brand = await db.get(Brand, product.brand_id)
    if not brand:
        issues.append(_issue('brand', 'Не найден бренд товара.'))
    elif brand.name_norm == 'CATALOG FIXTURE':
        issues.append(_issue('qa_fixture', 'Синтетические QA-товары нельзя публиковать.'))

    memberships = (await db.scalars(select(ProductCategory).where(
        ProductCategory.product_id == product.id))).all()
    primaries = [row for row in memberships if row.is_primary]
    if len(primaries) != 1:
        issues.append(_issue('primary_category', 'Должна быть ровно одна основная категория.'))
    all_cats = set()
    for membership in memberships:
        cur = membership.category_id
        seen = set()
        while cur is not None:
            if cur in seen or len(seen) >= 1000:
                issues.append(_issue('category_cycle', 'Обнаружен цикл или чрезмерная глубина дерева категорий.'))
                break
            seen.add(cur)
            cat = await db.get(Category, cur)
            if cat is None:
                issues.append(_issue('category_missing', 'Категория товара отсутствует.'))
                break
            if not cat.active:
                issues.append(_issue('category_inactive', 'Товар относится к неактивной категории или родителю.'))
            all_cats.add(cat.id)
            cur = cat.parent_id

    if all_cats:
        bindings = (await db.scalars(select(CategoryAttribute).where(
            CategoryAttribute.category_id.in_(all_cats)))).all()
        required = {row.attribute_id for row in bindings if row.required}
        if required:
            definitions = {row.id: row for row in (await db.scalars(select(AttributeDefinition).where(
                AttributeDefinition.id.in_(required)))).all()}
            values = {row.attribute_id: row for row in (await db.scalars(select(ProductAttribute).where(
                ProductAttribute.product_id == product.id, ProductAttribute.attribute_id.in_(required)))).all()}
            for aid in sorted(required, key=str):
                definition = definitions.get(aid)
                if definition is None or not definition.active:
                    issues.append(_issue('required_definition', 'Обязательная характеристика не найдена или отключена.'))
                    continue
                stored = values.get(aid)
                value = getattr(stored, 'value_' + definition.data_type, None) if stored else None
                if value is None or (isinstance(value, str) and not value.strip()):
                    issues.append(_issue('required_attribute', 'Не заполнена обязательная характеристика: ' + definition.code))
                elif definition.data_type == 'enum' and value not in (definition.enum_values or []):
                    issues.append(_issue('invalid_enum', 'Недопустимое значение enum: ' + definition.code))

    composition = await db.get(ProductComposition, product.id)
    if composition is not None and composition.kind == 'assembled_plan':
        issues.append(_issue('assembled_plan', 'Сборочный план нельзя публиковать без атомарного резервирования компонентов.'))
    # Keep operator output stable across DB row iteration order.
    return sorted(issues, key=lambda x: (x['code'], x['message']))


async def ensure_required_binding_safe(db, category_id, attribute_id) -> None:
    """Prevent new required fields from invalidating *existing published* products.

    Lock affected published Product rows to serialize with the publication route.
    Rejection makes no changes to current bindings/values.
    """
    from fastapi import HTTPException
    from app.modules.catalog_ext.category_facets import subtree_ids
    descendants = await subtree_ids(db, category_id)
    affected = (await db.scalars(select(Product).where(
        Product.status == 'published', Product.id.in_(
            select(ProductCategory.product_id).where(ProductCategory.category_id.in_(descendants))
        )).order_by(Product.id).with_for_update())).all()
    if not affected:
        return
    assigned = set((await db.scalars(select(ProductAttribute.product_id).where(
        ProductAttribute.attribute_id == attribute_id,
        ProductAttribute.product_id.in_([p.id for p in affected])))).all())
    if any(p.id not in assigned for p in affected):
        raise HTTPException(status_code=409, detail='Cannot require a new attribute: published products are missing values; unpublish/fill them first')
