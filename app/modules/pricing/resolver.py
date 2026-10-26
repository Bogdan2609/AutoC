"""The single public-quote commercial policy selection boundary.

Finance F2 performs the actual calculations. A verified exact warehouse rule
wins; otherwise the old global markup is visibly UNVERIFIED and is *never*
considered a final, payment-capable or procurement-capable customer quote.
"""
from app.modules.pricing.service import calculate_retail
from app.modules.supplier_feeds.pricing import quote_unit, usable_rule


def resolve_supplier_quote(purchase_price, *, legacy_policy=None,
                           commercial_rule=None, feed_source=None,
                           warehouse_map_approved=False, tax_status=None):
    valid = bool(commercial_rule is not None and feed_source is not None and
        warehouse_map_approved and tax_status == commercial_rule.tax_status_verified and
        usable_rule(commercial_rule, feed_source))
    if valid:
        result = quote_unit(purchase_price, commercial_rule)
        return {'price': result['price'], 'pricing_basis': 'approved_landed_cost',
                'pricing_breakdown': result, 'price_policy_version': commercial_rule.version,
                'price_is_final': False, 'payment_enabled': False}
    if legacy_policy is None:
        return None
    return {'price': str(calculate_retail(purchase_price, legacy_policy.markup_percent)),
            'pricing_basis': 'legacy_markup_unverified_costs',
            'pricing_breakdown': None, 'price_policy_version': legacy_policy.version,
            'price_is_final': False, 'payment_enabled': False}
