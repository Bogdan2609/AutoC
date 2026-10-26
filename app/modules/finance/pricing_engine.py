"""Deterministic F2 monetary previews, no IO. All results NON-ACCOUNTING simulation."""
from __future__ import annotations
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.modules.finance.tax_engine import PreviewInput, preview as vat_preview

Q = Decimal('0.01')
HUNDRED=Decimal('100')


def round_money(value: Decimal)->Decimal:
    if not value.is_finite():
        raise ValueError('Money must be finite')
    return value.quantize(Q,rounding=ROUND_HALF_UP)


def simple_markup_quote(purchase_price: Decimal, markup_percent: Decimal) -> Decimal:
    """Legacy percent markup, now owned by the Finance pricing arithmetic core.

    This is an UNVERIFIED cost estimate, never a production landed-cost quote.
    """
    if (not purchase_price.is_finite() or purchase_price < 0 or
            not markup_percent.is_finite() or not Decimal('0') <= markup_percent <= Decimal('1000')):
        raise ValueError('Invalid gross purchase or markup')
    return round_money(purchase_price * (Decimal('1') + markup_percent/HUNDRED))


def fee_amount(base: Decimal,percent: Decimal=Decimal('0'),fixed: Decimal=Decimal('0'),
               cap: Decimal|None=None, *, rounding: Literal['HALF_UP','CEILING']='HALF_UP')->Decimal:
    if any(not n.is_finite() or n<0 for n in (base,percent,fixed)) or percent>100:
        raise ValueError('Invalid fee')
    if cap is not None and (not cap.is_finite() or cap<0):
        raise ValueError('Invalid cap')
    if rounding not in ('HALF_UP','CEILING'):
        raise ValueError('Unknown fee rounding')
    mode=ROUND_CEILING if rounding=='CEILING' else ROUND_HALF_UP
    amount=(base*percent/HUNDRED+fixed).quantize(Q,rounding=mode)
    return min(amount,cap) if cap is not None else amount


def cost_coverage_quote(*, purchase_gross: Decimal, markup_percent: Decimal,
                        supplier_delivery: Decimal=Decimal('0'),
                        last_mile_delivery: Decimal=Decimal('0'),
                        packaging: Decimal=Decimal('0'), other_costs: Decimal=Decimal('0'),
                        tax_reserve: Decimal=Decimal('0'), sbp_percent: Decimal=Decimal('0'),
                        sbp_fixed: Decimal=Decimal('0'), sbp_cap: Decimal|None=None,
                        min_contribution: Decimal=Decimal('0')) -> dict:
    """A deterministic minimum-gross-price calculation, in RUB, shared by F2 and feeds.

    Conservative bank fee rounding is UP to kopecks. Binary search finds the
    minimal price satisfying documented cash-cost coverage (even at near-100%
    bank fees). The cash contribution is BEFORE taxes, NOT a net profit.
    This is not a checkout authorization, VAT computation, or supplier quote.
    """
    amounts=(purchase_gross, markup_percent, supplier_delivery, last_mile_delivery,
             packaging, other_costs, tax_reserve, sbp_percent, sbp_fixed, min_contribution)
    if any(not isinstance(x, Decimal) or not x.is_finite() or x<0 for x in amounts):
        raise ValueError('All pricing inputs must be finite nonnegative Decimals')
    if purchase_gross <= 0 or markup_percent > 1000 or sbp_percent >= 100:
        raise ValueError('Invalid purchase price, markup or SBP percent')
    if sbp_cap is not None and (not isinstance(sbp_cap,Decimal) or not sbp_cap.is_finite() or sbp_cap<0):
        raise ValueError('Invalid SBP cap')
    total_costs=supplier_delivery+last_mile_delivery+packaging+other_costs+tax_reserve
    base=purchase_gross+total_costs
    margin=max(min_contribution,purchase_gross*markup_percent/HUNDRED)
    target=base+margin
    rate=sbp_percent/HUNDRED
    # The upper bound is conservative: even without a fee cap it covers fees.
    lo=int((target/Q).to_integral_value(rounding=ROUND_CEILING))
    hi=int(((target+sbp_fixed)/(Decimal('1')-rate)/Q).to_integral_value(rounding=ROUND_CEILING))+2
    def meets(cents:int)->bool:
        price=Q*cents
        fee=fee_amount(price,sbp_percent,sbp_fixed,sbp_cap,rounding='CEILING')
        return price-base-fee >= margin
    while lo<hi:
        mid=(lo+hi)//2
        if meets(mid):hi=mid
        else:lo=mid+1
    price=Q*lo
    fee=fee_amount(price,sbp_percent,sbp_fixed,sbp_cap,rounding='CEILING')
    return {'price':str(price),'bank_fee':str(fee),'cash_contribution':str(round_money(price-base-fee)),
            'target_contribution':str(round_money(margin)),
            'documented_costs':str(round_money(total_costs)),
            'landed_cost_gross':str(round_money(base)),
            'tax_reserve':str(tax_reserve),
            'pricing_core':'finance_f2_cost_coverage_v1'}


