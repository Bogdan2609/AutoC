"""Read-only quote breakdown for ONE verified warehouse+receiving+optional pickup path.

No cart/order/payment mutation. No supplier network operation. Supplier delivery
charge is unknown in v0.16; don't ever expose the known subtotal as grand total.
"""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException

TWO = Decimal('0.01')


def quote_breakdown(variant, quantity, fulfillment):
    if not (1 <= quantity <= 1000):
        raise HTTPException(422, 'Quantity must be 1..1000')
    if not fulfillment or int(fulfillment.get('stock') or 0) < quantity:
        raise HTTPException(409, 'Requested quantity is no longer available')
    min_qty = max(1, int(variant.get('min_qty') or 1))
    multiple = max(1, int(variant.get('pack_multiple') or 1))
    if quantity < min_qty or quantity % multiple:
        raise HTTPException(409, f'Supplier requires min quantity {min_qty} and pack multiple {multiple}')
    from app.modules.marketplace.commercial_guardrails import checked_money
    unit = checked_money(variant['price'], positive=True)
    merchant_fee = checked_money(fulfillment.get('fee', '0.00'))
    goods = (unit * quantity).quantize(TWO, rounding=ROUND_HALF_UP)
    known = (goods + merchant_fee).quantize(TWO, rounding=ROUND_HALF_UP)
    # The Boolean alone does not quote a price: no payable total without an explicit fee.
    supplier_fee_known = (fulfillment.get('supplier_fee_known') is True
                          and fulfillment.get('supplier_delivery_fee') is not None)
    supplier_fee = (checked_money(fulfillment['supplier_delivery_fee'])
                    if supplier_fee_known else None)
    if supplier_fee is not None:
        known = (known + supplier_fee).quantize(TWO, rounding=ROUND_HALF_UP)
    pickup_selected = bool(fulfillment.get('pickup_node_id'))
    return {
        'source_kind': variant['source_kind'], 'supplier_code': variant.get('supplier_code'),
        'snapshot_id': variant.get('snapshot_id'), 'supplier_account_id': variant.get('supplier_account_id'),
        'supplier_destination_id': variant.get('supplier_destination_id'),
        'warehouse_id': variant.get('warehouse_id'),
        'quantity': quantity, 'effective_min_qty': min_qty, 'effective_pack_multiple': multiple,
        'units_per_package': int(variant.get('units_per_package') or 1),
        'available_stock_lower_bound': int(fulfillment['stock']),
        'stock_display': fulfillment.get('stock_display') or str(fulfillment['stock']),
        'unit_price': str(unit.quantize(TWO)), 'goods_subtotal': str(goods),
        'merchant_delivery_fee': str(merchant_fee.quantize(TWO)),
        'known_components_total': str(known),
        'supplier_delivery_fee_known': supplier_fee_known,
        'supplier_delivery_fee': str(supplier_fee) if supplier_fee is not None else None,
        'grand_total': str(known) if supplier_fee_known and pickup_selected else None,
        'final_price_complete': bool(supplier_fee_known and pickup_selected),
        'pickup_selected': pickup_selected,
        'delivery_scope': fulfillment.get('delivery_scope','receiving_only'),
        'estimated_days': fulfillment.get('days'),
        'route_legs': fulfillment.get('route_legs', []),
        'route_node_ids': fulfillment.get('route_node_ids', []),
        'receiving_node': fulfillment.get('receiving_node'),
        'receiving_node_id': fulfillment.get('receiving_node_id'),
        'pickup_node_id': fulfillment.get('pickup_node_id'),
        'requires_live_validation': True,
        'cart_enabled': False, 'payment_enabled': False,
        'note': ('Расчёт по последней успешной проценке. Тариф доставки поставщика неизвестен; '
                 'окончательная цена и наличие требуют проверки перед оформлением. '
                 'Закупка и оплата через этот расчёт отключены.'),
    }
