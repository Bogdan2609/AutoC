"""v0.31.5: Deterministic feed indicative unit prices for verified account/warehouse terms.

No order/payment API uses a supplier FILE price: a live account quote is mandatory.
No guessed VAT deduction: the documented purchase gross is the cost base.
"""
from decimal import Decimal
from sqlalchemy import select
from app.modules.marketplace.models import SupplierAccount, SupplierDestination
from .models import FeedPriceRule, FeedSource, FeedWarehouseMap
from app.modules.finance.pricing_engine import cost_coverage_quote

CENT = Decimal('0.01')
HUNDRED = Decimal('100')


def money(value):
    d=Decimal(str(value))
    if not d.is_finite() or d<0:
        raise ValueError('Negative or nonfinite cost is forbidden')
    return d


def quote_unit(purchase_price, rule):
    """Compatibility adapter for an approved warehouse rule.

    The price math lives exclusively in the shared Finance F2 core.
    Policy validation, approval and provenance remain in Supplier Feed Gateway.
    """
    purchase=money(purchase_price)
    costs={k:money(getattr(rule,k)) for k in
           ('supplier_delivery','packaging','other_costs','tax_reserve','sbp_fixed')}
    # Existing in-memory test rules may predate the dedicated destination cost.
    costs['last_mile_delivery']=money(getattr(rule,'last_mile_delivery',Decimal('0')))
    if money(rule.markup_percent)>Decimal('500') or money(rule.sbp_percent)>Decimal('10'):
        raise ValueError('Unreviewed excessive markup or SBP tariff in feed pricing policy')
    result=cost_coverage_quote(
        purchase_gross=purchase,markup_percent=money(rule.markup_percent),
        supplier_delivery=costs['supplier_delivery'],last_mile_delivery=costs['last_mile_delivery'],
        packaging=costs['packaging'],
        other_costs=costs['other_costs'],tax_reserve=costs['tax_reserve'],
        sbp_percent=money(rule.sbp_percent),sbp_fixed=costs['sbp_fixed'],
        min_contribution=money(rule.min_contribution))
    return {'price':result['price'],'purchase_price':str(purchase),
            'supplier_delivery':str(costs['supplier_delivery']),
            'last_mile_delivery':str(costs['last_mile_delivery']),'packaging':str(costs['packaging']),
            'other_costs':str(costs['other_costs']),'tax_reserve':str(costs['tax_reserve']),
            'sbp_fee':result['bank_fee'],'contribution':result['cash_contribution'],
            'target_contribution':result['target_contribution'],
            'pricing_core':result['pricing_core'],
            'pricing_rule_id':str(rule.id),'pricing_rule_version':rule.version,
            'tax_note':'No VAT input deduction assumed; explicit reserve is not tax accounting',
            'requires_live_validation':True,'cart_enabled':False,'payment_enabled':False}


async def active_rules_for(db, scope_tuples):
    """Never fall back from an account/warehouse to a different city/contract."""
    if not scope_tuples:return {}
    account_ids={a for a,d,w in scope_tuples}
    rows=(await db.scalars(select(FeedPriceRule).where(FeedPriceRule.approved.is_(True),
        FeedPriceRule.account_id.in_(account_ids)))).all()
    return {(r.account_id,r.destination_id,r.warehouse_code):r for r in rows}


def usable_rule(rule, source):
    return bool(rule and rule.approved and source.commercial_verified and
         source.price_kind=='purchase' and source.currency=='RUB' and
         # Gross-cost arithmetic cannot safely price VAT-exclusive supplier feeds.
         source.tax_status == 'included' and
         source.tax_status==rule.tax_status_verified)
