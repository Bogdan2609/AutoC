"""Pure F5 preview logic, never posts tax returns or creates real tax obligations."""
from __future__ import annotations
import csv
import hashlib
import io
from datetime import date
from decimal import Decimal
from app.modules.finance.tax_engine import PreviewInput, preview

ACTOR_KINDS={
    'PLATFORM':{'OWN_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE'},
    'SELLER':{'AGENCY_SELLER_SALE','PURCHASE','ADVANCE','REFUND','EXPENSE'},
}
DOC_KINDS={
    'OWN_SALE':{'SALE'},'AGENCY_SELLER_SALE':{'SALE','AGENT_REPORT'},
    'AGENCY_PLATFORM_FEE':{'AGENT_REPORT'},'PURCHASE':{'PURCHASE'},
    'ADVANCE':{'ADVANCE'},'REFUND':{'REFUND'},'EXPENSE':{'EXPENSE'},
}
EXPORT_FORMATS={'EVENT_JOURNAL','VAT_SALES_PREVIEW','VAT_PURCHASES_PREVIEW','USN_KUDIR_PREVIEW','AGENCY_REPORT_PREVIEW'}


def calculate_vat(kind:str,gross:Decimal,vat_scheme:str,tax_class:str,operation_date:date,document_ref:str):
    if vat_scheme=='UNRESOLVED':return None,None
    mode=('AGENCY' if kind.startswith('AGENCY_') else 'OWN')
    op='AGENCY_FEE' if kind=='AGENCY_PLATFORM_FEE' else 'ADVANCE' if kind=='ADVANCE' else 'REFUND' if kind=='REFUND' else 'GOODS'
    p=preview(PreviewInput(gross_amount=gross,operation_date=operation_date,sale_model=mode,
        operation_kind=op,vat_scheme=vat_scheme,tax_class=tax_class,
        supporting_document=document_ref))
    return Decimal(p['net_amount']),Decimal(p['vat_amount'])


def signed_amount(row):
    return -row.gross_amount if row.event_kind=='REFUND' else row.gross_amount


def export_lines(books,docs,rows,format_name:str)->tuple[bytes,int]:
    if format_name not in EXPORT_FORMATS:raise ValueError('Unsupported export format')
    d={x.id:x for x in docs}
    def include(row):
        if format_name=='EVENT_JOURNAL':return True
        if format_name=='VAT_SALES_PREVIEW':return row.event_kind in {'OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','ADVANCE','REFUND'}
        if format_name=='VAT_PURCHASES_PREVIEW':return row.event_kind=='PURCHASE'
        if format_name=='USN_KUDIR_PREVIEW':return row.event_kind in {'OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','PURCHASE','EXPENSE','REFUND'}
        return row.event_kind in {'AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE'}
    def safe(s):
        v=str(s or '')
        if v[:1] in {'=','+','-','@','\t','\r'}:return "'"+v
        return v
    buf=io.StringIO(newline='')
    w=csv.writer(buf,delimiter=';',quoting=csv.QUOTE_MINIMAL)
    w.writerow(['status','book','role','document','document_date','event_date','event_kind','amount_rub','vat_scheme','tax_class','net_preview_rub','vat_preview_rub','tax_profile_status'])
    count=0
    for x in sorted(rows,key=lambda r:(r.operation_date,str(r.id))):
        if not include(x):continue
        doc=d[x.document_id]
        w.writerow(['SHADOW_NOT_FOR_FILING',safe(books.book_ref),books.actor_role,safe(doc.document_ref),doc.document_date.isoformat(),x.operation_date.isoformat(),x.event_kind,f'{signed_amount(x):.2f}',x.vat_scheme,x.tax_class,'' if x.net_amount is None else f'{(-x.net_amount if x.event_kind=="REFUND" else x.net_amount):.2f}', '' if x.vat_amount is None else f'{(-x.vat_amount if x.event_kind=="REFUND" else x.vat_amount):.2f}',books.tax_profile_status])
        count+=1
    return ('\ufeff'+buf.getvalue()).encode('utf-8'),count


def digest_bytes(payload:bytes)->str:
    return hashlib.sha256(payload).hexdigest()
