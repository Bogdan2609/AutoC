"""Read-only account-specific ROSSKO GetCheckoutDetails probe.

Does not mutate a supplier account, create an order, or trust invented addresses.
"""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import require_admin
from app.modules.marketplace.models import SupplierAccount
from app.modules.marketplace.account_credentials import for_account
from app.modules.suppliers.models import Supplier
from app.modules.offer_engine.discovery import claim_supplier_budget

router=APIRouter(prefix='/api/v1/admin/marketplace', tags=['supplier-account-probes'],
                 dependencies=[Depends(require_admin)])


def records(obj,container,field):
    if not isinstance(obj,dict):return []
    values=obj.get(container) or {}
    if not isinstance(values,dict):return []
    rows=values.get(field) or []
    return [x for x in (rows if isinstance(rows,list) else [rows]) if isinstance(x,dict)]


def normalize_checkout_details(result):
    if not isinstance(result,dict):raise ValueError('Unexpected SOAP result')
    o=result.get('CheckoutDetailsResult',result)
    if not isinstance(o,dict) or str(o.get('success',o.get('Success',''))).lower() not in ('true','1'):
        raise ValueError('Supplier rejected checkout detail lookup')
    deliveries=[{'id':str(x.get('id',''))[:32], 'name':str(x.get('name',''))[:160]}
                for x in records(o,'DeliveryType','delivery')][:80]
    payments=[{'id':str(x.get('id',''))[:32],'name':str(x.get('name',''))[:160]}
              for x in records(o,'PaymentType','payment')][:50]
    addresses=[]
    for x in records(o,'DeliveryAddress','address')[:100]:
        delivery_ids=x.get('Delivery') or {}
        ids=(delivery_ids.get('ids') or {}) if isinstance(delivery_ids,dict) else {}
        ids=ids.get('id') or [] if isinstance(ids,dict) else []
        ids=ids if isinstance(ids,list) else [ids]
        addresses.append({'id':str(x.get('id',''))[:80],
                          'city':str(x.get('city',''))[:120],
                          'street':str(x.get('street',''))[:160],
                          'house':str(x.get('house',''))[:50],
                          'allowed_delivery_ids':[str(v)[:32] for v in ids][:20]})
    # Do not disclose CompanyList tax identifiers and sensitive contract details here.
    return {'deliveries':deliveries,'payments':payments,'addresses':addresses}


async def live_rossko_details(cfg):
    import httpx
    from zeep import AsyncClient
    from zeep.transports import AsyncTransport
    from zeep.helpers import serialize_object
    async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=4)) as http:
        with httpx.Client(timeout=httpx.Timeout(8,connect=4)) as wsdl_http:
            client=AsyncClient('https://api.rossko.ru/service/v2.1/GetCheckoutDetails?wsdl',
                              transport=AsyncTransport(client=http,wsdl_client=wsdl_http))
            reply=await client.service.GetCheckoutDetails(KEY1=cfg.rossko_key1,KEY2=cfg.rossko_key2)
    return serialize_object(reply,target_cls=dict)


@router.post('/supplier-accounts/{account_id}/checkout-details')
async def checkout_details(account_id:UUID,db:AsyncSession=Depends(get_session)):
    s=get_settings()
    if not s.supplier_api_enabled:raise HTTPException(403,'External supplier reads are disabled')
    a=await db.get(SupplierAccount,account_id)
    supplier=await db.get(Supplier,a.supplier_id) if a else None
    if not a or not supplier or supplier.code!='rossko' or not a.enabled or not a.read_enabled:
        raise HTTPException(409,'Enabled ROSSKO account required')
    # GetCheckoutDetails requires only KEY1/KEY2. A delivery ID is obtained
    # FROM this read-only response and must not be required to request it.
    cfg=for_account('rossko',a)
    if not (cfg.rossko_key1 and cfg.rossko_key2):
        raise HTTPException(409,'ROSSKO API keys not configured for this account profile')
    if not await claim_supplier_budget('rossko',str(a.id),a.requests_per_hour):
        raise HTTPException(429,'Supplier quota exhausted or Redis unavailable')
    await db.commit() # network I/O never holds a DB transaction
    try:
        result=normalize_checkout_details(await live_rossko_details(cfg))
    except Exception:
        raise HTTPException(503,'ROSSKO account probe failed; check credentials and supplier portal') from None
    return {'account_id':str(a.id),'supplier':'rossko','read_only':True,**result}


