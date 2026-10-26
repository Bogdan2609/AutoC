"""Marketplace public read APIs, tenant-isolated seller writes, admin provisioning.

No payment or actual supplier-order endpoints are enabled by this component.
"""
import re
import uuid
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.accounts.api import current_user
from app.modules.accounts.models import User
from app.modules.catalog.models import Product, Brand
from app.modules.catalog.normalize import normalize_article
from app.modules.catalog_ext.models import Category as CanonicalCategory, ProductCategory as CanonicalProductCategory
from app.modules.commerce.models import CartLine
from app.modules.marketplace.models import (
    Merchant, Store, StoreMembership, FulfillmentNode, DeliveryRoute, SupplierAccount,
    SupplierDestination, SellerOffer, UsedPart, InventoryBalance, InventoryMovement,
    ProductIdentifier, ProductRelation,
)
from app.modules.marketplace.service import assert_store_access, store_node, offer_check, options_for_offer
from app.modules.marketplace.receiving_contact import (validate_receiving_contact, normalize_receiving_comment, ReceivingContactError)
from app.modules.suppliers.models import Supplier, SupplierItem
from app.modules.offer_engine.cache import invalidate

admin = APIRouter(prefix='/api/v1/admin/marketplace', tags=['marketplace-admin'],
                  dependencies=[Depends(require_admin)])
seller = APIRouter(prefix='/api/v1/seller', tags=['seller'])
public = APIRouter(prefix='/api/v1/marketplace', tags=['marketplace'])
SLUG = r'^[a-z0-9][a-z0-9-]{1,98}$'
PROFILE = r'^(default|[A-Z][A-Z0-9_]{1,39})$'


async def save(db, obj):
    db.add(obj)
    try:
        await db.commit()
        await db.refresh(obj)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, 'Conflicting or invalid marketplace data') from exc
    return obj


class NewMerchant(BaseModel):
    name: str = Field(min_length=2,max_length=180)
    slug: str = Field(pattern=SLUG)
    is_platform_owner: bool = False


@admin.post('/merchants',status_code=201)
async def merchant_create(body:NewMerchant,db:AsyncSession=Depends(get_session)):
    if body.is_platform_owner and await db.scalar(select(Merchant).where(Merchant.is_platform_owner.is_(True))):
        raise HTTPException(409,'Platform merchant already exists')
    obj=await save(db,Merchant(**body.model_dump(),status='active'))
    return {'id':str(obj.id),'slug':obj.slug}


class NewStore(BaseModel):
    merchant_id: uuid.UUID
    name: str = Field(min_length=2,max_length=160)
    slug: str = Field(pattern=SLUG)


@admin.post('/stores',status_code=201)
async def store_create(body:NewStore,db:AsyncSession=Depends(get_session)):
    if not await db.get(Merchant,body.merchant_id):raise HTTPException(404,'Merchant not found')
    obj=await save(db,Store(**body.model_dump()))
    return {'id':str(obj.id)}


class NewMember(BaseModel):
    store_id: uuid.UUID
    user_id: uuid.UUID
    role: Literal['owner','manager','inventory','viewer'] = 'viewer'


@admin.post('/memberships',status_code=201)
async def membership_create(body:NewMember,db:AsyncSession=Depends(get_session)):
    if not await db.get(Store,body.store_id) or not await db.get(User,body.user_id):
        raise HTTPException(404,'Store or user not found')
    obj=await save(db,StoreMembership(**body.model_dump()))
    return {'id':str(obj.id)}


class NewNode(BaseModel):
    merchant_id: uuid.UUID
    store_id: uuid.UUID | None = None
    code: str = Field(min_length=2,max_length=60)
    name: str = Field(min_length=2,max_length=180)
    kind: Literal['warehouse','pickup','warehouse_pickup']
    city: str = Field(min_length=2,max_length=120)
    address: str = Field(min_length=4,max_length=320)
    receiving_contact_name: str | None = Field(default=None, max_length=160)
    receiving_contact_phone: str | None = Field(default=None, max_length=40)
    receiving_contact_comment: str = Field(default='', max_length=500)
    timezone_name: str = Field(default='Europe/Moscow',max_length=64)


