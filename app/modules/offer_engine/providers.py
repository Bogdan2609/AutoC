"""Document-based read-only suppliers: separate transports and response normalizers.

Every order/checkout endpoint is intentionally gated until credentials, branch, warehouse,
delivery settings and sandbox supplier-order tests have been verified. No speculative orders.
"""
from dataclasses import dataclass,field
from decimal import Decimal
import re
from xml.etree import ElementTree as ET
import httpx
from app.modules.offer_engine.http_pool import get_client
from app.core.config import get_settings
from app.modules.catalog.normalize import normalize_article,normalize_brand

class Unavailable(Exception):pass
@dataclass(frozen=True)
class Offer:
 code:str
 key:str
 brand:str
 article:str
 price:Decimal
 qty:int|None
 days:int|None
 token:str
 warehouse:str=''
 minimum:int=1
 multiple:int=1
 public_meta:dict=field(default_factory=dict)
def dec(x):
 try:
  v=Decimal(str(x).replace(',','.'))
  if not v.is_finite() or v<0:raise ValueError('Invalid price')
  return v.quantize(Decimal('.01'))
 except Exception as e:raise Unavailable('Invalid supplier price') from e
def as_int(v,default=None):
 try:return int(v)
 except (ValueError,TypeError):return default

def parse_adeo_stock(rest):
 """ADEO reports exact numeric stock or strict lower bounds such as >4, >100.

 A lower bound is safe for quantity checks, but MUST retain its original
 representation so the storefront does not present it as an exact amount.
 """
 raw = str(rest or '').strip()
 if not raw:
  return None, {'stock_raw': raw, 'stock_is_lower_bound': False}
 match = re.fullmatch(r'>\s*([0-9]{1,10})', raw)
 if match:
  lower_bound = int(match.group(1)) + 1
  if lower_bound > 2_147_483_647:
   return None, {'stock_raw': raw, 'stock_is_lower_bound': False}
  return lower_bound, {'stock_raw': raw, 'stock_is_lower_bound': True}
 value = as_int(raw)
 if value is None or not (0 <= value <= 2_147_483_647):
  return None, {'stock_raw': raw[:64], 'stock_is_lower_bound': False}
 return value, {'stock_raw': raw, 'stock_is_lower_bound': False}

def exact(offer,brand,article):return normalize_brand(offer.brand)==normalize_brand(brand) and normalize_article(offer.article)==normalize_article(article)

class PartKom:
 code='partkom'
 async def search(self,brand,article,config=None):
  s=config or get_settings()
  if not (s.supplier_api_enabled and s.partkom_login and s.partkom_password):raise Unavailable('PartKom account not configured')
  cli=get_client()
  response=await cli.get('https://ws.part-kom.ru/v4/search/offers',
   params={'number':article,'find_substitutes':0,'store':0},
   auth=(s.partkom_login,s.partkom_password),timeout=httpx.Timeout(14,connect=3))
  response.raise_for_status();rows=response.json()
  if not isinstance(rows,list):raise Unavailable('PartKom unexpected data')
  found=[]
  for r in rows:
   if not isinstance(r,dict) or r.get('detailGroup','Original') not in ('Original','1',1):continue
   b=str(r.get('maker',''));a=str(r.get('number',''))
   if normalize_brand(b)!=normalize_brand(brand) or normalize_article(a)!=normalize_article(article):continue
   maker=r.get('makerId');provider=r.get('providerId');placement=r.get('placementId','')
   if maker is None or provider is None:continue
   minimum=max(1,as_int(r.get('minQuantity'),1))
   found.append(Offer(self.code,f'{maker}:{normalize_article(a)}:{provider}:{placement}',b,a,dec(r['price']),as_int(r.get('quantity')),
     as_int(r.get('guaranteedDays'),as_int(r.get('expectedDays'))),str(provider),str(placement),minimum,1,
     {'maker_id':str(maker),'provider_id':str(provider),'placement_id':str(placement),'success_percent':r.get('statSuccessPercent'),'pack_multiple_unverified':True}))
  return found

