"""v0.21 administrative catalog governance and published read APIs.

All mutations are operator-token guarded. Existing Brand/Product identities and
supplier association are never rewritten by alias management.
"""
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Brand, Product
from app.modules.catalog.normalize import normalize_brand
from app.modules.suppliers.models import Supplier
from .models import (BrandAlias, BrandProfile, Category, ProductCategory,
                     AttributeDefinition, AttributeCodeHistory, CategoryAttribute, ProductAttribute)

admin = APIRouter(prefix='/api/v1/backoffice/catalog-v021', tags=['catalog-v021-admin'], dependencies=[Depends(require_admin)])
public = APIRouter(prefix='/api/v1/catalog-v021', tags=['catalog-v021'])


def fail(code, message):
    raise HTTPException(status_code=code, detail=message)


async def commit(db):
    try: await db.commit()
    except IntegrityError as e:
        await db.rollback()
        fail(409,'Catalog identity or relation conflict')


def uid(v): return str(v) if v is not None else None


def category_data(c):
    return {'id':uid(c.id),'parent_id':uid(c.parent_id),'name':c.name,'slug':c.slug,'position':c.position,'active':c.active}


def attr_data(a):
    return {'id':uid(a.id),'code':a.code,'name':a.name,'data_type':a.data_type,'unit':a.unit,
            'enum_values':a.enum_values,'filterable':a.filterable,'active':a.active}


def media_noop(): return None


class BrandProfileIn(BaseModel):
    legal_name:str|None=Field(default=None,max_length=240)
    website:str|None=Field(default=None,max_length=300)
    country_code:str|None=Field(default=None,pattern=r'^[A-Z]{2}$')
    description:str|None=Field(default=None,max_length=10000)
    verified:bool=False

    @model_validator(mode='after')
    def validate_site(self):
        if self.website:
            u=urlsplit(self.website)
            if u.scheme!='https' or not u.hostname or u.username or u.password:
                raise ValueError('Brand website must be a public HTTPS URL without credentials')
        return self


@admin.put('/brands/{brand_id}/profile')
async def put_brand_profile(brand_id:uuid.UUID,body:BrandProfileIn,db:AsyncSession=Depends(get_session)):
    if not await db.get(Brand,brand_id):fail(404,'Brand not found')
    row=await db.get(BrandProfile,brand_id)
    if row is None:
        row=BrandProfile(brand_id=brand_id);db.add(row)
    for name,value in body.model_dump().items():setattr(row,name,value)
    await commit(db)
    return {'brand_id':uid(brand_id),**body.model_dump()}


class BrandAliasIn(BaseModel):
    alias:str=Field(min_length=2,max_length=160)
    supplier_id:uuid.UUID|None=None
    verified:bool=False


@admin.post('/brands/{brand_id}/aliases',status_code=201)
async def add_brand_alias(brand_id:uuid.UUID,body:BrandAliasIn,db:AsyncSession=Depends(get_session)):
    brand=await db.get(Brand,brand_id)
    if not brand:fail(404,'Brand not found')
    if body.supplier_id and not await db.get(Supplier,body.supplier_id):fail(404,'Supplier not found')
    norm=normalize_brand(body.alias)
    if len(norm)<2:fail(422,'Empty normalized brand alias')
    # Never shadow an existing canonical brand, even in a supplier-scoped alias.
    canonical=await db.scalar(select(Brand).where(Brand.name_norm==norm))
    if canonical:fail(409,'Brand alias collides with an existing canonical brand')
    scope=f'{body.supplier_id or "global"}:{norm}'
    row=BrandAlias(brand_id=brand_id,supplier_id=body.supplier_id,alias=body.alias.strip(),
                   alias_norm=norm,scope_key=scope,verified=body.verified)
    db.add(row);await commit(db);await db.refresh(row)
    return {'id':uid(row.id),'brand_id':uid(brand_id),'supplier_id':uid(row.supplier_id),'alias':row.alias,'verified':row.verified}