@admin.post('/nodes',status_code=201)
async def node_create(body:NewNode,db:AsyncSession=Depends(get_session)):
    if not await db.get(Merchant,body.merchant_id):raise HTTPException(404,'Merchant not found')
    if body.store_id:
        store=await db.get(Store,body.store_id)
        if not store or store.merchant_id!=body.merchant_id:raise HTTPException(409,'Store is owned by a different merchant')
    from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
    try:ZoneInfo(body.timezone_name)
    except ZoneInfoNotFoundError as exc:raise HTTPException(422,'Unknown timezone') from exc
    data=body.model_dump()
    if (data['receiving_contact_name'] is None) != (data['receiving_contact_phone'] is None):
        raise HTTPException(422, 'Both receiving contact name and phone are required together')
    if data['receiving_contact_name'] is not None:
        try:
            contact=validate_receiving_contact(data['receiving_contact_name'],data['receiving_contact_phone'], data['receiving_contact_comment'])
        except ReceivingContactError as exc:
            raise HTTPException(422, str(exc)) from None
        data.update(receiving_contact_name=contact.name, receiving_contact_phone=contact.phone, receiving_contact_comment=contact.comment)
    else:
        try:
            data['receiving_contact_comment'] = normalize_receiving_comment(data['receiving_contact_comment'])
        except ReceivingContactError as exc:
            raise HTTPException(422, str(exc)) from None
    obj=await save(db,FulfillmentNode(**data))
    return {'id':str(obj.id)}


class EditNode(BaseModel):
    receiving_contact_name: str | None = Field(default=None, max_length=160)
    receiving_contact_phone: str | None = Field(default=None, max_length=40)
    receiving_contact_comment: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None,min_length=2,max_length=180)
    city: str | None = Field(default=None,min_length=2,max_length=120)
    address: str | None = Field(default=None,min_length=4,max_length=320)
    timezone_name: str | None = Field(default=None,max_length=64)
    active: bool | None = None


@admin.patch('/nodes/{node_id}')
async def node_edit(node_id:uuid.UUID,body:EditNode,db:AsyncSession=Depends(get_session)):
    node=await db.get(FulfillmentNode,node_id)
    if not node:raise HTTPException(404,'Node not found')
    if not body.model_fields_set:raise HTTPException(422,'No fields provided')
    if any(getattr(body,k) is None for k in body.model_fields_set):
        raise HTTPException(422,'Updated node fields cannot be null')
    contacts={'receiving_contact_name','receiving_contact_phone'}
    if contacts & body.model_fields_set:
        if not contacts <= body.model_fields_set:
            raise HTTPException(422, 'Update receiving name and phone together')
        try:
            comment = body.receiving_contact_comment if 'receiving_contact_comment' in body.model_fields_set else node.receiving_contact_comment
            contact=validate_receiving_contact(body.receiving_contact_name,body.receiving_contact_phone,comment)
        except ReceivingContactError as exc:
            raise HTTPException(422, str(exc)) from None
        body.receiving_contact_name=contact.name
        body.receiving_contact_phone=contact.phone
        if 'receiving_contact_comment' in body.model_fields_set:
            body.receiving_contact_comment=contact.comment
    elif 'receiving_contact_comment' in body.model_fields_set:
        try:
            body.receiving_contact_comment=normalize_receiving_comment(body.receiving_contact_comment)
        except ReceivingContactError as exc:
            raise HTTPException(422, str(exc)) from None
    if body.timezone_name is not None:
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        try:ZoneInfo(body.timezone_name)
        except ZoneInfoNotFoundError as exc:raise HTTPException(422,'Unknown timezone') from exc
    invalidates=any(k in body.model_fields_set and getattr(node,k)!=getattr(body,k)
                    for k in ('city','address','timezone_name'))
    if body.active is False and node.active:
        invalidates=True
    for field in body.model_fields_set:
        setattr(node,field,getattr(body,field))
    revoked=0
    if invalidates:
        mappings=(await db.scalars(select(SupplierDestination).where(
            SupplierDestination.node_id==node.id))).all()
        for mapping in mappings:
            if mapping.verified or mapping.enabled:revoked+=1
            mapping.verified=False;mapping.enabled=False
    await db.commit()
    return {'id':str(node.id),'active':node.active,
            'destinations_requiring_reverification':revoked,
            'receiving_contact_configured':bool(node.receiving_contact_name and node.receiving_contact_phone),
            'note':'Receiving contact is stored on the warehouse/PVZ and used for supplier couriers; destination approvals are revoked only when address/city/timezone changes or the node is disabled'}


