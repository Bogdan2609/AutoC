"""Supplier purchase contract normalization, without outbound writes.

Use only after paid buyer-line + tenant + quote verification performed by the
sale lifecycle and procurement boundary. An intent is NOT an authorization to
purchase.  Real order dispatch is a separate, durably leased, audited action.

Based on ROSSKO SOAP GetCheckout, ADEO XML make_orders, PARTKOM v4 order,
and FORUM-AUTO v2 addGoodsToOrder documentation (22 Oct 2026).
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping


class InvalidPurchasePlan(ValueError):
    """The supplier identifiers or buyer-approved parameters are inadequate."""


@dataclass(frozen=True)
class PurchaseIntent:
    supplier: str
    buyer_order_line_id: str
    reference: str
    brand: str
    article: str
    quantity: int
    purchase_price: Decimal
    maximum_approved_price: Decimal
    supplier_token: str
    warehouse: str = ''
    minimum: int = 1
    multiple: int = 1
    # IDs are source-specific and MUST originate from confirmed live quote.
    quote_metadata: Mapping[str, str] = field(default_factory=dict)
    external_line_id: int | None = None
    external_order_id: int | None = None
    # Explicit shipping assurance; never inferred from supplier warehouse or buyer pickup.
    delivery_mode: str = 'unverified'
    courier_route_verified: bool = False


@dataclass(frozen=True)
class PurchasePlan:
    supplier: str
    operation: str
    transport: str
    payload: dict
    reconciliation_reference: str
    # A plan is never permitted to dispatch by itself.
    dispatch_authorized: bool = False
    requires_paid_line_validation: bool = True
    requires_verified_courier_destination: bool = True


def _validate(i: PurchaseIntent) -> None:
    if i.delivery_mode != 'courier' or i.courier_route_verified is not True:
        raise InvalidPurchasePlan('Supplier purchases require a verified courier route')
    if i.supplier not in ('rossko', 'adeo', 'partkom', 'forum-auto'):
        raise InvalidPurchasePlan('Unsupported supplier')
    if not i.buyer_order_line_id or not i.reference or len(i.reference) > 80:
        raise InvalidPurchasePlan('An immutable buyer line and unique reference are mandatory')
    if not (i.brand and i.article and i.supplier_token):
        raise InvalidPurchasePlan('Live supplier identity is required')
    if i.quantity <= 0 or i.quantity > 1000 or i.minimum <= 0 or i.multiple <= 0:
        raise InvalidPurchasePlan('Invalid ordered quantity')
    if i.quantity < i.minimum or i.quantity % i.multiple:
        raise InvalidPurchasePlan('Supplier quantity constraints not satisfied')
    try:
        price = Decimal(str(i.purchase_price))
        ceiling = Decimal(str(i.maximum_approved_price))
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise InvalidPurchasePlan('Invalid buyer-approved price') from exc
    if not price.is_finite() or not ceiling.is_finite() or price <= 0 or ceiling < price:
        raise InvalidPurchasePlan('Quote price exceeds approved ceiling')
    if price != price.quantize(Decimal('.01')) or ceiling != ceiling.quantize(Decimal('.01')):
        raise InvalidPurchasePlan('Quote price has unsupported precision')


def build_purchase_plan(intent: PurchaseIntent) -> PurchasePlan:
    """Create a secret-free, side-effect-free adapter plan.

    The calling application must persist an unknown-dispatch marker before
    any supplier write, reconcile after network ambiguity, and NEVER replay
    a write just because an HTTP timeout occurred.
    """
    _validate(intent)
    i = intent
    if i.supplier == 'rossko':
        if not i.warehouse:
            raise InvalidPurchasePlan('ROSSKO requires a confirmed stock identifier')
        return PurchasePlan(i.supplier, 'GetCheckout', 'SOAP', {
            'PARTS': {'Part': [{'partnumber': i.article, 'brand': i.brand,
                                'stock': i.warehouse, 'count': i.quantity}]},
            # delivery, payment and contact must be resolved for the exact
            # account/destination by a separate verified checkout boundary.
        }, i.reference)
    if i.supplier == 'adeo':
        # internal_n_orders is documented as a numeric order reference. Never
        # derive it with Python hash() (unstable across processes).
        if i.external_order_id is None or not (1 <= i.external_order_id < 2**31):
            raise InvalidPurchasePlan('ADEO requires a persistent numeric internal order ID')
        return PurchasePlan(i.supplier, 'make_orders', 'XML POST', {
            'basket': [{'b_id': i.supplier_token, 'count': i.quantity,
                        'internal_n_orders': i.external_order_id}]},
            f'{i.supplier_token}:{i.external_order_id}')
    if i.supplier == 'partkom':
        maker = i.quote_metadata.get('maker_id')
        provider = i.quote_metadata.get('provider_id')
        if not maker or not provider or not maker.isdecimal() or not provider.isdecimal():
            raise InvalidPurchasePlan('PARTKOM requires verified makerId and providerId')
        if not i.reference.replace('_', '').replace('-', '').isalnum():
            raise InvalidPurchasePlan('PARTKOM reference contains invalid characters')
        return PurchasePlan(i.supplier, '/v4/basket/order', 'multipart/form-data POST', {
            'flagTest': '1',  # preview/test contract; not a chargeable order
            'returnOnSuccess': '1',
            'orderItems[0][detailNum]': i.article,
            'orderItems[0][makerId]': maker,
            'orderItems[0][providerId]': provider,
            'orderItems[0][price]': str(i.purchase_price),
            'orderItems[0][quantity]': str(i.quantity),
            'orderItems[0][reference]': i.reference,
            'orderItems[0][reorderAgreement]': '0',
            'orderItems[0][possibleIncreasePrice]': '0',
            'orderItems[0][possibleIncreaseDeliveryDate]': '0',
        }, i.reference)
    # Forum-Auto reuses the same eid to CHANGE line quantity. This is not an
    # idempotent create operation; a durable eid needs to be assigned once.
    if i.external_line_id is None or not (1 <= i.external_line_id < 2**31):
        raise InvalidPurchasePlan('FORUM-AUTO requires a durable numeric eid')
    payload = {'tid': i.supplier_token, 'num': i.quantity, 'eid': i.external_line_id}
    if i.external_order_id is not None:
        if not 1 <= i.external_order_id < 2**31:
            raise InvalidPurchasePlan('Invalid FORUM-AUTO eoid')
        payload['eoid'] = i.external_order_id
    return PurchasePlan(i.supplier, 'addGoodsToOrder', 'Forum-Auto API v2', payload, str(i.external_line_id))