class Forum:
 code='forum-auto'
 async def search(self,brand,article,config=None):
  s=config or get_settings()
  if not (s.supplier_api_enabled and s.forum_login and s.forum_password):raise Unavailable('Forum-Auto account not configured')
  # REST v2 GET carries credentials in query: do not record the full URL or response logs.
  cli=get_client()
  response=await cli.get('https://api.forum-auto.ru/v2/listgoods',
    params={'login':s.forum_login,'pass':s.forum_password,'art':article,'br':brand,'cross':0},
    timeout=httpx.Timeout(14,connect=3))
  response.raise_for_status();rows=response.json()
  if not isinstance(rows,list):raise Unavailable('Forum-Auto unexpected data')
  found=[]
  for r in rows:
   if not isinstance(r,dict) or not r.get('gid'):continue
   b=str(r.get('brand',''));a=str(r.get('art',''))
   if normalize_brand(b)!=normalize_brand(brand) or normalize_article(a)!=normalize_article(article):continue
   multiple=max(1,as_int(r.get('kr'),1))
   found.append(Offer(self.code,str(r['gid']),b,a,dec(r['price']),as_int(r.get('num')),as_int(r.get('d_deliv')),
      str(r['gid']),str(r.get('whse','')),multiple,multiple,{'returnable':bool(as_int(r.get('is_returnable'),0))}))
  return found

def require_rub(currency: str) -> None:
 """A supplier price must have an explicitly supported currency.

 ADEO price is documented in `currency` (for example `руб`). An unknown
 currency MUST be an error, not an empty or RUB-typed offer.
 """
 currency = str(currency or '').strip().casefold()
 if currency not in ('руб', 'руб.', 'rub', 'rur', 'rubles', 'рубли', '₽', '643'):
  raise Unavailable('ADEO price currency is not confirmed as RUB')


class Adeo:
 code='adeo'
 async def search(self,brand,article,config=None):
  s=config or get_settings()
  if not (s.supplier_api_enabled and s.adeo_login and s.adeo_password):raise Unavailable('ADEO account not configured')
  root=ET.Element('message');p=ET.SubElement(root,'param')
  for tag,value in [('action','price'),('login',s.adeo_login),('password',s.adeo_password),('code',article),('brand',brand),('crosses','disallow')]:ET.SubElement(p,tag).text=value
  cli=get_client()
  response=await cli.post('https://xml.adeo.pro/pricedetals2.php',
    data={'xml':ET.tostring(root,encoding='unicode')},timeout=httpx.Timeout(20,connect=3))
  response.raise_for_status()
  if len(response.content)>2_000_000:raise Unavailable('Adeo XML too large')
  from defusedxml.ElementTree import fromstring
  rows=fromstring(response.content).findall('.//detail');found=[]
  for node in rows:
   r={child.tag:(child.text or '') for child in node}
   b=r.get('producer','');a=r.get('code','')
   if normalize_brand(b)!=normalize_brand(brand) or normalize_article(a)!=normalize_article(article):continue
   if not r.get('b_id'):continue
   require_rub(r.get('currency'))
   multi=max(1,as_int(r.get('minOrderCount'),1))
   key=f"{r.get('id','')}:{r.get('cella_id','')}:{r.get('n_file','')}"
   qty,stock_meta=parse_adeo_stock(r.get('rest'))
   found.append(Offer(self.code,key,b,a,dec(r['price']),qty,as_int(r.get('delivery')),
      r['b_id'],r.get('stock',''),multi,multi,
      {'refusal_percent':r.get('PercentRefuse'),'delivery_min_days':as_int(r.get('deliverydays')), 'delivery_max_days':as_int(r.get('delivery')),'currency':'RUB',**stock_meta}))
  return found

