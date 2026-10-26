"""F1 conservative domestic VAT preview. No guessing tax status or deductions.

Every preview is explicitly a SIMULATION. A taxable sale needs a verified profile,
legal sale model, rate class and documented basis before later modules settle money.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SOURCE_FNS = 'https://www.nalog.gov.ru/new2026/'
THRESHOLDS_2026 = {'usn_vat_exempt': 20_000_000, 'usn_5_to_7': 272_500_000, 'usn_limit': 490_500_000}
THRESHOLDS_FUTURE = {2027: 15_000_000, 2028: 10_000_000}
VAT_MODES = {'STANDARD','USN_5','USN_7','EXEMPT'}
REGIMES = {'OSNO','USN_INCOME','USN_PROFIT','AUSN_INCOME','AUSN_PROFIT'}
CENT = Decimal('0.01')


class PreviewInput(BaseModel):
    gross_amount: Decimal = Field(ge=0, max_digits=20, decimal_places=2)
    operation_date: date
    sale_model: Literal['OWN','AGENCY']
    operation_kind: Literal['GOODS','AGENCY_FEE','ADVANCE','REFUND']
    vat_scheme: Literal['STANDARD','USN_5','USN_7','EXEMPT']
    tax_class: Literal['STANDARD_GOODS','REDUCED_10','ZERO','EXEMPT'] = 'STANDARD_GOODS'
    supporting_document: str | None = Field(default=None, max_length=250)
    tax_profile_id: str | None = None
    simulated: Literal[True] = True

    @field_validator('gross_amount')
    @classmethod
    def finite(cls, value):
        if not value.is_finite():
            raise ValueError('Amount must be finite')
        return value

    @model_validator(mode='after')
    def consistency(self):
        if self.operation_date.year != 2026:
            raise ValueError('F1 tax calculation is restricted to the reviewed 2026 ruleset; other years require an approved rules version')
        if self.tax_class in ('REDUCED_10','ZERO','EXEMPT') and not self.supporting_document:
            raise ValueError('Non-standard tax class requires documented legal basis')
        if self.operation_kind == 'AGENCY_FEE' and self.sale_model != 'AGENCY':
            raise ValueError('Agency fee requires AGENCY sale model')
        if self.vat_scheme in ('USN_5','USN_7') and self.tax_class == 'REDUCED_10':
            raise ValueError('Reduced 10% class is not compatible with special USN VAT')
        if self.tax_class == 'ZERO' and self.vat_scheme == 'EXEMPT':
            raise ValueError('Zero rate and exemption are distinct tax statuses')
        return self


def money(n: Decimal) -> Decimal:
    return n.quantize(CENT, rounding=ROUND_HALF_UP)


def preview(req: PreviewInput) -> dict:
    if req.tax_class == 'EXEMPT' or req.vat_scheme == 'EXEMPT':
        status, rate = 'WITHOUT_VAT', None
    elif req.tax_class == 'ZERO':
        status, rate = 'ZERO_RATED', Decimal('0')
    elif req.vat_scheme == 'USN_5':
        status, rate = 'TAXED', Decimal('5')
    elif req.vat_scheme == 'USN_7':
        status, rate = 'TAXED', Decimal('7')
    elif req.tax_class == 'REDUCED_10':
        status, rate = 'TAXED', Decimal('10')
    else:
        status, rate = 'TAXED', Decimal('22')
    vat = money(req.gross_amount * rate / (Decimal('100')+rate)) if rate is not None else Decimal('0.00')
    result = {
        'status': 'SIMULATION', 'vat_status': status, 'vat_scheme': req.vat_scheme,
        'vat_rate': str(rate) if rate is not None else None,
        'gross_amount': str(money(req.gross_amount)),
        'net_amount': str(money(req.gross_amount-vat)), 'vat_amount': str(vat),
        'sale_model': req.sale_model, 'operation_kind': req.operation_kind,
        'tax_class': req.tax_class, 'input_vat_deductible': None,
        'tax_profile_id': req.tax_profile_id,
        'warnings': ['Illustrative VAT calculation only; eligibility, tax obligations, fiscal documents and income tax are not confirmed.'],
        'rule_source': SOURCE_FNS, 'rule_period': '2026+',
        'production_approved': False,
    }
    if req.operation_date.year > 2026:
        result['warnings'].append('Revalidate rate and legal thresholds for this year before production use.')
    if req.operation_kind in ('ADVANCE','REFUND'):
        result['warnings'].append('Advance/refund requires event history and underlying document; F1 does not create tax events.')
    if req.sale_model == 'AGENCY' and req.operation_kind != 'AGENCY_FEE':
        result['warnings'].append('This is the seller transaction, not platform agency-fee revenue.')
    return result
