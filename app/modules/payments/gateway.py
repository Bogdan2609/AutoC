"""YooKassa API v3, SBP-only. Webhooks are verified with authoritative GET.

There is NO documented HMAC(secretKey, webhook) for current YooKassa v3:
validate via provider GET (and trusted-proxy IP ranges at ingress), not
an invented signature. RUB-only; multicurrency is a separate future epic.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import httpx
from app.core.config import get_settings

class GatewayError(Exception):
    pass


def money(value) -> str:
    """Amounts must arrive as Decimal or decimal strings, never binary floats."""
    if isinstance(value, (float, bool)):
        raise GatewayError('Floating-point payment amounts are forbidden')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('0.01')):
            raise ValueError('Invalid RUB monetary precision')
        return format(amount, '.2f')
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise GatewayError('Invalid RUB amount') from exc


class YooKassa:
    def config(self):
        s = get_settings()
        if not s.yookassa_shop_id or not s.yookassa_secret_key:
            raise GatewayError('SBP acquiring not configured')
        if s.yookassa_vat_code == 0:
            raise GatewayError('Fiscal VAT code not configured')
        if not s.public_base_url.startswith('https://') and s.app_env != 'test':
            raise GatewayError('Public HTTPS required')
        return s

    async def call(self, method, path, body=None, key=None):
        s = self.config()
        if s.app_env == 'test':
            raise GatewayError('Bank HTTP forbidden in test mode: use FakeYooKassa')
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Idempotence-Key'] = str(key)
        try:
            async with httpx.AsyncClient(auth=(s.yookassa_shop_id,s.yookassa_secret_key),
                                         timeout=httpx.Timeout(15,connect=3)) as c:
                r = await c.request(method,'https://api.yookassa.ru/v3'+path,
                                    json=body,headers=headers)
                r.raise_for_status()
                response = r.json()
        except (httpx.HTTPError,ValueError) as exc:
            # Do not expose URLs, credentials or response bodies in error text.
            raise GatewayError('External payment status uncertain; reconciliation needed') from exc
        if not isinstance(response,dict) or not response.get('id'):
            raise GatewayError('Unexpected gateway reply')
        return response

    async def create(self,order_id,total,items,email):
        s = self.config()
        amount = money(total)
        receipt = []
        computed = Decimal('0.00')
        for name,qty,price in items:
            if not isinstance(qty,int) or isinstance(qty,bool) or qty <= 0:
                raise GatewayError('Invalid receipt item quantity')
            item_price = money(price)
            computed += Decimal(item_price)*qty
            receipt.append({'description':name[:128],'quantity':f'{qty}.000',
                            'amount':{'value':item_price,'currency':'RUB'},
                            'vat_code':s.yookassa_vat_code,'payment_mode':'full_prepayment',
                            'payment_subject':'commodity'})
        if computed != Decimal(amount):
            raise GatewayError('Receipt total does not match SBP payment')
        return await self.call('POST','/payments',key=order_id,body={
            'amount':{'value':amount,'currency':'RUB'},
            'payment_method_data':{'type':'sbp'},'capture':True,
            'confirmation':{'type':'redirect','return_url':
                            f'{s.public_base_url.rstrip("/")}/payment-result?order={order_id}'},
            'description':f'Заказ Autoparts {str(order_id)[:8]}',
            'metadata':{'order_id':str(order_id)},
            'receipt':{'customer':{'email':email},'items':receipt}})

    async def status(self,provider_id):
        return await self.call('GET','/payments/'+str(provider_id))

    async def refund_status(self,provider_id):
        return await self.call('GET','/refunds/'+str(provider_id))

    async def refund(self,payment_id,total,key):
        return await self.call('POST','/refunds',key=key,body={
            'payment_id':payment_id,'amount':{'value':money(total),'currency':'RUB'}})


class FakeYooKassa(YooKassa):
    """CI-only bank double. Never confirms payment unless explicitly settled by tests."""
    def __init__(self):
        self.payments = {}
        self.refunds = {}
        self.idempotency = {}

    def config(self):
        return get_settings()

    async def call(self, method, path, body=None, key=None):
        if get_settings().app_env != 'test':
            raise GatewayError('Fake gateway cannot be used outside tests')
        if method == 'GET':
            if path.startswith('/payments/'):
                item = self.payments.get(path.removeprefix('/payments/'))
            elif path.startswith('/refunds/'):
                item = self.refunds.get(path.removeprefix('/refunds/'))
            else:
                item = None
            if item is None:
                raise GatewayError('Fake provider object not found')
            return dict(item)
        if method != 'POST' or path not in ('/payments','/refunds') or not key:
            raise GatewayError('Invalid fake operation')
        from uuid import uuid5, NAMESPACE_URL
        from copy import deepcopy
        idem = (path,str(key))
        if idem in self.idempotency:
            record,original = self.idempotency[idem]
            if original != body:
                raise GatewayError('Idempotence key reused for different amount/payload')
            return dict(record)
        ref = 'fake-' + str(uuid5(NAMESPACE_URL,path+':'+str(key)))
        record = {'id':ref,'status':'pending','amount':deepcopy(body['amount'])}
        if path == '/payments':
            record.update({'paid':False,'payment_method':{'type':'sbp'},
                           'metadata':deepcopy(body['metadata']),
                           'confirmation':{'confirmation_url':'https://example.invalid/fake-sbp'}})
            self.payments[ref] = record
        else:
            record['payment_id'] = body['payment_id']
            self.refunds[ref] = record
        self.idempotency[idem] = (record,deepcopy(body))
        return dict(record)

    def settle(self, provider_id, *, succeeded: bool = True):
        if get_settings().app_env != 'test':
            raise GatewayError('Fake cannot settle outside test mode')
        record = self.payments[provider_id]
        record['status'] = 'succeeded' if succeeded else 'canceled'
        record['paid'] = succeeded


_fake_gateway = FakeYooKassa()

def gateway():
    s = get_settings()
    return _fake_gateway if s.app_env == 'test' and s.yookassa_fake_enabled else YooKassa()
