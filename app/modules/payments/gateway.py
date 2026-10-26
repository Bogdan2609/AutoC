"""YooKassa API v3 SBP-only. Provider GET verification is required after webhooks."""
import httpx
from decimal import Decimal
from app.core.config import get_settings
class GatewayError(Exception):pass
class YooKassa:
 def config(self):
  s=get_settings()
  if not s.yookassa_shop_id or not s.yookassa_secret_key:raise GatewayError('SBP acquiring not configured')
  if s.yookassa_vat_code==0:raise GatewayError('Fiscal VAT code not configured')
  if not s.public_base_url.startswith('https://') and s.app_env!='test':raise GatewayError('Public HTTPS required')
  return s
 async def call(self,method,path,body=None,key=None):
  s=self.config()
  if s.app_env=='test':
   raise GatewayError('Real bank HTTP is forbidden in test mode; inject a fake adapter')
  headers={'Content-Type':'application/json'}
  if key:headers['Idempotence-Key']=str(key)
  try:
   async with httpx.AsyncClient(auth=(s.yookassa_shop_id,s.yookassa_secret_key),timeout=httpx.Timeout(15,connect=3)) as c:
    r=await c.request(method,'https://api.yookassa.ru/v3'+path,json=body,headers=headers);r.raise_for_status();response=r.json()
  except (httpx.HTTPError,ValueError) as exc:raise GatewayError('External payment status uncertain; reconciliation needed') from exc
  if not isinstance(response,dict) or not response.get('id'):raise GatewayError('Unexpected gateway reply')
  return response
 async def create(self,order_id,total,items,email):
  s=self.config()
  receipt=[{'description':name[:128],'quantity':f'{qty:.3f}','amount':{'value':f'{price:.2f}','currency':'RUB'},
    'vat_code':s.yookassa_vat_code,'payment_mode':'full_prepayment','payment_subject':'commodity'} for name,qty,price in items]
  return await self.call('POST','/payments',key=order_id,body={
   'amount':{'value':f'{total:.2f}','currency':'RUB'},'payment_method_data':{'type':'sbp'},'capture':True,
   'confirmation':{'type':'redirect','return_url':f'{s.public_base_url.rstrip("/")}/payment-result?order={order_id}'},
   'description':f'Заказ Autoparts {str(order_id)[:8]}','metadata':{'order_id':str(order_id)},
   'receipt':{'customer':{'email':email},'items':receipt}})
 async def status(self,provider_id):return await self.call('GET','/payments/'+provider_id)
 async def refund_status(self,provider_id):return await self.call('GET','/refunds/'+provider_id)
 async def refund(self,payment_id,total,key):
  return await self.call('POST','/refunds',key=key,body={'payment_id':payment_id,'amount':{'value':f'{total:.2f}','currency':'RUB'}})

def gateway():return YooKassa()