class Rossko:
 code='rossko'
 async def search(self,brand,article,config=None):
  s=config or get_settings()
  if not (s.supplier_api_enabled and s.rossko_key1 and s.rossko_key2 and s.rossko_delivery_id):
   raise Unavailable('ROSSKO SOAP credentials/delivery not configured')
  # WSDL-defined SOAP binding avoids hand-crafted requests. Until confirmed on an
  # account, leave SUPPLIER_API_ENABLED=false; NEVER call GetCheckout here.
  from zeep import AsyncClient
  from zeep.transports import AsyncTransport
  from zeep.helpers import serialize_object
  import httpx
  wsdl='https://api.rossko.ru/service/v2.1/GetSearch?wsdl'
  async with httpx.AsyncClient(timeout=httpx.Timeout(18,connect=4)) as http_client:
   transport=AsyncTransport(client=http_client,wsdl_client=httpx.Client(timeout=6))
   client=AsyncClient(wsdl,transport=transport)
   params={'KEY1':s.rossko_key1,'KEY2':s.rossko_key2,'text':f'{brand} {article}','delivery_id':s.rossko_delivery_id}
   if s.rossko_address_id:params['address_id']=s.rossko_address_id
   try:raw=await client.service.GetSearch(**params)
   except Exception as exc:raise Unavailable('ROSSKO SOAP GetSearch failed') from exc
  obj=serialize_object(raw,target_cls=dict)
  return parse_rossko_search_result(obj,brand,article)


def parse_rossko_search_result(obj,brand,article):
  """Convert ROSSKO GetSearch to exact SKU offers.

  ROSSKO's real zeep result is ``{'success': True, 'PartsList': ...}``;
  accepting only the capitalized SOAP alias ``Success`` would silently treat every
  successful result as an empty search (cache status=empty).
  """
  if not isinstance(obj,dict):raise Unavailable('Unexpected ROSSKO SOAP result')
  obj=obj.get('SearchResult',obj)
  if not isinstance(obj,dict):raise Unavailable('Unexpected ROSSKO SearchResult')
  success=obj.get('success',obj.get('Success',''))
  if str(success).strip().lower() not in ('true','1'):
   # A failed upstream request must never enter the negative availability cache.
   raise Unavailable('ROSSKO GetSearch returned non-success status')
  parts=obj.get('PartsList')
  if parts is None:parts={}
  if not isinstance(parts,dict):raise Unavailable('ROSSKO PartsList invalid')
  entries=parts.get('Part') or []
  if isinstance(entries,dict):entries=[entries]
  if not isinstance(entries,list):raise Unavailable('ROSSKO Part entries invalid')
  result=[]
  for part in entries:
   if not isinstance(part,dict):continue
   b=str(part.get('brand',''));a=str(part.get('partnumber',''))
   if normalize_brand(b)!=normalize_brand(brand) or normalize_article(a)!=normalize_article(article):continue
   stocks=(part.get('stocks') or {}).get('stock') or []
   if isinstance(stocks,dict):stocks=[stocks]
   if not isinstance(stocks,list):raise Unavailable('ROSSKO stock entries invalid')
   for stock in stocks:
    if not isinstance(stock,dict):continue
    stock_id=str(stock.get('id',''));guid=str(part.get('guid',''))
    if not stock_id or not guid:continue
    multiple=max(1,as_int(stock.get('multiplicity'),1))
    result.append(Offer('rossko',f'{guid}:{stock_id}',b,a,dec(stock.get('price')),
     as_int(stock.get('count')),as_int(stock.get('delivery')),f'{guid}:{stock_id}',stock_id,multiple,multiple,
     {'stock_type':stock.get('type'),'extra_days':stock.get('extra')}))
  return result

PROVIDERS={'partkom':PartKom(),'forum-auto':Forum(),'adeo':Adeo(),'rossko':Rossko()}
async def verify(code,key,brand,article,quantity):
 if code not in PROVIDERS:raise Unavailable('Unknown supplier')
 records=await PROVIDERS[code].search(brand,article)
 offer=next((x for x in records if x.key==key and exact(x,brand,article)),None)
 if not offer or offer.qty is None or offer.qty<quantity or quantity<offer.minimum or quantity%offer.multiple:
  raise Unavailable('Selected offer is unavailable or quantity violates packaging rules')
 return offer