@admin.get('/nodes/{node_id}/receiving-contact')
async def node_receiving_contact(node_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    """Admin-only receiving site contact; never expose supplier contacts publicly."""
    node=await db.get(FulfillmentNode,node_id)
    if node is None: raise HTTPException(404, 'Receiving node not found')
    return {'node_id':str(node.id),
            'receiving_contact_name':node.receiving_contact_name,
            'receiving_contact_phone':node.receiving_contact_phone,
            'receiving_contact_comment':node.receiving_contact_comment or '',
            'configured':bool(node.receiving_contact_name and node.receiving_contact_phone)}


class NewRoute(BaseModel):
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    transit_days: int = Field(ge=0,le=90)
    handling_days: int = Field(default=0,ge=0,le=30)
    fee: Decimal = Field(default=Decimal('0.00'),ge=0,max_digits=14,decimal_places=2)


@admin.post('/routes',status_code=201)
async def route_create(body:NewRoute,db:AsyncSession=Depends(get_session)):
    a=await db.get(FulfillmentNode,body.source_node_id)
    b=await db.get(FulfillmentNode,body.target_node_id)
    if not a or not b or a.merchant_id!=b.merchant_id or a.id==b.id :
        raise HTTPException(409,'Only internal routes within one merchant are allowed')
    obj=await save(db,DeliveryRoute(**body.model_dump()))
    return {'id':str(obj.id)}


class EditRoute(BaseModel):
    transit_days: int | None = Field(default=None,ge=0,le=90)
    handling_days: int | None = Field(default=None,ge=0,le=30)
    fee: Decimal | None = Field(default=None,ge=0,max_digits=14,decimal_places=2)
    active: bool | None = None


@admin.patch('/routes/{route_id}')
async def route_edit(route_id:uuid.UUID,body:EditRoute,db:AsyncSession=Depends(get_session)):
    route=await db.get(DeliveryRoute,route_id)
    if not route:raise HTTPException(404,'Route not found')
    if not body.model_fields_set:raise HTTPException(422,'No fields provided')
    if any(getattr(body,k) is None for k in body.model_fields_set):
        raise HTTPException(422,'Updated route fields cannot be null')
    for field in body.model_fields_set:
        setattr(route,field,getattr(body,field))
    if route.active:
        a=await db.get(FulfillmentNode,route.source_node_id)
        b=await db.get(FulfillmentNode,route.target_node_id)
        if not a or not b or not a.active or not b.active or a.merchant_id!=b.merchant_id:
            raise HTTPException(409,'Active route must connect active nodes of one merchant')
    await db.commit()
    return {'id':str(route.id),'active':route.active,'transit_days':route.transit_days,
            'handling_days':route.handling_days,'fee':str(route.fee)}


class NewAccount(BaseModel):
    supplier_id: uuid.UUID
    merchant_id: uuid.UUID
    label: str = Field(min_length=2,max_length=120)
    credential_profile: str = Field(pattern=PROFILE)
    requests_per_hour: int = Field(default=30,ge=1,le=1000)
    timeout_seconds: int = Field(default=20,ge=2,le=60)


@admin.post('/supplier-accounts',status_code=201)
async def supplier_account_create(body:NewAccount,db:AsyncSession=Depends(get_session)):
    if not await db.get(Supplier,body.supplier_id) or not await db.get(Merchant,body.merchant_id):
        raise HTTPException(404,'Supplier or merchant not found')
    obj=await save(db,SupplierAccount(**body.model_dump()))
    return {'id':str(obj.id),'enabled':False,'credentials_stored':False}


class AccountEnable(BaseModel):
    enabled: bool
    read_enabled: bool
    label: str | None = Field(default=None,min_length=2,max_length=120)
    credential_profile: str | None = Field(default=None,pattern=PROFILE)
    requests_per_hour: int | None = Field(default=None,ge=1,le=1000)
    timeout_seconds: int | None = Field(default=None,ge=2,le=60)


@admin.patch('/supplier-accounts/{account_id}')
async def account_enable(account_id:uuid.UUID,body:AccountEnable,db:AsyncSession=Depends(get_session)):
    obj=await db.get(SupplierAccount,account_id)
    if not obj:raise HTTPException(404,'Account not found')
    profile_changed = body.credential_profile is not None and body.credential_profile != obj.credential_profile
    if profile_changed and obj.procurement_enabled:
        raise HTTPException(409, 'Disable real supplier purchases before changing credentials profile')
    if body.label is not None: obj.label=body.label
    if profile_changed: obj.credential_profile=body.credential_profile
    if body.requests_per_hour is not None: obj.requests_per_hour=body.requests_per_hour
    if body.timeout_seconds is not None: obj.timeout_seconds=body.timeout_seconds
    # Switching profile can point to different secrets. Require explicit re-enablement.
    obj.enabled=False if profile_changed else body.enabled
    obj.read_enabled=False if profile_changed else body.read_enabled
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Conflicting supplier account label or credential profile') from exc
    return {'id':str(obj.id),'label':obj.label,'credential_profile':obj.credential_profile,
            'requests_per_hour':obj.requests_per_hour,'timeout_seconds':obj.timeout_seconds,
            'enabled':obj.enabled,'read_enabled':obj.read_enabled,
            'requires_reenable':profile_changed,
            'note':'Changing credential profile disables the account until reviewed; API keys are not stored here'}


class AccountProcurementPermission(BaseModel):
    enabled: bool
    confirmation: str


@admin.patch('/supplier-accounts/{account_id}/procurement-permission')
async def account_procurement_permission(account_id:uuid.UUID, body:AccountProcurementPermission,
                                         db:AsyncSession=Depends(get_session)):
    obj=await db.get(SupplierAccount, account_id)
    if not obj:raise HTTPException(404,'Account not found')
    if body.enabled and body.confirmation!='I_ACCEPT_REAL_SUPPLIER_PURCHASES':
        raise HTTPException(409,'Explicit real-purchase confirmation required')
    # Only the verified ROSSKO checkout transport is supplied in this build.
    supplier=await db.get(Supplier,obj.supplier_id)
    if body.enabled and (not supplier or supplier.code!='rossko'):
        raise HTTPException(409,'Supplier checkout adapter has not been contract-tested')
    if body.enabled:
        from app.modules.marketplace.supplier_delivery_policy import require_rossko_courier
        targets=(await db.scalars(select(SupplierDestination).where(
            SupplierDestination.account_id==obj.id,
            SupplierDestination.enabled.is_(True),
            SupplierDestination.verified.is_(True)))).all()
        def courier_ok(d):
            try:
                require_rossko_courier(d.external_delivery_id, d.external_address_id)
            except ValueError:
                return False
            return True
        if not any(courier_ok(d) for d in targets):
            raise HTTPException(409, 'A verified courier address is required before ROSSKO purchasing can be enabled')
    obj.procurement_enabled=body.enabled
    await db.commit()
    return {'id':str(obj.id),'procurement_enabled':obj.procurement_enabled}


class NewDestination(BaseModel):
    account_id: uuid.UUID
    node_id: uuid.UUID
    external_address_id: str | None = Field(default=None,max_length=100)
    external_delivery_id: str | None = Field(default=None,max_length=100)
    external_requisite_id: str | None = Field(default=None,pattern=r'^[0-9]{1,18}$')


@admin.post('/supplier-destinations',status_code=201)
async def destination_create(body:NewDestination,db:AsyncSession=Depends(get_session)):
    a=await db.get(SupplierAccount,body.account_id)
    n=await db.get(FulfillmentNode,body.node_id)
    if not a or not n or n.merchant_id != a.merchant_id:
        raise HTTPException(409,'Destination must belong to the supplier account merchant')
    # Not verified until a real contract/API address check.
    obj=await save(db,SupplierDestination(**body.model_dump(),enabled=False,verified=False))
    return {'id':str(obj.id),'verified':False}


class DestinationApproval(BaseModel):
    verified: bool
    enabled: bool
    node_id: uuid.UUID | None = None
    external_address_id: str | None = Field(default=None,max_length=100)
    external_delivery_id: str | None = Field(default=None,max_length=100)
    external_requisite_id: str | None = Field(default=None,pattern=r'^[0-9]{1,18}$')


@admin.patch('/supplier-destinations/{mapping_id}')
async def destination_approve(mapping_id:uuid.UUID,body:DestinationApproval,db:AsyncSession=Depends(get_session)):
    obj=await db.get(SupplierDestination,mapping_id)
    if not obj:raise HTTPException(404,'Mapping not found')
    changed=False
    if 'node_id' in body.model_fields_set:
        if body.node_id is None:raise HTTPException(422,'Node must be selected')
        node=await db.get(FulfillmentNode,body.node_id)
        account=await db.get(SupplierAccount,obj.account_id)
        if not node or not account or node.merchant_id!=account.merchant_id or not node.active:
            raise HTTPException(409,'Destination must be an active node of the account merchant')
        if obj.node_id!=body.node_id:obj.node_id=body.node_id;changed=True
    for attr in ('external_address_id','external_delivery_id','external_requisite_id'):
        if attr in body.model_fields_set:
            value=getattr(body,attr)
            if getattr(obj,attr)!=value:setattr(obj,attr,value);changed=True
    if changed:
        # Never retain previous approval after changing a delivery address, method or node.
        obj.verified=False
        obj.enabled=False
    else:
        if body.enabled and not body.verified:
            raise HTTPException(409,'Unverified destinations cannot be enabled')
        obj.verified=body.verified
        obj.enabled=body.enabled
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,'Destination mapping already exists for this account and node') from exc
    return {'id':str(obj.id),'verified':obj.verified,'enabled':obj.enabled,
            'reverification_required':changed}