class RoundingTier(BaseModel):
    """Price band with upper exclusive bound; last tier must be open-ended."""
    upper_exclusive: Decimal | None = Field(default=None,gt=0,max_digits=16,decimal_places=2)
    step: Decimal = Field(ge=1,le=100000,max_digits=12,decimal_places=2)
    max_uplift_pct: Decimal = Field(ge=0,le=10,max_digits=5,decimal_places=2)

    @model_validator(mode='after')
    def validate_step(self):
        if self.step != self.step.to_integral_value():
            raise ValueError('Price rounding step must be a whole number of roubles')
        return self


DEFAULT_ROUNDING_TIERS = (
    RoundingTier(upper_exclusive=Decimal('100'),step=Decimal('1'),max_uplift_pct=Decimal('2')),
    RoundingTier(upper_exclusive=Decimal('500'),step=Decimal('5'),max_uplift_pct=Decimal('2')),
    RoundingTier(upper_exclusive=Decimal('5000'),step=Decimal('10'),max_uplift_pct=Decimal('1')),
    RoundingTier(upper_exclusive=Decimal('20000'),step=Decimal('50'),max_uplift_pct=Decimal('1')),
    RoundingTier(upper_exclusive=None,step=Decimal('100'),max_uplift_pct=Decimal('1')),
)


def validate_rounding_tiers(tiers):
    if not 1 <= len(tiers) <= 20:
        raise ValueError('Price rounding requires 1 to 20 tiers')
    if tiers[-1].upper_exclusive is not None:
        raise ValueError('Last tier must have null upper_exclusive')
    prev = Decimal('0')
    for tier in tiers[:-1]:
        if tier.upper_exclusive is None or tier.upper_exclusive <= prev:
            raise ValueError('Price rounding bands must be ascending, with no gaps')
        prev = tier.upper_exclusive
    return tiers


def ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return (value/step).to_integral_value(rounding=ROUND_CEILING)*step


def customer_price_rounding(value: Decimal, mode: str='EXACT_RUB', tiers=None,
                            max_uplift_pct: Decimal | None = None,
                            price_ceiling: Decimal | None = None) -> dict:
    """Never reduce a quote; never add rounding beyond a cap; choose first allowed step."""
    raw=round_money(value)
    if raw<0:raise ValueError('Cannot round a negative customer price')
    if mode not in ('EXACT_RUB','SMART_UP'):
        raise ValueError('Unknown rounding mode')
    config=validate_rounding_tiers([x if isinstance(x,RoundingTier) else RoundingTier.model_validate(x) for x in (tiers if tiers is not None else DEFAULT_ROUNDING_TIERS)])
    buyer=ceil_to(raw,Decimal('1'))
    selected=Decimal('1')
    uplift_cap_met=True
    if mode=='SMART_UP':
        uplift_cap_met=False
        tier=next((x for x in config if x.upper_exclusive is None or raw < x.upper_exclusive),config[-1])
        pct=tier.max_uplift_pct if max_uplift_pct is None else max_uplift_pct
        for step in sorted({tier.step, *(x for x in (Decimal('100'),Decimal('50'),Decimal('10'),Decimal('5'),Decimal('1')) if x<tier.step)}, reverse=True):
            candidate=ceil_to(raw,step)
            if raw>0 and (candidate-raw)*HUNDRED > raw*pct:
                continue
            if price_ceiling is not None and candidate>price_ceiling:
                continue
            buyer=candidate;selected=step;uplift_cap_met=True;break
    buyer=round_money(buyer)
    ceiling_met=price_ceiling is None or buyer<=price_ceiling
    return {'unrounded_customer_gross':str(raw),'customer_gross':str(buyer),
            'rounding_mode':mode,'rounding_step_used':str(selected),
            'rounding_uplift':str(round_money(buyer-raw)),
            'price_ceiling_met':ceiling_met,'rounding_uplift_cap_met':uplift_cap_met}