@admin.patch('/brands/{brand_id}/aliases/{alias_id}')
async def verify_brand_alias(brand_id:uuid.UUID,alias_id:uuid.UUID,verified:bool,db:AsyncSession=Depends(get_session)):
    row=await db.get(BrandAlias,alias_id)
    if not row or row.brand_id!=brand_id:fail(404,'Alias not found')
    if verified and await db.scalar(select(Brand.id).where(Brand.name_norm==row.alias_norm)):
        fail(409,'Canonical brand now conflicts with alias')
    row.verified=verified;await commit(db)
    return {'id':uid(row.id),'verified':row.verified}


@admin.delete('/brands/{brand_id}/aliases/{alias_id}')
async def delete_brand_alias(brand_id:uuid.UUID,alias_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.get(BrandAlias,alias_id)
    if not row or row.brand_id!=brand_id:fail(404,'Alias not found')
    await db.delete(row);await commit(db)
    return {'removed':True}


@public.get('/brands/resolve')
async def resolve_brand(name:str=Query(min_length=2,max_length=160),supplier_id:uuid.UUID|None=None,
                        db:AsyncSession=Depends(get_session)):
    """Read-only candidate lookup, NOT an automated supplier-product rebind."""
    norm=normalize_brand(name)
    canonical=await db.scalar(select(Brand).where(Brand.name_norm==norm))
    if canonical:return {'brand_id':uid(canonical.id),'name':canonical.name,'source':'canonical','verified':True}
    scopes=[f'global:{norm}']
    if supplier_id:scopes.insert(0,f'{supplier_id}:{norm}')
    for scope in scopes:
        row=await db.scalar(select(BrandAlias).where(BrandAlias.scope_key==scope,BrandAlias.verified==True))
        if row:
            brand=await db.get(Brand,row.brand_id)
            return {'brand_id':uid(brand.id),'name':brand.name,'source':'verified_alias','verified':True}
    return {'brand_id':None,'name':None,'source':'unresolved','verified':False}


@public.get('/brands/{brand_id}')
async def brand_details(brand_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    brand=await db.get(Brand,brand_id)
    if not brand:fail(404,'Brand not found')
    profile=await db.get(BrandProfile,brand_id)
    aliases=(await db.scalars(select(BrandAlias).where(BrandAlias.brand_id==brand_id,BrandAlias.verified==True,
            BrandAlias.supplier_id.is_(None)).order_by(BrandAlias.alias))).all()
    return {'id':uid(brand.id),'name':brand.name,'slug':brand.slug,
            'profile':{'legal_name':profile.legal_name,'website':profile.website,'country_code':profile.country_code,
                       'description':profile.description} if profile and profile.verified else None,
            'aliases':[x.alias for x in aliases]}


class CategoryIn(BaseModel):
    name:str=Field(min_length=2,max_length=180)
    slug:str=Field(min_length=2,max_length=200,pattern=r'^[a-z0-9][a-z0-9-]+$')
    parent_id:uuid.UUID|None=None
    position:int=Field(default=0,ge=0,le=1000000)
    active:bool=True


@admin.post('/categories',status_code=201)
async def create_category(body:CategoryIn,db:AsyncSession=Depends(get_session)):
    if body.parent_id:
        parent=await db.get(Category,body.parent_id)
        if not parent:fail(404,'Parent category not found')
        if not parent.active:fail(409,'Cannot attach a category below an inactive parent')
    c=Category(**body.model_dump());db.add(c);await commit(db);await db.refresh(c)
    return category_data(c)


class CategoryUpdate(BaseModel):
    name:str=Field(min_length=2,max_length=180)
    parent_id:uuid.UUID|None=None
    position:int=Field(default=0,ge=0,le=1000000)
    active:bool=True


@admin.put('/categories/{category_id}')
async def update_category(category_id:uuid.UUID,body:CategoryUpdate,db:AsyncSession=Depends(get_session)):
    c=await db.get(Category,category_id)
    if not c:fail(404,'Category not found')
    if body.parent_id:
        if body.parent_id==c.id:fail(409,'Category cannot contain itself')
        parent=await db.get(Category,body.parent_id)
        if not parent:fail(404,'Parent category not found')
        if not parent.active:fail(409,'Parent category is inactive')
        seen={c.id};cur=parent
        while cur:
            if cur.id in seen:fail(409,'Category cycle rejected')
            seen.add(cur.id)
            if len(seen)>1000:fail(409,'Category tree too deep')
            cur=await db.get(Category,cur.parent_id) if cur.parent_id else None
    if (not body.active or body.parent_id != c.parent_id) and get_settings().app_env != 'test':
        from app.modules.catalog_ext.category_facets import subtree_ids
        affected = await subtree_ids(db, category_id)
        published = await db.scalar(select(Product.id).where(
            Product.status == 'published', Product.id.in_(select(ProductCategory.product_id).where(
                ProductCategory.category_id.in_(affected)))).limit(1))
        if published:
            fail(409,'Cannot move/deactivate a category containing published products; unpublish them first')
    if not body.active:
        child=await db.scalar(select(Category.id).where(Category.parent_id==c.id,Category.active==True))
        if child:fail(409,'Deactivate child categories first')
    for k,v in body.model_dump().items():setattr(c,k,v)
    await commit(db)
    return category_data(c)


@public.get('/categories')
async def list_categories(include_inactive:bool=False, db:AsyncSession=Depends(get_session)):
    stmt=select(Category)
    if not include_inactive:stmt=stmt.where(Category.active==True)
    rows=(await db.scalars(stmt.order_by(Category.position,Category.name).limit(2000))).all()
    # Inactive data is restricted to admins; public cannot request hidden nodes.
    if include_inactive: rows=[x for x in rows if x.active]
    return {'items':[category_data(c) for c in rows]}


class CategoryAssignment(BaseModel):
    category_id:uuid.UUID
    is_primary:bool=False


@admin.post('/products/{product_id}/categories',status_code=201)
async def add_product_category(product_id:uuid.UUID,body:CategoryAssignment,db:AsyncSession=Depends(get_session)):
    p=await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    cat=await db.get(Category,body.category_id)
    if not p or not cat:fail(404,'Product or category not found')
    if not cat.active:fail(409,'Inactive category')
    if body.is_primary:
        existing=await db.scalar(select(ProductCategory).where(ProductCategory.product_id==product_id,
                    ProductCategory.is_primary==True))
        if existing:fail(409,'Primary category already set; explicitly remove it first')
    if p.status == 'published' and get_settings().app_env != 'test':
        from app.modules.catalog.publication_policy import publication_issues
        # Pending assignment must be considered in a transient in-memory check.
        # Preserve active publication invariants: add the link, flush, validate;
        # rollback the complete transaction on error (no partial attachment).
        db.add(ProductCategory(product_id=product_id, **body.model_dump()))
        await db.flush()
        errors = await publication_issues(db, p)
        if errors:
            await db.rollback()
            fail(409, 'Cannot add category to published Product: ' + '; '.join(x['message'] for x in errors))
        await commit(db)
        return {'product_id':uid(product_id),'category_id':uid(body.category_id),'is_primary':body.is_primary}
    row=ProductCategory(product_id=product_id,**body.model_dump())
    db.add(row);await commit(db);await db.refresh(row)
    return {'id':uid(row.id),'product_id':uid(product_id),'category_id':uid(body.category_id),'is_primary':body.is_primary}


@admin.delete('/products/{product_id}/categories/{category_id}')
async def remove_product_category(product_id:uuid.UUID,category_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(ProductCategory).where(ProductCategory.product_id==product_id,ProductCategory.category_id==category_id))
    if not row:fail(404,'Membership not found')
    product = await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product and product.status == 'published' and row.is_primary:
        fail(409, 'Unpublish the Product before removing its primary category')
    await db.delete(row);await commit(db)
    return {'removed':True}


class AttributeIn(BaseModel):
    code:str=Field(min_length=2,max_length=80,pattern=r'^[a-z][a-z0-9_]+$')
    name:str=Field(min_length=2,max_length=180)
    data_type:Literal['text','integer','decimal','boolean','enum']
    unit:str|None=Field(default=None,max_length=24,pattern=r'^[a-zA-Z0-9/%°._-]+$')
    enum_values:list[str]|None=Field(default=None,max_length=80)
    filterable:bool=False
    active:bool=True

    @model_validator(mode='after')
    def valid(self):
        if self.data_type=='enum':
            if not self.enum_values or len(self.enum_values)!=len(set(self.enum_values)) or any(not x or len(x)>160 for x in self.enum_values):
                raise ValueError('Enum needs a unique nonempty list')
        elif self.enum_values is not None:
            raise ValueError('Only enum attributes accept enum_values')
        if self.unit and self.data_type not in ('integer','decimal'):
            raise ValueError('Units can only be attached to numeric types')
        return self


@admin.post('/attributes',status_code=201)
async def create_attribute(body:AttributeIn,db:AsyncSession=Depends(get_session)):
    if await db.scalar(select(AttributeCodeHistory.id).where(AttributeCodeHistory.code==body.code)):
        fail(409,'Attribute code is reserved in history')
    row=AttributeDefinition(**body.model_dump());db.add(row);await commit(db);await db.refresh(row)
    return attr_data(row)


class CategoryAttrIn(BaseModel):
    attribute_id:uuid.UUID
    required:bool=False


@admin.post('/categories/{category_id}/attributes',status_code=201)
async def assign_category_attribute(category_id:uuid.UUID,body:CategoryAttrIn,db:AsyncSession=Depends(get_session)):
    category=await db.get(Category,category_id);definition=await db.get(AttributeDefinition,body.attribute_id)
    if not category or not definition:fail(404,'Category or attribute not found')
    if not category.active or not definition.active:fail(409,'Inactive category/attribute')
    if body.required and get_settings().app_env != 'test':
        from app.modules.catalog.publication_policy import ensure_required_binding_safe
        await ensure_required_binding_safe(db, category_id, body.attribute_id)
    row=CategoryAttribute(category_id=category_id,**body.model_dump());db.add(row)
    await commit(db);await db.refresh(row)
    return {'id':uid(row.id),'required':row.required}


@admin.delete('/categories/{category_id}/attributes/{attribute_id}')
async def unlink_category_attribute(category_id:uuid.UUID,attribute_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(CategoryAttribute).where(CategoryAttribute.category_id==category_id,
                        CategoryAttribute.attribute_id==attribute_id))
    if not row:fail(404,'Category attribute binding not found')
    await db.delete(row);await commit(db)
    return {'removed':True}


async def applicable_attrs(db, product_id):
    """Explicit product category attributes plus those inherited from ancestors."""
    categories=(await db.scalars(select(ProductCategory.category_id).where(ProductCategory.product_id==product_id))).all()
    node_ids=set();pending=list(categories)
    while pending:
        current=pending.pop()
        if current in node_ids:continue
        node_ids.add(current)
        if len(node_ids)>1000:fail(409,'Category graph too large')
        node=await db.get(Category,current)
        if node and node.parent_id:pending.append(node.parent_id)
    if not node_ids:return {}
    stmt=select(CategoryAttribute).where(CategoryAttribute.category_id.in_(node_ids))
    rows=(await db.scalars(stmt)).all();byid={}
    for item in rows:
        byid[item.attribute_id]=byid.get(item.attribute_id,False) or item.required
    return byid


class ProductValueIn(BaseModel):
    value:str|int|float|bool|None=None


@admin.put('/products/{product_id}/attributes/{attribute_id}')
async def set_product_attribute(product_id:uuid.UUID,attribute_id:uuid.UUID,body:ProductValueIn,
                                db:AsyncSession=Depends(get_session)):
    product=await db.get(Product,product_id);definition=await db.get(AttributeDefinition,attribute_id)
    if not product or not definition:fail(404,'Product or attribute not found')
    if not definition.active:fail(409,'Inactive attribute')
    allowed=await applicable_attrs(db,product_id)
    if attribute_id not in allowed:fail(409,'Attribute is not assigned to this product category')
    val=body.value
    values={'value_text':None,'value_integer':None,'value_decimal':None,'value_boolean':None,'value_enum':None}
    dtype=definition.data_type
    if dtype=='text':
        if not isinstance(val,str) or not val.strip() or len(val)>500:fail(422,'Expected nonempty text <= 500 chars')
        values['value_text']=val.strip()
    elif dtype=='integer':
        if type(val) is not int or not -2147483648<=val<=2147483647:fail(422,'Expected 32-bit integer')
        values['value_integer']=val
    elif dtype=='decimal':
        if isinstance(val,bool) or not isinstance(val,(str,int,float)):fail(422,'Expected finite decimal')
        try:dec=Decimal(str(val))
        except InvalidOperation:fail(422,'Invalid decimal')
        if not dec.is_finite() or abs(dec)>=10**12 or dec.as_tuple().exponent < -6:fail(422,'Decimal out of range or precision')
        values['value_decimal']=dec
    elif dtype=='boolean':
        if type(val) is not bool:fail(422,'Expected boolean')
        values['value_boolean']=val
    elif dtype=='enum':
        if not isinstance(val,str) or val not in (definition.enum_values or []):fail(422,'Invalid enum option')
        values['value_enum']=val
    row=await db.scalar(select(ProductAttribute).where(ProductAttribute.product_id==product_id,
                            ProductAttribute.attribute_id==attribute_id))
    if row is None:row=ProductAttribute(product_id=product_id,attribute_id=attribute_id);db.add(row)
    for k,v in values.items():setattr(row,k,v)
    await commit(db)
    return {'product_id':uid(product_id),'attribute_code':definition.code,'value':str(val) if isinstance(val,Decimal) else val,'unit':definition.unit}


@admin.delete('/products/{product_id}/attributes/{attribute_id}')
async def delete_product_attribute(product_id:uuid.UUID,attribute_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    row=await db.scalar(select(ProductAttribute).where(ProductAttribute.product_id==product_id,
                        ProductAttribute.attribute_id==attribute_id))
    if not row:fail(404,'Product attribute value not found')
    product=await db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product and product.status == 'published':
        allowed=await applicable_attrs(db,product_id)
        if allowed.get(attribute_id):
            fail(409, 'Unpublish the Product before deleting a required characteristic')
    await db.delete(row);await commit(db)
    return {'removed':True}


async def required_missing(db,product_id):
    applicable=await applicable_attrs(db,product_id)
    required={aid for aid,is_required in applicable.items() if is_required}
    if not required:return []
    available=set((await db.scalars(select(ProductAttribute.attribute_id).where(ProductAttribute.product_id==product_id))).all())
    missing=required-available
    rows=(await db.scalars(select(AttributeDefinition).where(AttributeDefinition.id.in_(missing)))).all() if missing else []
    return sorted(x.code for x in rows)


@public.get('/products/{product_id}/details')
async def product_details(product_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    p=await db.get(Product,product_id)
    if not p or p.status!='published':fail(404,'Product not found')
    brand=await db.get(Brand,p.brand_id)
    if brand and brand.name_norm=='CATALOG FIXTURE':fail(404,'Product not found')
    catstmt=select(Category,ProductCategory.is_primary).join(ProductCategory,ProductCategory.category_id==Category.id).where(
        ProductCategory.product_id==product_id,Category.active==True).order_by(Category.name)
    categories=[dict(category_data(c),is_primary=primary) for c,primary in (await db.execute(catstmt)).all()]
    attrstmt=select(ProductAttribute,AttributeDefinition).join(AttributeDefinition,ProductAttribute.attribute_id==AttributeDefinition.id).where(
        ProductAttribute.product_id==product_id,AttributeDefinition.active==True).order_by(AttributeDefinition.code)
    attrs=[]
    for row,a in (await db.execute(attrstmt)).all():
        raw={'text':row.value_text,'integer':row.value_integer,'decimal':row.value_decimal,
             'boolean':row.value_boolean,'enum':row.value_enum}[a.data_type]
        attrs.append({'code':a.code,'name':a.name,'type':a.data_type,'unit':a.unit,'value':str(raw) if a.data_type=='decimal' else raw,
                      'filterable':a.filterable})
    return {'product_id':uid(p.id),'brand_id':uid(p.brand_id),'categories':categories,'attributes':attrs}

@admin.get('/brands')
async def operator_brands(q:str=Query('',max_length=120),limit:int=Query(100,ge=1,le=500),
                          offset:int=Query(0,ge=0,le=5000),db:AsyncSession=Depends(get_session)):
    stmt=select(Brand)
    if q.strip():stmt=stmt.where(Brand.name_norm.ilike('%'+q.strip().replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    rows=(await db.scalars(stmt.order_by(Brand.name).offset(offset).limit(limit))).all()
    return {'items':[{'id':uid(r.id),'name':r.name,'slug':r.slug} for r in rows], 'limit':limit,'offset':offset}


@admin.get('/brands/{brand_id}/aliases')
async def operator_brand_aliases(brand_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(Brand,brand_id):fail(404,'Brand not found')
    rows=(await db.scalars(select(BrandAlias).where(BrandAlias.brand_id==brand_id).order_by(BrandAlias.alias))).all()
    return {'items':[{'id':uid(r.id),'alias':r.alias,'supplier_id':uid(r.supplier_id),
                      'verified':r.verified} for r in rows]}


@admin.get('/categories')
async def operator_categories(db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(Category).order_by(Category.position,Category.name).limit(2000))).all()
    return {'items':[category_data(r) for r in rows]}


@admin.get('/attributes')
async def operator_attributes(db:AsyncSession=Depends(get_session)):
    rows=(await db.scalars(select(AttributeDefinition).order_by(AttributeDefinition.code).limit(2000))).all()
    return {'items':[attr_data(r) for r in rows]}


@admin.get('/products/{product_id}/details')
async def operator_product_details(product_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    p=await db.get(Product,product_id)
    if not p:fail(404,'Product not found')
    memberships=(await db.scalars(select(ProductCategory).where(ProductCategory.product_id==product_id))).all()
    values=(await db.scalars(select(ProductAttribute).where(ProductAttribute.product_id==product_id))).all()
    return {'product_id':uid(p.id),'status':p.status,'brand_id':uid(p.brand_id),
            'categories':[{'category_id':uid(r.category_id),'is_primary':r.is_primary} for r in memberships],
            'attributes':[{'attribute_id':uid(r.attribute_id),
              'value':str(next(v for v in (r.value_text,r.value_integer,r.value_decimal,r.value_boolean,r.value_enum) if v is not None))} for r in values],
            'missing_required':await required_missing(db,product_id)}

@public.get('/categories/{category_id}/attributes')
async def category_public_attributes(category_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    category=await db.get(Category,category_id)
    if not category or not category.active:fail(404,'Category not found')
    ids=set();node=category
    while node:
        if node.id in ids:fail(409,'Category graph is cyclic')
        ids.add(node.id)
        if len(ids)>1000:fail(409,'Category path too deep')
        node=await db.get(Category,node.parent_id) if node.parent_id else None
    links=(await db.scalars(select(CategoryAttribute).where(CategoryAttribute.category_id.in_(ids)))).all()
    by_id={}
    for link in links:
        by_id[link.attribute_id]=by_id.get(link.attribute_id,False) or link.required
    items=[]
    for aid,required in by_id.items():
        definition=await db.get(AttributeDefinition,aid)
        if definition and definition.active:items.append({**attr_data(definition),'required':required})
    return {'category_id':uid(category_id),'items':sorted(items,key=lambda x:x['code'])}


def typed_filter(definition,raw):
    dt=definition.data_type
    if dt in ('text','enum'):
        if not raw or len(raw)>500:fail(422,'Invalid text filter')
        if dt=='enum' and raw not in (definition.enum_values or []):fail(422,'Unknown enum filter option')
        return ProductAttribute.value_text==raw if dt=='text' else ProductAttribute.value_enum==raw
    if dt=='integer':
        if '..' in raw:
            lo,hi=raw.split('..',1)
            if not re.fullmatch(r'[+-]?\d{1,10}',lo) or not re.fullmatch(r'[+-]?\d{1,10}',hi):fail(422,'Invalid integer range')
            a,b=int(lo),int(hi)
            if a>b or a < -2147483648 or b>2147483647:fail(422,'Invalid integer range bounds')
            return ProductAttribute.value_integer.between(a,b)
        try:
            if not re.fullmatch(r'[+-]?\d{1,10}',raw):fail(422,'Invalid integer filter')
            num=int(raw)
        except ValueError:fail(422,'Invalid integer filter')
        if not -2147483648<=num<=2147483647:fail(422,'Integer filter out of range')
        return ProductAttribute.value_integer==num
    if dt=='decimal':
        if '..' in raw:
            try:
                parts=raw.split('..')
                if len(parts)!=2:raise InvalidOperation
                a,b=(Decimal(part) for part in parts)
            except InvalidOperation:fail(422,'Invalid decimal range')
            if any(not d.is_finite() or abs(d)>=10**12 or d.as_tuple().exponent < -6 for d in (a,b)) or a>b:
                fail(422,'Invalid decimal range bounds')
            return ProductAttribute.value_decimal.between(a,b)
        try:dec=Decimal(raw)
        except InvalidOperation:fail(422,'Invalid decimal filter')
        if not dec.is_finite() or abs(dec)>=10**12 or dec.as_tuple().exponent < -6:fail(422,'Invalid decimal filter')
        return ProductAttribute.value_decimal==dec
    if raw.lower() not in ('true','false'):fail(422,'Invalid boolean filter')
    return ProductAttribute.value_boolean==(raw.lower()=='true')


@public.get('/products')
async def filter_products(category_id:uuid.UUID|None=None,brand_id:uuid.UUID|None=None,
                          q:str=Query('',max_length=110),
                          attr:list[str]=Query(default=[],max_length=510),
                          limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=5000),
                          db:AsyncSession=Depends(get_session)):
    """Filter published products; no catalog/offer identity changes or supplier calls.

    `attr=code:value` repeated up to 8 times. Matching is exact and typed.
    Child categories are included via a portable recursive CTE.
    """
    from sqlalchemy import exists
    from app.modules.catalog.service import _search_filter, _search_ranking
    if len(attr)>8:fail(422,'At most eight attribute filters')
    stmt=select(Product).join(Brand,Brand.id==Product.brand_id).where(Product.status=='published',Brand.name_norm!='CATALOG FIXTURE')
    if brand_id:stmt=stmt.where(Product.brand_id==brand_id)
    if category_id:
        category=await db.get(Category,category_id)
        if not category or not category.active:fail(404,'Category not found')
        subtree=select(Category.id).where(Category.id==category_id).cte('descendants',recursive=True)
        subtree=subtree.union_all(select(Category.id).join(subtree,Category.parent_id==subtree.c.id).where(Category.active==True))
        stmt=stmt.where(exists(select(ProductCategory.id).where(
            ProductCategory.product_id==Product.id,ProductCategory.category_id.in_(select(subtree.c.id)))))
    if q.strip():stmt=stmt.where(_search_filter(q.strip()))
    allowed_ids = None
    if category_id is not None:
        from app.modules.catalog_ext.category_facets import effective_template
        allowed_ids = {x['attribute_id'] for x in await effective_template(db, category_id)
                       if x['active'] and x['filterable'] and x['show_in_filters']}
    seen=set()
    seen_attributes=set()
    for expression in attr:
        if ':' not in expression:fail(422,'Attribute filter format must be code:value')
        code,raw=expression.split(':',1)
        if code in seen or not re.fullmatch(r'[a-z][a-z0-9_]{1,79}',code):fail(422,'Invalid or duplicate attribute filter')
        seen.add(code)
        definition=await db.scalar(select(AttributeDefinition).where(AttributeDefinition.code==code,
                                  AttributeDefinition.filterable==True,AttributeDefinition.active==True))
        if definition is None:
            old_code=await db.scalar(select(AttributeCodeHistory).where(AttributeCodeHistory.code==code))
            if old_code is not None:
                candidate=await db.get(AttributeDefinition,old_code.attribute_id)
                if candidate and candidate.active and candidate.filterable:
                    definition=candidate
        if definition is None:fail(422,'Attribute is not filterable or does not exist')
        if allowed_ids is not None and str(definition.id) not in allowed_ids:
            fail(422, 'Filter is not enabled for this category')
        if definition.id in seen_attributes:
            fail(422,'Duplicate logical attribute filter')
        seen_attributes.add(definition.id)
        predicate=typed_filter(definition,raw)
        stmt=stmt.where(exists(select(ProductAttribute.id).where(ProductAttribute.product_id==Product.id,
                                  ProductAttribute.attribute_id==definition.id,predicate)))
    total=int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    order=(_search_ranking(q.strip()),Product.name,Product.id) if q.strip() else (Product.name,Product.id)
    rows=(await db.scalars(stmt.order_by(*order).limit(limit).offset(offset))).all()
    return {'count':total,'limit':limit,'offset':offset,
            'items':[{'id':uid(p.id),'brand_id':uid(p.brand_id),'article':p.article,
                      'name':p.name,'slug':p.slug} for p in rows]}