@admin.get('/supplier-accounts')
async def account_list(db:AsyncSession=Depends(get_session)):
    records=(await db.scalars(select(SupplierAccount).order_by(SupplierAccount.label).limit(300))).all()
    result=[]
    for a in records:
        destinations=(await db.scalars(select(SupplierDestination).where(SupplierDestination.account_id==a.id))).all()
        result.append({'id':str(a.id),'supplier_id':str(a.supplier_id),'merchant_id':str(a.merchant_id),
                       'label':a.label,'credential_profile':a.credential_profile,'enabled':a.enabled,
                       'read_enabled':a.read_enabled,'procurement_enabled':a.procurement_enabled,
                       'requests_per_hour':a.requests_per_hour,
                       'destinations':[{'id':str(d.id),'node_id':str(d.node_id),'enabled':d.enabled,
                                        'verified':d.verified,'external_address_id':d.external_address_id,
                                        'external_delivery_id':d.external_delivery_id,
                                        'external_requisite_id':d.external_requisite_id} for d in destinations]})
    return result


class NewCategory(BaseModel):
    name: str = Field(min_length=2,max_length=160)
    slug: str = Field(pattern=SLUG)
    parent_id: uuid.UUID | None = None


@admin.post('/categories',status_code=201)
async def category_create(body:NewCategory,db:AsyncSession=Depends(get_session)):
    if body.parent_id:
        parent=await db.get(CanonicalCategory,body.parent_id)
        if not parent:raise HTTPException(404,'Parent category not found in canonical catalog')
        if not parent.active:raise HTTPException(409,'Cannot attach category below inactive parent')
    obj=await save(db,CanonicalCategory(**body.model_dump(),position=0,active=True))
    return {'id':str(obj.id),'canonical':True}


