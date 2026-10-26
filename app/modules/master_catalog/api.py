"""v0.18: operator-controlled product relationships; published read is read-only.

No supplier HTTP, order, payment, inventory movement or media scraping.
"""
import json
import uuid
from decimal import Decimal
from math import lcm
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.catalog.models import Product
from app.modules.marketplace.models import ProductRelation, SellerOffer, SupplierAccount, SupplierDestination
from app.modules.suppliers.models import SupplierItem
from .models import (ProductFamily, FamilyMember, ProductComposition, ComponentLine,
                     FactoryPackage, OfferSaleTerms)

admin = APIRouter(prefix='/api/v1/backoffice/master-catalog',tags=['master-catalog-admin'],
                  dependencies=[Depends(require_admin)])
public = APIRouter(prefix='/api/v1/master-catalog',tags=['master-catalog'])


def stop(code, message):
    raise HTTPException(code, message)


async def save(db,obj):
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        stop(409,'Catalog identity or constraint conflict')
    return obj


async def product(db,pid,*,published=False):
    row=await db.get(Product,pid)
    if not row or (published and row.status!='published'):
        stop(404,'Product not found')
    return row


async def lock_products(db,*ids):
    """Serialize conflicting graph edits on PostgreSQL using deterministic lock order."""
    for pid in sorted(set(ids),key=str):
        await db.scalar(select(Product.id).where(Product.id==pid).with_for_update())


class NewFamily(BaseModel):
    name: str=Field(min_length=2,max_length=220)
    slug: str=Field(min_length=2,max_length=220,pattern=r'^[a-z0-9][a-z0-9-]+$')
    kind: Literal['variant','kit_line','related_line']='variant'
    axes: list[str]=Field(min_length=1,max_length=8)

    @model_validator(mode='after')
    def validate_axes(self):
        import re
        if len(set(self.axes))!=len(self.axes) or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',x) for x in self.axes):
            raise ValueError('Family axes must be unique lower-case machine keys')
        return self


class NewMember(BaseModel):
    product_id:uuid.UUID
    axis_values:dict[str,str|int|bool]=Field(min_length=1,max_length=8)