class PricePreview(BaseModel):
    operation_date: date
    sale_model: Literal['OWN','AGENCY']
    strategy: Literal['MARKUP_SIMULATION','COST_COVERAGE'] = 'MARKUP_SIMULATION'
    # For OWN: gross purchase invoice amount; for AGENCY: customer sale price.
    source_gross: Decimal = Field(ge=0,max_digits=20,decimal_places=2)
    markup_percent: Decimal = Field(default=Decimal('0'),ge=0,le=500,max_digits=7,decimal_places=2)
    fixed_markup: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    floor_price: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    min_contribution: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    procurement_delivery: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    last_mile_delivery: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    packaging: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    other_costs: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    tax_reserve: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    sbp_percent: Decimal = Field(default=Decimal('0'),ge=0,le=10,max_digits=7,decimal_places=3)
    sbp_fixed: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    sbp_cap: Decimal | None = Field(default=None,ge=0,max_digits=20,decimal_places=2)
    marketplace_percent: Decimal = Field(default=Decimal('0'),ge=0,le=100,max_digits=7,decimal_places=3)
    marketplace_fixed: Decimal = Field(default=Decimal('0'),ge=0,max_digits=20,decimal_places=2)
    marketplace_cap: Decimal | None = Field(default=None,ge=0,max_digits=20,decimal_places=2)
    bank_fee_bearer: Literal['PLATFORM','SELLER'] = 'PLATFORM'
    vat_scheme: Literal['STANDARD','USN_5','USN_7','EXEMPT'] | None = None
    purchase_vat_rate: Literal['0','5','7','10','20','22'] | None = None
    purchase_vat_deductible: bool = False
    purchase_document_reference: str | None = Field(default=None,max_length=300)
    tax_profile_id: str | None = None
    pricing_rule_id: str | None = None
    sbp_fee_schedule_id: str | None = None
    marketplace_fee_schedule_id: str | None = None
    merchant_id: str | None = None
    supplier_id: str | None = None
    category_id: str | None = None
    brand_id: str | None = None
    product_id: str | None = None
    # Applies only to OWN sale; AGENCY seller price must never be silently changed.
    rounding_mode: Literal['EXACT_RUB','SMART_UP'] = 'EXACT_RUB'
    rounding_tiers: list[RoundingTier] | None = None
    rounding_max_uplift_pct: Decimal | None = Field(default=None,ge=0,le=10,max_digits=5,decimal_places=2)
    price_ceiling: Decimal | None = Field(default=None,ge=0,max_digits=20,decimal_places=2)
    simulated: Literal[True] = True

    # Prevent catastrophically high simulated markup/bank-fee parameters.
    # For production these limits must be reviewed against actual merchant tariffs.
    @model_validator(mode='after')
    def validate(self):
        if self.operation_date.year != 2026:
            raise ValueError('F2 preview supports reviewed 2026 VAT rules only')
        if self.rounding_tiers is not None:validate_rounding_tiers(self.rounding_tiers)
        if self.strategy=='COST_COVERAGE' and (self.sale_model!='OWN' or self.fixed_markup or self.floor_price or self.rounding_mode!='EXACT_RUB' or self.rounding_tiers is not None or self.rounding_max_uplift_pct is not None or self.price_ceiling is not None):
            raise ValueError('Cost coverage is OWN-only and incompatible with legacy floor/fixed markup/rounding')
        if self.sale_model=='AGENCY' and (self.rounding_mode!='EXACT_RUB' or self.rounding_tiers is not None or self.rounding_max_uplift_pct is not None or self.price_ceiling is not None):
            raise ValueError('Agency prices are seller-controlled; platform rounding is forbidden')
        if self.sale_model=='OWN' and (self.marketplace_percent or self.marketplace_fixed):
            raise ValueError('Own sale must not treat marketplace seller fees as own income')
        if self.sale_model=='AGENCY' and (self.markup_percent or self.fixed_markup or self.floor_price or self.min_contribution or self.procurement_delivery or self.purchase_vat_rate or self.purchase_vat_deductible):
            raise ValueError('Agent has no purchase cost or mark-up on third party merchandise')
        if self.purchase_vat_deductible and (self.purchase_vat_rate in (None,'0') or self.vat_scheme!='STANDARD' or not self.purchase_document_reference):
            raise ValueError('Deduction requires a source document, input VAT rate and standard VAT scheme; verification required before production')
        for key in ('source_gross','markup_percent','fixed_markup','floor_price','min_contribution','procurement_delivery','last_mile_delivery','packaging','other_costs','tax_reserve','sbp_percent','sbp_fixed','marketplace_percent','marketplace_fixed'):
            if not getattr(self,key).is_finite():
                raise ValueError(f'{key} must be finite')
        return self