class NewIdentifier(BaseModel):
    product_id: uuid.UUID
    kind: Literal['oem','ean','gtin','interchange','internal']
    value: str = Field(min_length=2,max_length=130)


@admin.post('/identifiers',status_code=201)
async def identifier_create(body:NewIdentifier,db:AsyncSession=Depends(get_session)):
    if not await db.get(Product,body.product_id):raise HTTPException(404,'Product not found')
    obj=await save(db,ProductIdentifier(product_id=body.product_id,kind=body.kind,value=body.value,
                                        value_norm=normalize_article(body.value)))
    return {'id':str(obj.id)}


class NewProductCategory(BaseModel):
    product_id: uuid.UUID
    category_id: uuid.UUID


@admin.post('/product-categories',status_code=201)
async def category_link(body:NewProductCategory,db:AsyncSession=Depends(get_session)):
    if not await db.get(Product,body.product_id) or not await db.get(CanonicalCategory,body.category_id):
        raise HTTPException(404,'Unknown product/canonical category')
    obj=await save(db,CanonicalProductCategory(**body.model_dump(),is_primary=False))
    return {'id':str(obj.id),'canonical':True}


class NewRelation(BaseModel):
    product_id: uuid.UUID
    related_product_id: uuid.UUID
    relation: Literal['analog','replacement','related']='related'
    verified: bool = False


@admin.post('/product-relations',status_code=201)
async def relation_create(body:NewRelation,db:AsyncSession=Depends(get_session)):
    if body.product_id==body.related_product_id or not await db.get(Product,body.product_id) or not await db.get(Product,body.related_product_id):
        raise HTTPException(409,'Invalid product relation')
    # Ambiguous legacy "replacement" cannot be automatically converted to
    # directed "supersedes" without confirming the relation direction.
    raise HTTPException(410, 'Legacy relation endpoint retired. Use /api/v1/backoffice/master-catalog/relations with explicit relation type')


class NewUsed(BaseModel):
    store_id: uuid.UUID
    original_product_id: uuid.UUID | None = None
    title: str = Field(min_length=3,max_length=250)
    description: str = Field(default='',max_length=8000)
    condition: Literal['used_good','used_fair','damaged','for_parts']
    mileage_km: int | None = Field(default=None,ge=0,le=3000000)
    defects: str = Field(default='',max_length=8000)
    origin_note: str = Field(default='',max_length=400)
    photos: list[HttpUrl] = Field(default_factory=list,max_length=12)