@router.get('/supplier-integrations/capabilities')
async def supplier_integration_capabilities():
    """Zero-network, zero-secrets supplier operation matrix."""
    from app.integrations.suppliers.diagnostics import matrix
    return matrix()


@router.get('/supplier-accounts/{account_id}/integration-readiness')
async def supplier_integration_readiness(account_id: UUID,
                                         db: AsyncSession = Depends(get_session)):
    """Local-only readiness per permitted warehouse/destination; not an API probe."""
    from sqlalchemy import select
    from app.modules.marketplace.models import SupplierDestination, FulfillmentNode
    from app.integrations.suppliers.diagnostics import readiness
    account = await db.get(SupplierAccount, account_id)
    if account is None:
        raise HTTPException(404, 'Supplier account not found')
    supplier = await db.get(Supplier, account.supplier_id)
    if supplier is None:
        raise HTTPException(409, 'Supplier account has no organization')
    destinations = (await db.scalars(select(SupplierDestination).where(
        SupplierDestination.account_id == account.id).order_by(SupplierDestination.id))).all()
    rows = []
    for destination in destinations[:100]:
        node = await db.get(FulfillmentNode, destination.node_id)
        if node is None:
            rows.append({'supplier': supplier.code, 'account_id': str(account.id),
                         'destination_id': str(destination.id), 'node_id': str(destination.node_id),
                         'ready': False, 'read_only': True, 'status': 'not_ready'})
            continue
        rows.append(readiness(supplier.code, supplier=supplier, account=account,
                              destination=destination, node=node))
    return {'supplier': supplier.code, 'account_id': str(account.id),
            'read_only': True, 'checked_destinations': len(rows), 'items': rows,
            'note': 'Local configuration only; not provider connectivity or purchase approval'}


class SupplierOrderReadbackInput(BaseModel):
    destination_id: UUID
    external_reference: str = Field(min_length=1, max_length=80)


@router.post('/supplier-accounts/{account_id}/order-readback')
async def supplier_order_readback(account_id: UUID, body: SupplierOrderReadbackInput,
                                  db: AsyncSession = Depends(get_session)):
    """Explicit operator-initiated READ only. Never changes a purchase/order.

    POST is used only to protect input from URL logs and trigger staff CSRF.
    Reject any unverified account/destination before contacting the provider.
    """
    from app.integrations.suppliers.factory import SupplierFactory, SupplierBindingError
    from app.integrations.suppliers.order_readback import (
        ReadbackError, normalize_order, validate_reference)
    from app.integrations.suppliers.order_transport import read_external_order
    from app.modules.marketplace.models import SupplierDestination, FulfillmentNode
    from app.modules.offer_engine.discovery import claim_supplier_budget
    from app.core.config import get_settings
    if not get_settings().supplier_api_enabled or get_settings().app_env == 'test':
        raise HTTPException(403, 'Live supplier order reads disabled')
    account = await db.get(SupplierAccount, account_id)
    dest = await db.get(SupplierDestination, body.destination_id)
    supplier = await db.get(Supplier, account.supplier_id) if account else None
    node = await db.get(FulfillmentNode, dest.node_id) if dest else None
    if not all((account, dest, supplier, node)):
        raise HTTPException(404, 'Supplier route not found')
    try:
        ref = validate_reference(supplier.code, body.external_reference)
        binding = SupplierFactory().for_search(
            supplier.code,supplier=supplier,account=account,destination=dest,node=node)
    except (ReadbackError, SupplierBindingError, ValueError):
        raise HTTPException(409, 'Supplier readback route or order reference invalid') from None
    if not binding.scoped:
        raise HTTPException(409, 'Account-scoped readback required')
    if not await claim_supplier_budget(supplier.code, str(account.id), account.requests_per_hour):
        raise HTTPException(429, 'Supplier read budget exhausted')
    config=binding.config
    await db.commit()  # Never hold a database transaction across provider HTTP.
    try:
        raw=await read_external_order(supplier.code, config, ref)
        result=normalize_order(supplier.code, ref, raw)
    except Exception:
        # Never surface supplier URL (Forum credentials may be in query),
        # supplier raw XML/JSON, network diagnostics or private fields.
        raise HTTPException(503, 'Supplier order readback unavailable or inconclusive') from None
    return {'account_id': str(account.id), 'destination_id': str(dest.id),
            **result.public(), 'stored_changes': False,
            'operator_action': 'Verify buyer order, exact SKU, quantities and courier evidence separately'}