def calculate(req:PricePreview)->dict:
    """Quote without side effects, all figures are preliminary and not a tax filing."""
    bank = Decimal('0')
    marketplace = Decimal('0')
    warnings=['SIMULATION ONLY: Figures require verification of tax profile, contracts, bank tariff and source documents.',
              'Not connected to live Pricing Engine, customer offers, payment or settlement.']
    costs=round_money(req.procurement_delivery+req.last_mile_delivery+req.packaging+req.other_costs+req.tax_reserve)
    if req.sale_model=='OWN':
        if req.strategy=='COST_COVERAGE':
            covered=cost_coverage_quote(purchase_gross=req.source_gross,markup_percent=req.markup_percent,
                supplier_delivery=req.procurement_delivery,last_mile_delivery=req.last_mile_delivery,
                packaging=req.packaging,other_costs=req.other_costs,tax_reserve=req.tax_reserve,
                sbp_percent=req.sbp_percent,sbp_fixed=req.sbp_fixed,sbp_cap=req.sbp_cap,
                min_contribution=req.min_contribution)
            customer=Decimal(covered['price'])
            rounding={'unrounded_customer_gross':str(customer),'customer_gross':str(customer),
                      'rounding_mode':'COST_COVERAGE_KOPECK_UP','rounding_step_used':'0.01',
                      'rounding_uplift':'0.00','price_ceiling_met':True,'rounding_uplift_cap_met':True}
        else:
            preliminary=max(req.floor_price,(req.source_gross+req.procurement_delivery+req.last_mile_delivery+req.packaging+req.other_costs)*(1+req.markup_percent/HUNDRED)+req.fixed_markup)
            rounding=customer_price_rounding(preliminary, req.rounding_mode, req.rounding_tiers, req.rounding_max_uplift_pct, req.price_ceiling)
            customer=Decimal(rounding['customer_gross'])
        if not rounding['price_ceiling_met']:
            warnings.append('Price ceiling cannot be respected without lowering the computed price; preview is blocked.')
        if not rounding['rounding_uplift_cap_met']:
            warnings.append('Rouble rounding exceeds maximum allowed price uplift; preview is blocked.')
        bank=Decimal(covered['bank_fee']) if req.strategy=='COST_COVERAGE' else fee_amount(customer,req.sbp_percent,req.sbp_fixed,req.sbp_cap)
        # All prices are gross until verified VAT scheme and rate are explicitly given.
        cash_contribution=round_money(customer-req.source_gross-costs-bank)
        if req.vat_scheme is not None:
            vat=vat_preview(PreviewInput(gross_amount=customer,operation_date=req.operation_date,sale_model='OWN',operation_kind='GOODS',vat_scheme=req.vat_scheme))
            output_vat=Decimal(vat['vat_amount'])
            input_vat=Decimal('0.00')
            if req.purchase_vat_rate is not None and req.purchase_vat_rate!='0':
                input_vat=round_money(req.source_gross*Decimal(req.purchase_vat_rate)/(HUNDRED+Decimal(req.purchase_vat_rate)))
            contribution=round_money(cash_contribution-output_vat+(input_vat if req.purchase_vat_deductible else Decimal('0')))
            vat_amount=str(output_vat)
        else:
            contribution=None;vat_amount=None
            warnings.append('Tax regime unknown: contribution net of sales VAT is not available.')
        floor_met=(contribution>=req.min_contribution) if contribution is not None else None
        if floor_met is False:
            warnings.append('The planned minimum contribution is not met; preview does not silently raise the selling price.')
        return {'status':'SIMULATION' if rounding['price_ceiling_met'] and rounding['rounding_uplift_cap_met'] else 'SIMULATION_CONSTRAINT_FAILED','sale_model':'OWN',**rounding, 'purchase_gross':str(req.source_gross),
                'landed_cost_gross':str(round_money(req.source_gross+req.procurement_delivery+req.last_mile_delivery+req.packaging+req.other_costs+req.tax_reserve)),
                'delivery_and_other_costs':str(costs),'bank_fee':str(bank),'platform_fee_gross':'0.00',
                'seller_payout_gross':None,'cash_contribution_before_tax':str(cash_contribution),
                'output_vat_estimate':vat_amount,'net_contribution_estimate':str(contribution) if contribution is not None else None,
                'minimum_contribution_met':floor_met,'profit_after_income_tax':None,
                'bank_fee_bearer':'PLATFORM','warnings':warnings,'production_approved':False}
    customer=round_money(req.source_gross)
    marketplace=fee_amount(customer,req.marketplace_percent,req.marketplace_fixed,req.marketplace_cap)
    bank=fee_amount(customer,req.sbp_percent,req.sbp_fixed,req.sbp_cap)
    seller_payout=round_money(customer-marketplace-(bank if req.bank_fee_bearer=='SELLER' else Decimal('0')))
    if seller_payout<0:
        raise ValueError('Commission and seller-borne charges exceed seller proceeds')
    platform_cash=round_money(marketplace-(bank if req.bank_fee_bearer=='PLATFORM' else Decimal('0'))-costs)
    if req.vat_scheme is not None:
        vat=vat_preview(PreviewInput(gross_amount=marketplace,operation_date=req.operation_date,sale_model='AGENCY',operation_kind='AGENCY_FEE',vat_scheme=req.vat_scheme))
        output_vat=Decimal(vat['vat_amount']);net_estimate=round_money(platform_cash-output_vat)
        vat_amount=str(output_vat)
    else:
        net_estimate=None;vat_amount=None
        warnings.append('Platform tax regime unknown: net agency fee after VAT is unavailable.')
    warnings.append('Seller tax on gross merchandise sale is separate and NOT calculated here.')
    return {'status':'SIMULATION','sale_model':'AGENCY','customer_gross':str(customer),
            'unrounded_customer_gross':str(customer),'rounding_mode':'SELLER_PRICE_UNCHANGED',
            'rounding_step_used':None,'rounding_uplift':'0.00','price_ceiling_met':None,'rounding_uplift_cap_met':None,'purchase_gross':None,
            'landed_cost_gross':None,'delivery_and_other_costs':str(costs),'bank_fee':str(bank),
            'platform_fee_gross':str(marketplace),'seller_payout_gross':str(seller_payout),
            'cash_contribution_before_tax':str(platform_cash),'output_vat_estimate':vat_amount,
            'net_contribution_estimate':str(net_estimate) if net_estimate is not None else None,
            'minimum_contribution_met':None,'profit_after_income_tax':None,
            'bank_fee_bearer':req.bank_fee_bearer,'warnings':warnings,'production_approved':False}