@seller.post('/used-parts',status_code=201)
async def create_used(body:NewUsed,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    store=await assert_store_access(db,body.store_id,user.id)
    if body.original_product_id and not await db.get(Product,body.original_product_id):
        raise HTTPException(404,'Original product not found')
    data=body.model_dump(exclude={'store_id','photos'})
    used=await save(db,UsedPart(merchant_id=store.merchant_id,**data,
                                photos=[str(u) for u in body.photos]))
    return {'id':str(used.id),'status':used.status}


class PublishUsed(BaseModel):
    status: Literal['draft','review','published','archived']


@admin.patch('/used-parts/{used_id}')
async def publish_used(used_id:uuid.UUID,body:PublishUsed,db:AsyncSession=Depends(get_session)):
    used=await db.get(UsedPart,used_id)
    if not used:raise HTTPException(404,'Used part not found')
    used.status=body.status
    await db.commit()
    return {'id':str(used.id),'status':used.status}


class NewSellerOffer(BaseModel):
    store_id: uuid.UUID
    sku: str = Field(min_length=2,max_length=120)
    product_id: uuid.UUID | None = None
    used_part_id: uuid.UUID | None = None
    source_kind: Literal['own_stock','supplier'] = 'own_stock'
    supplier_account_id: uuid.UUID | None = None
    supplier_item_id: uuid.UUID | None = None
    price: Decimal = Field(ge=0,max_digits=14,decimal_places=2)
    @model_validator(mode='after')
    def validate_source(self):
        if (self.product_id is None)==(self.used_part_id is None):
            raise ValueError('Choose exactly one product or used part')
        if self.used_part_id and self.source_kind!='own_stock':
            raise ValueError('Used parts are individually stocked, not remotely procured')
        if self.source_kind=='supplier' and not (self.supplier_account_id and self.supplier_item_id):
            raise ValueError('Supplier backed offer requires account and item')
        if self.source_kind=='own_stock' and (self.supplier_account_id or self.supplier_item_id):
            raise ValueError('Own stock offer cannot reference a supplier')
        return self


@seller.post('/offers',status_code=201)
async def seller_offer_create(body:NewSellerOffer,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    store=await assert_store_access(db,body.store_id,user.id)
    if body.product_id:
        product=await db.get(Product,body.product_id)
        if not product or product.status!='published':raise HTTPException(409,'Product must be published')
    if body.used_part_id:
        used=await db.get(UsedPart,body.used_part_id)
        if not used or used.merchant_id!=store.merchant_id:raise HTTPException(409,'Used part owner mismatch')
    if body.source_kind=='supplier':
        account=await db.get(SupplierAccount,body.supplier_account_id)
        item=await db.get(SupplierItem,body.supplier_item_id)
        if not account or not item or account.merchant_id!=store.merchant_id or account.supplier_id!=item.supplier_id or item.product_id!=body.product_id:
            raise HTTPException(409,'Supplier account or item mismatch')
    obj=await save(db,SellerOffer(merchant_id=store.merchant_id,**body.model_dump()))
    return {'id':str(obj.id),'status':obj.status}


class SetOfferStatus(BaseModel):
    status: Literal['draft','active','paused','archived']


@seller.patch('/offers/{offer_id}')
async def offer_status(offer_id:uuid.UUID,body:SetOfferStatus,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    obj=await db.get(SellerOffer,offer_id)
    if not obj:raise HTTPException(404,'Offer not found')
    await assert_store_access(db,obj.store_id,user.id)
    if body.status=='active':
        if obj.used_part_id:
            used=await db.get(UsedPart,obj.used_part_id)
            if not used or used.status!='published':raise HTTPException(409,'Used item unpublished')
        elif obj.product_id:
            product=await db.get(Product,obj.product_id)
            if not product or product.status!='published':raise HTTPException(409,'Product unpublished')
    obj.status=body.status
    await db.commit()
    await invalidate(obj.product_id)
    return {'status':obj.status}


class StockSet(BaseModel):
    node_id: uuid.UUID
    on_hand: int = Field(ge=0,le=1000000)


@seller.put('/offers/{offer_id}/stock')
async def set_stock(offer_id:uuid.UUID,body:StockSet,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    # Row lock on an existing offer serializes the very first balance INSERT.
    offer=await db.scalar(select(SellerOffer).where(SellerOffer.id==offer_id).with_for_update())
    if not offer:raise HTTPException(404,'Offer not found')
    await assert_store_access(db,offer.store_id,user.id,('owner','manager','inventory'))
    if offer.source_kind!='own_stock':raise HTTPException(409,'Only own stock can have physical balances')
    node=await store_node(db,offer.merchant_id,body.node_id)
    if node.kind not in ('warehouse','warehouse_pickup'):
        raise HTTPException(409,'Inventory cannot be stored at a pickup-only node')
    balance=await db.scalar(select(InventoryBalance).where(
        InventoryBalance.seller_offer_id==offer.id,InventoryBalance.node_id==node.id).with_for_update())
    if not balance:
        balance=InventoryBalance(seller_offer_id=offer.id,node_id=node.id,on_hand=body.on_hand,reserved=0)
        before=0
    else:
        before=balance.on_hand
        if body.on_hand<balance.reserved:raise HTTPException(409,'Stock below reservations')
        balance.on_hand=body.on_hand
    db.add(balance)
    await db.flush()
    if body.on_hand != before:
        db.add(InventoryMovement(balance_id=balance.id,delta_on_hand=body.on_hand-before,
                                 delta_reserved=0,reason='stock_adjustment',
                                 idempotency_key='stock-adjust:'+str(uuid.uuid4())))
    await db.commit()
    await invalidate(offer.product_id)
    return {'id':str(balance.id),'available':balance.on_hand-balance.reserved}


@seller.get('/stores')
async def own_stores(user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    rows=(await db.execute(select(Store,StoreMembership).join(StoreMembership,Store.id==StoreMembership.store_id).where(StoreMembership.user_id==user.id))).all()
    return [{'id':str(store.id),'merchant_id':str(store.merchant_id),'name':store.name,'role':member.role} for store,member in rows]


@seller.get('/stores/{store_id}/offers')
async def seller_offers(store_id:uuid.UUID,user:User=Depends(current_user),db:AsyncSession=Depends(get_session)):
    await assert_store_access(db,store_id,user.id,('owner','manager','inventory','viewer'))
    rows=(await db.scalars(select(SellerOffer).where(SellerOffer.store_id==store_id).limit(200))).all()
    return [{'id':str(o.id),'sku':o.sku,'price':str(o.price),'status':o.status,
             'product_id':str(o.product_id) if o.product_id else None,
             'used_part_id':str(o.used_part_id) if o.used_part_id else None} for o in rows]


@public.get('/products/{product_id}/offers')
async def marketplace_offers(product_id:uuid.UUID,city:str|None=Query(default=None,max_length=120),
                             pickup_node_id:uuid.UUID|None=None,db:AsyncSession=Depends(get_session)):
    product=await db.get(Product,product_id)
    if not product or product.status!='published':raise HTTPException(404,'Product not published')
    pickup=await db.get(FulfillmentNode,pickup_node_id) if pickup_node_id else None
    if pickup_node_id and (not pickup or not pickup.active or pickup.kind not in ('pickup','warehouse_pickup')):
        raise HTTPException(404,'Pickup node not found')
    rows=(await db.scalars(select(SellerOffer).where(SellerOffer.product_id==product_id,
                                                     SellerOffer.status=='active').limit(250))).all()
    output=[]
    for offer in rows:
        try:await offer_check(db,offer.id)
        except HTTPException:continue
        store=await db.get(Store,offer.store_id)
        merchant=await db.get(Merchant,offer.merchant_id)
        if offer.source_kind=='supplier':
            from app.modules.marketplace.supplier_selling import account_quote_options
            options=await account_quote_options(db,offer,pickup_node_id)
        else:
            options=await options_for_offer(db,offer,pickup_node_id)
        if city:
            options=[x for x in options if x['city'].casefold()==city.casefold()]
        if not options:continue
        output.append({'offer_id':str(offer.id),'store':store.name,'merchant':merchant.name,
                       'merchant_id':str(merchant.id),'source_kind':offer.source_kind,
                       'price':str(offer.price),'currency':'RUB','fulfillment':options,
                       'requires_live_validation':offer.source_kind=='supplier'})
    from app.modules.marketplace.service import verified_supplier_quotes
    output.extend(await verified_supplier_quotes(db,product,pickup_node_id=pickup_node_id,city=city))
    from app.modules.supplier_feeds.storefront import preliminary_feed_offers
    output.extend(await preliminary_feed_offers(db, product_id, pickup_node_id=pickup_node_id,city=city))
    output.sort(key=lambda row:Decimal(row['price']))
    return {'product_id':str(product_id),'city':city,'offers':output,
            'note':'Supplier and file offers require live API validation before cart; payment remains disabled'}


@public.get('/products/{product_id}/offers/grouped')
async def grouped_marketplace_offers(product_id:uuid.UUID,
                             city:str|None=Query(default=None,max_length=120),
                             pickup_node_id:uuid.UUID|None=None,
                             db:AsyncSession=Depends(get_session)):
    """Additive grouped view; flat /offers remains backwards-compatible."""
    from app.modules.marketplace.grouping import group_marketplace_offers
    raw=await marketplace_offers(product_id,city=city,pickup_node_id=pickup_node_id,db=db)
    groups=group_marketplace_offers(raw['offers'])
    return {'product_id':str(product_id),'city':city,
            'group_count':len(groups),'variant_count':sum(g['variant_count'] for g in groups),
            'groups':groups,'note':raw['note']}


class QuotePreviewRequest(BaseModel):
    snapshot_id: uuid.UUID
    supplier_account_id: uuid.UUID
    supplier_destination_id: uuid.UUID
    quantity: int = Field(ge=1,le=1000)
    pickup_node_id: uuid.UUID | None = None
    route_node_ids: list[uuid.UUID] | None = Field(default=None,max_length=4)


@public.post('/products/{product_id}/offers/preview')
async def preview_supplier_quote(product_id:uuid.UUID,body:QuotePreviewRequest,
                                db:AsyncSession=Depends(get_session)):
    """No supplier call, reservations, checkout or order; reconfirm scoped snapshot."""
    from app.modules.marketplace.quote_preview import quote_breakdown
    raw=await marketplace_offers(product_id,city=None,pickup_node_id=body.pickup_node_id,db=db)
    matches=[r for r in raw['offers'] if
             r.get('source_kind')=='supplier_quote' and
             r.get('snapshot_id')==str(body.snapshot_id) and
             r.get('supplier_account_id')==str(body.supplier_account_id) and
             r.get('supplier_destination_id')==str(body.supplier_destination_id)]
    if not matches:raise HTTPException(409,'Warehouse offer expired or is no longer in the current supplier batch')
    variant=matches[0]
    paths=[p for matched in matches for p in matched.get('fulfillment', [])]
    if body.route_node_ids is not None:
        wanted=[str(v) for v in body.route_node_ids]
        paths=[p for p in paths if p.get('route_node_ids')==wanted]
    if not paths:raise HTTPException(409,'Selected delivery path is unavailable')
    # Scope all supplier snapshots to an original OfferDetail and its min/pack.
    from app.modules.offer_engine.models import OfferDetail
    detail=await db.get(OfferDetail,body.snapshot_id)
    if not detail or not detail.verified_live:
        raise HTTPException(409,'Warehouse details are not verified')
    from app.modules.suppliers.models import OfferSnapshot
    from app.modules.master_catalog.sale_terms import supplier_constraints
    snapshot=await db.get(OfferSnapshot,body.snapshot_id)
    if not snapshot:raise HTTPException(409,'Warehouse snapshot unavailable')
    sale=await supplier_constraints(db,snapshot.supplier_item_id,body.supplier_account_id,
                                    body.supplier_destination_id,detail.warehouse,
                                    detail.min_qty,detail.pack_multiple)
    variant={**variant,'min_qty':sale['minimum_units'],
             'pack_multiple':sale['step_units'],
             'units_per_package':sale['units_per_package']}
    return quote_breakdown(variant,body.quantity,paths[0])


@public.get('/products/{product_id}/used')
async def used_list(product_id:uuid.UUID,db:AsyncSession=Depends(get_session)):
    if not await db.get(Product,product_id):raise HTTPException(404,'Product not found')
    rows=(await db.scalars(select(UsedPart).where(
        UsedPart.original_product_id==product_id,UsedPart.status=='published').limit(100))).all()
    results=[]
    for u in rows:
        offers=(await db.scalars(select(SellerOffer).where(
            SellerOffer.used_part_id==u.id,SellerOffer.status=='active'))).all()
        for offer in offers:
            try: await offer_check(db,offer.id)
            except HTTPException:continue
            opts=await options_for_offer(db,offer)
            if opts:
                results.append({'id':str(u.id),'title':u.title,'condition':u.condition,
                                'mileage_km':u.mileage_km,'defects':u.defects,
                                'photos':u.photos,'offer_id':str(offer.id),
                                'price':str(offer.price),'availability':opts})
    return results


@public.get('/nodes')
async def public_nodes(city:str|None=Query(default=None,max_length=120),db:AsyncSession=Depends(get_session)):
    stmt=select(FulfillmentNode).where(FulfillmentNode.active.is_(True),FulfillmentNode.kind.in_(['pickup','warehouse_pickup']))
    if city:stmt=stmt.where(FulfillmentNode.city==city)
    nodes=(await db.scalars(stmt.order_by(FulfillmentNode.city,FulfillmentNode.name).limit(200))).all()
    return [{'id':str(n.id),'name':n.name,'city':n.city,'address':n.address} for n in nodes]


@admin.get('/overview')
async def marketplace_overview(db:AsyncSession=Depends(get_session)):
    from sqlalchemy import func
    result={}
    for name,model in [('merchants',Merchant),('stores',Store),('supplier_accounts',SupplierAccount),
                       ('warehouses_and_pickups',FulfillmentNode),('seller_offers',SellerOffer),
                       ('used_parts',UsedPart),('routes',DeliveryRoute)]:
        result[name]=int((await db.scalar(select(func.count()).select_from(model))) or 0)
    return result