def axis_key(axis_values):
    if any(isinstance(v,str) and (not v.strip() or len(v)>100) for v in axis_values.values()):
        stop(422,'Invalid axis value')
    key=json.dumps(axis_values,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if len(key)>480:stop(422,'Variant key is too long')
    return key


@admin.post('/families',status_code=201)
async def new_family(body:NewFamily,db:AsyncSession=Depends(get_session)):
    fam=await save(db,ProductFamily(**body.model_dump()))
    return {'id':str(fam.id),'slug':fam.slug,'axes':fam.axes}


@admin.post('/families/{fid}/members',status_code=201)
async def new_member(fid:uuid.UUID,body:NewMember,db:AsyncSession=Depends(get_session)):
    fam=await db.get(ProductFamily,fid)
    if not fam:stop(404,'Family not found')
    await product(db,body.product_id)
    if set(body.axis_values)!=set(fam.axes):stop(422,'Variant must specify exactly the axes defined by its family')
    await lock_products(db,body.product_id)
    member=await save(db,FamilyMember(family_id=fid,product_id=body.product_id,
                                     axis_values=body.axis_values,variant_key=axis_key(body.axis_values)))
    return {'id':str(member.id),'product_id':str(member.product_id),'axis_values':member.axis_values}


@admin.delete('/families/{fid}/members/{pid}')
async def remove_member(fid:uuid.UUID,pid:uuid.UUID,db:AsyncSession=Depends(get_session)):
    member=await db.scalar(select(FamilyMember).where(FamilyMember.family_id==fid,FamilyMember.product_id==pid))
    if not member:stop(404,'Membership not found')
    await db.delete(member);await db.commit()
    return {'removed':True}


class NewComposition(BaseModel):
    kind:Literal['factory_kit','assembled_plan']
    verified:bool=False


@admin.put('/products/{pid}/composition')
async def set_composition(pid:uuid.UUID,body:NewComposition,db:AsyncSession=Depends(get_session)):
    p=await product(db,pid)
    # Assemble-on-demand is a blueprint, never a published sellable product until
    # a later release implements atomic component reservations.
    if body.kind=='assembled_plan' and p.status=='published':
        stop(409,'Assembled plans must remain draft; v0.18 cannot reserve their components')
    entry=await db.get(ProductComposition,pid)
    if entry and entry.kind!=body.kind:stop(409,'Composition kind cannot change in place')
    if not entry:entry=ProductComposition(product_id=pid,kind=body.kind)
    entry.verified=body.verified
    await save(db,entry)
    return {'product_id':str(pid),'kind':entry.kind,'verified':entry.verified,
            'inventory_mode':'finished_product_stock' if entry.kind=='factory_kit' else 'not_sellable_blueprint'}


class NewComponent(BaseModel):
    position:int=Field(ge=1,le=10000)
    component_product_id:uuid.UUID|None=None
    name:str=Field(min_length=2,max_length=220)
    external_part_number:str|None=Field(default=None,max_length=140)
    quantity:Decimal=Field(gt=0,le=100000,max_digits=12,decimal_places=3)
    unit:Literal['pcs','kg','l','m']='pcs'
    sale_status:Literal['unknown','not_listed','independent']='unknown'

    @model_validator(mode='after')
    def validate_unit(self):
        if self.unit=='pcs' and self.quantity!=self.quantity.to_integral_value():
            raise ValueError('Piece quantities must be whole numbers')
        if self.sale_status=='independent' and self.component_product_id is None:
            raise ValueError('A separately sellable component needs a linked catalog product')
        return self


async def would_make_cycle(db,edge_table,from_id,to_id,from_attr,to_attr):
    """Reject all directed cycles, including transitive nesting."""
    # Forward links are OUTER->INNER. A cycle occurs if INNER reaches OUTER.
    pending=[to_id];visited=set()
    while pending:
        current=pending.pop()
        if current==from_id:return True
        if current in visited:continue
        visited.add(current)
        if len(visited)>2000:stop(409,'Dependency graph exceeds verification limit')
        stmt=select(getattr(edge_table,to_attr)).where(getattr(edge_table,from_attr)==current)
        pending.extend((await db.scalars(stmt)).all())
    return False


@admin.post('/products/{pid}/components',status_code=201)
async def new_component(pid:uuid.UUID,body:NewComponent,db:AsyncSession=Depends(get_session)):
    comp=await db.get(ProductComposition,pid)
    if not comp:stop(409,'Configure product composition first')
    if body.component_product_id:
        await product(db,body.component_product_id)
        if body.component_product_id==pid:stop(409,'A product cannot contain itself')
        await lock_products(db,pid,body.component_product_id)
        if await would_make_cycle(db,ComponentLine,pid,body.component_product_id,
                                  'parent_product_id','component_product_id'):
            stop(409,'Nested component cycle rejected')
    line=await save(db,ComponentLine(parent_product_id=pid,**body.model_dump()))
    return {'id':str(line.id),'parent_product_id':str(pid),'component_product_id':str(line.component_product_id) if line.component_product_id else None}


@admin.delete('/products/{pid}/components/{line_id}')
async def remove_component(pid:uuid.UUID,line_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    line=await db.get(ComponentLine,line_id)
    if not line or line.parent_product_id!=pid:stop(404,'Component line not found')
    await db.delete(line);await db.commit()
    return {'removed':True}


class NewPackage(BaseModel):
    outer_product_id:uuid.UUID
    inner_product_id:uuid.UUID
    units_per_package:int=Field(ge=2,le=100000)
    kind:Literal['retail_pack','box','case','pallet']='retail_pack'
    verified:bool=False


@admin.post('/packages',status_code=201)
async def new_package(body:NewPackage,db:AsyncSession=Depends(get_session)):
    await product(db,body.outer_product_id);await product(db,body.inner_product_id)
    if body.outer_product_id==body.inner_product_id:stop(409,'Package and contained item need separate Product UUIDs')
    await lock_products(db,body.outer_product_id,body.inner_product_id)
    if await would_make_cycle(db,FactoryPackage,body.outer_product_id,body.inner_product_id,
                              'outer_product_id','inner_product_id'):
        stop(409,'Packaging cycle rejected')
    obj=await save(db,FactoryPackage(**body.model_dump()))
    return {'id':str(obj.id),'outer_product_id':str(obj.outer_product_id),
            'inner_product_id':str(obj.inner_product_id),'units_per_package':obj.units_per_package}


@admin.delete('/packages/{package_id}')
async def remove_package(package_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    obj=await db.get(FactoryPackage,package_id)
    if not obj:stop(404,'Factory package not found')
    await db.delete(obj);await db.commit()
    return {'removed':True}


class NewRelation(BaseModel):
    product_id:uuid.UUID
    related_product_id:uuid.UUID
    relation:Literal['related','alternative','supersedes','accessory','paired','equivalent']
    verified:bool=False


@admin.post('/relations',status_code=201)
async def new_relation(body:NewRelation,db:AsyncSession=Depends(get_session)):
    await product(db,body.product_id);await product(db,body.related_product_id)
    if body.product_id==body.related_product_id:stop(409,'Relation must connect distinct products')
    obj=await save(db,ProductRelation(**body.model_dump()))
    return {'id':str(obj.id),'product_id':str(obj.product_id),'related_product_id':str(obj.related_product_id),'relation':obj.relation}


class VerifyLink(BaseModel):
    verified:bool


@admin.patch('/relations/{relation_id}')
async def verify_relation(relation_id:uuid.UUID,body:VerifyLink,db:AsyncSession=Depends(get_session)):
    obj=await db.get(ProductRelation,relation_id)
    if not obj:stop(404,'Relation not found')
    obj.verified=body.verified
    await save(db,obj)
    return {'id':str(obj.id),'verified':obj.verified}


@admin.delete('/relations/{relation_id}')
async def remove_relation(relation_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    obj=await db.get(ProductRelation,relation_id)
    if not obj:stop(404,'Relation not found')
    await db.delete(obj);await db.commit()
    return {'removed':True}


@admin.patch('/packages/{package_id}')
async def verify_package(package_id:uuid.UUID,body:VerifyLink,db:AsyncSession=Depends(get_session)):
    obj=await db.get(FactoryPackage,package_id)
    if not obj:stop(404,'Package not found')
    obj.verified=body.verified
    await save(db,obj)
    return {'id':str(obj.id),'verified':obj.verified}


class NewSaleTerms(BaseModel):
    minimum_units:int=Field(default=1,ge=1,le=100000)
    step_units:int=Field(default=1,ge=1,le=100000)
    units_per_package:int=Field(default=1,ge=1,le=100000)
    order_unit:Literal['piece','pack']='piece'
    status:Literal['draft','active']='draft'

    @model_validator(mode='after')
    def unit_rules(self):
        if self.step_units % self.units_per_package:
            raise ValueError('Step must be a multiple of units in each sale package')
        if self.order_unit=='pack' and self.units_per_package==1:
            raise ValueError('Pack sale requires more than 1 unit per package')
        if self.order_unit=='pack' and self.status=='active':
            raise ValueError('Pack-priced offers are draft-only until explicit price-unit conversion is implemented; use a separate factory-package Product')
        return self


def render_terms(row):
    return {'id':str(row.id),'scope_key':row.scope_key,'status':row.status,
            'minimum_units':row.minimum_units,'step_units':row.step_units,
            'units_per_package':row.units_per_package,'order_unit':row.order_unit,
            'quantity_is_base_units':True}


async def upsert_terms(db,scope,ids,body):
    row=await db.scalar(select(OfferSaleTerms).where(OfferSaleTerms.scope_key==scope).with_for_update())
    if row is None:
        row=OfferSaleTerms(scope_key=scope,**ids)
    for key,value in body.model_dump().items():setattr(row,key,value)
    return render_terms(await save(db,row))


@admin.put('/sale-terms/seller/{offer_id}')
async def seller_terms(offer_id:uuid.UUID,body:NewSaleTerms,db:AsyncSession=Depends(get_session)):
    if not await db.get(SellerOffer,offer_id):stop(404,'Seller offer not found')
    return await upsert_terms(db,'seller:'+str(offer_id),{'seller_offer_id':offer_id},body)


class SupplierSaleScope(NewSaleTerms):
    supplier_item_id:uuid.UUID
    supplier_account_id:uuid.UUID
    supplier_destination_id:uuid.UUID
    warehouse_id:str=Field(min_length=1,max_length=140)


@admin.put('/sale-terms/supplier')
async def supplier_terms(body:SupplierSaleScope,db:AsyncSession=Depends(get_session)):
    item=await db.get(SupplierItem,body.supplier_item_id)
    account=await db.get(SupplierAccount,body.supplier_account_id)
    destination=await db.get(SupplierDestination,body.supplier_destination_id)
    if not item or not account or not destination:stop(404,'Supplier item/account/destination not found')
    if item.supplier_id!=account.supplier_id or destination.account_id!=account.id:
        stop(409,'Supplier item/account/destination mismatch')
    from app.modules.suppliers.models import OfferSnapshot
    from app.modules.offer_engine.models import OfferDetail
    exists=await db.scalar(select(OfferDetail.snapshot_id).join(OfferSnapshot,OfferSnapshot.id==OfferDetail.snapshot_id)
        .where(OfferSnapshot.supplier_item_id==item.id,
               OfferSnapshot.supplier_account_id==account.id,
               OfferSnapshot.supplier_destination_id==destination.id,
               OfferDetail.warehouse==body.warehouse_id).limit(1))
    if exists is None:stop(409,'Warehouse is not known to this supplier identity')
    scope='supplier:'+':'.join(map(str,(item.id,account.id,destination.id,body.warehouse_id)))
    if len(scope)>260:stop(422,'Supplier warehouse scope key is too long')
    vals=body.model_dump(exclude={'supplier_item_id','supplier_account_id','supplier_destination_id','warehouse_id'})
    return await upsert_terms(db,scope,{'supplier_item_id':item.id,'supplier_account_id':account.id,
                                       'supplier_destination_id':destination.id,'warehouse_id':body.warehouse_id},NewSaleTerms(**vals))


async def _visible(db,pid):
    return bool(await db.scalar(select(Product.id).where(Product.id==pid,Product.status=='published')))


async def catalog_links(db,pid,*,admin_view=False):
    await product(db,pid,published=not admin_view)
    member=await db.scalar(select(FamilyMember).where(FamilyMember.product_id==pid))
    family=None
    if member:
        f=await db.get(ProductFamily,member.family_id)
        ms=(await db.scalars(select(FamilyMember).where(FamilyMember.family_id==f.id).order_by(FamilyMember.variant_key))).all()
        items=[]
        for m in ms:
            p=await db.get(Product,m.product_id)
            if p and (admin_view or p.status=='published'):
                items.append({'product_id':str(p.id),'slug':p.slug,'name':p.name,'article':p.article,
                              'axis_values':m.axis_values,'current':p.id==pid})
        family={'id':str(f.id),'name':f.name,'slug':f.slug,'kind':f.kind,'axes':f.axes,'members':items}
    c=await db.get(ProductComposition,pid)
    composition=None
    if c and (admin_view or (c.verified and c.kind=='factory_kit')):
        lines=(await db.scalars(select(ComponentLine).where(ComponentLine.parent_product_id==pid).order_by(ComponentLine.position))).all()
        parts=[]
        for l in lines:
            linked=l.component_product_id and (admin_view or await _visible(db,l.component_product_id))
            parts.append({'id':str(l.id),'position':l.position,'name':l.name,'external_part_number':l.external_part_number,
                          'quantity':str(l.quantity),'unit':l.unit,
                          'component_product_id':str(l.component_product_id) if linked else None,
                          'sale_status':l.sale_status if linked or admin_view or l.sale_status=='not_listed' else 'unknown'})
        composition={'kind':c.kind,'verified':c.verified,'inventory_mode':'finished_product_stock' if c.kind=='factory_kit' else 'not_sellable_blueprint','parts':parts}
    ps=(await db.scalars(select(FactoryPackage).where((FactoryPackage.outer_product_id==pid)|(FactoryPackage.inner_product_id==pid)))).all()
    packs=[]
    for pk in ps:
        other=pk.inner_product_id if pk.outer_product_id==pid else pk.outer_product_id
        if (admin_view or pk.verified) and (admin_view or await _visible(db,other)):
            packs.append({'id':str(pk.id),'outer_product_id':str(pk.outer_product_id),'inner_product_id':str(pk.inner_product_id),
                          'units_per_package':pk.units_per_package,'kind':pk.kind,'verified':pk.verified,
                          'role':'outer' if pk.outer_product_id==pid else 'inner'})
    rs=(await db.scalars(select(ProductRelation).where(ProductRelation.product_id==pid))).all()
    relations=[]
    for relation in rs:
        if (admin_view or relation.verified) and (admin_view or await _visible(db,relation.related_product_id)):
            rp=await db.get(Product,relation.related_product_id)
            if rp:
                relations.append({'id':str(relation.id),'related_product_id':str(rp.id),'slug':rp.slug,
                                  'name':rp.name,'relation':relation.relation,'verified':relation.verified})
    return {'product_id':str(pid),'family':family,'composition':composition,
            'factory_packages':packs,'relations':relations,
            'note':'Navigation only; quantities, offers, warehouses and prices are separate.'}


@public.get('/products/{pid}/links')
async def public_links(pid:uuid.UUID,db:AsyncSession=Depends(get_session)):
    return await catalog_links(db,pid)


@admin.get('/products/{pid}/links')
async def admin_links(pid:uuid.UUID,db:AsyncSession=Depends(get_session)):
    return await catalog_links(db,pid,admin_view=True)
