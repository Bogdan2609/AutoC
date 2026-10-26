import {useMemo,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import type {ColumnDef} from '@tanstack/react-table'
import {Link,useSearchParams} from 'react-router-dom'
import {AlertTriangle,ArrowUpRight,Plus,Settings2} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Tabs,TabsContent,TabsList,TabsTrigger} from '@/components/ui/tabs'
import {DataGrid,ErrorBox,Heading,Loading,Status} from '@/components/page'
import {api,canAccessPath,MONITOR,OPS,WORKSPACE} from '@/lib/api'
import {dateText} from '@/lib/utils'
import {EntityDialog} from '@/features/EntityDialog'
import {entities} from '@/features/entity-config'
import type {EntityRecord} from '@/features/entity-config'

type SectionName='catalog'|'integrations'|'warehouses'|'marketplace'|'finance'|'analytics'|'system'
type Source={id:string;name:string;path:string;dataKey?:string;cols:{key:string;label:string;format?:'status'|'date'|'boolean'}[];entity?:string;note?:string;keyId?:string}
type Group={title:string;description:string;items:Source[]}
const c=(key:string,label:string,format?:Source['cols'][number]['format'])=>({key,label,format})
const finance='/api/v1/backoffice/finance',ce='/api/v1/backoffice/catalog-v021'
const groups:Record<SectionName,Group>={
 catalog:{title:'Управление мастер-каталогом',description:'Бренды, иерархия категорий, характеристики, варианты и история семейств. Публикация товаров проходит отдельную проверку.',items:[
  {id:'brands',name:'Бренды',path:`${WORKSPACE}/brands`,entity:'brands',cols:[c('name','Производитель'),c('slug','URL')]},
  {id:'categories',name:'Категории',path:`${ce}/categories`,dataKey:'items',entity:'categories',cols:[c('name','Категория'),c('slug','URL'),c('parent_id','Родитель'),c('position','Порядок'),c('active','Активна','boolean')]},
  {id:'attributes',name:'Характеристики',path:`${ce}/attributes`,dataKey:'items',entity:'attributes',cols:[c('code','Код'),c('name','Название'),c('data_type','Тип'),c('unit','Единица'),c('filterable','Фильтр','boolean')]},
  {id:'families',name:'Семейства',path:'/api/v1/backoffice/catalog-editor/families',dataKey:'items',entity:'families',cols:[c('name','Название'),c('slug','URL'),c('kind','Тип')]}]},
 integrations:{title:'Управление поставщиками',description:'Поставщики, независимые API-аккаунты и курьерские адреса получения. Изменения не запускают закупку автоматически.',items:[
  {id:'suppliers',name:'Поставщики',path:`${WORKSPACE}/suppliers`,entity:'suppliers',cols:[c('name','Название'),c('code','Код')]},
  {id:'accounts',name:'API-аккаунты',path:`${WORKSPACE}/accounts`,entity:'accounts',cols:[c('label','Аккаунт'),c('supplier','Поставщик'),c('merchant','Продавец'),c('enabled','Активен','boolean'),c('read_enabled','Поиск','boolean'),c('procurement_enabled','Закупка','boolean'),c('requests_per_hour','Лимит/час')]},
  {id:'destinations',name:'Адреса доставки',path:`${WORKSPACE}/destinations`,entity:'destinations',cols:[c('account','API-аккаунт'),c('city','Город'),c('node','Наш склад'),c('external_delivery_id','Способ'),c('verified','Подтверждён','boolean'),c('enabled','Разрешён','boolean')]},
  {id:'receiving',name:'Готовность приёмки',path:`${MONITOR}/receiving-readiness`,dataKey:'items',cols:[c('supplier','Поставщик'),c('account','Аккаунт'),c('city','Город'),c('node','Получатель'),c('status','Проверка','status')],note:'Это проверка конфигурации, а не живое подтверждение склада поставщика'}]},
 warehouses:{title:'Склады и логистика',description:'Физические остатки, собственные точки приёмки, внутренние маршруты и готовность курьерских поставок.',items:[
  {id:'nodes',name:'Склады и ПВЗ',path:`${WORKSPACE}/nodes`,entity:'nodes',cols:[c('code','Код'),c('name','Точка'),c('city','Город'),c('address','Адрес'),c('kind','Тип'),c('active','Активна','boolean'),c('receiving_contact_configured','Контакты','boolean')]},
  {id:'inventory',name:'Остатки',path:`${WORKSPACE}/inventory`,entity:'stock',cols:[c('sku','SKU'),c('node','Склад'),c('city','Город'),c('on_hand','Факт'),c('reserved','Резерв'),c('available','Доступно')]},
  {id:'routes',name:'Маршруты',path:`${WORKSPACE}/routes`,entity:'routes',cols:[c('source','Откуда'),c('target','Куда'),c('transit_days','В пути, дн.'),c('handling_days','Обработка'),c('fee','Тариф'),c('active','Активен','boolean')]},
  {id:'receiving',name:'Приёмка',path:`${MONITOR}/receiving-readiness`,dataKey:'items',cols:[c('supplier','Поставщик'),c('node','Наш склад'),c('city','Город'),c('status','Проверка','status')]}]},
 marketplace:{title:'Маркетплейс',description:'Продавцы, магазины, независимые предложения и индивидуальные б/у запчасти.',items:[
  {id:'merchants',name:'Продавцы',path:`${WORKSPACE}/merchants`,entity:'merchants',cols:[c('name','Продавец'),c('slug','Код'),c('status','Статус','status'),c('platform_owner','Наш','boolean')]},
  {id:'stores',name:'Магазины',path:`${WORKSPACE}/stores`,entity:'stores',cols:[c('name','Магазин'),c('merchant','Продавец'),c('slug','URL'),c('status','Статус','status')]},
  {id:'offers',name:'Предложения',path:`${WORKSPACE}/offers`,entity:'offers',cols:[c('sku','SKU'),c('store','Магазин'),c('merchant','Продавец'),c('price','Розница, ₽'),c('on_hand','Остаток'),c('status','Статус','status')]},
  {id:'used',name:'Б/у детали',path:`${WORKSPACE}/used`,entity:'used',cols:[c('title','Запчасть'),c('merchant','Продавец'),c('condition','Состояние'),c('mileage_km','Пробег'),c('status','Статус','status')]}]},
 finance:{title:'Финансы',description:'Юридические лица, налоги, тарифы и моделирование. Помеченные SHADOW действия не инициируют движение денег.',items:[
  {id:'entities',name:'Юрлица',path:`${finance}/overview`,dataKey:'entities',entity:'legal_entities',cols:[c('name','Название'),c('entity_type','Тип'),c('inn','ИНН'),c('verification_status','Проверка','status')]},
  {id:'tax',name:'Налоговые профили',path:`${finance}/overview`,dataKey:'profiles',entity:'tax_profiles',cols:[c('regime','Режим'),c('vat_scheme','НДС'),c('valid_from','С'),c('status','Статус','status')]},
  {id:'pricing',name:'Наценки и комиссии',path:`${finance}/pricing/overview`,dataKey:'pricing_rules',entity:'pricing_rules',cols:[c('name','Правило'),c('scope_kind','Область'),c('markup_percent','Наценка, %'),c('state','Статус','status')],note:'Проверяйте версию и статус применения правил'},
  {id:'fees',name:'Расходы',path:`${finance}/pricing/overview`,dataKey:'fee_schedules',entity:'fee_schedules',cols:[c('name','Название'),c('fee_kind','Тип'),c('percent','Процент'),c('state','Статус','status')]},
  {id:'ledger',name:'Взаиморасчёты · SHADOW',path:`${finance}/ledger/overview`,dataKey:'books',cols:[c('code','Книга'),c('name','Название'),c('state','Статус','status')],note:'Симуляция проводок, не реальные банковские балансы'},
  {id:'payments',name:'СБП и чеки · SHADOW',path:`${finance}/payments-sim/overview`,dataKey:'payments',cols:[c('id','Платёж'),c('state','Статус','status'),c('amount','Сумма')],note:'Только имитация оплаты; не факт поступления денег'},
  {id:'registers',name:'Регистры · SHADOW',path:`${finance}/tax-registers-sim/overview`,dataKey:'books',cols:[c('code','Код'),c('name','Регистр'),c('state','Статус','status')],note:'Только расчётные налоговые регистры'}]},
 analytics:{title:'Аналитика',description:'Сравнимые исторические наблюдения, не подтверждённая выручка или текущее наличие.',items:[
  {id:'quotes',name:'История цен поставщиков',path:'/api/v1/backoffice/analytics/a3/supplier-quotes',dataKey:'rows',cols:[c('brand','Бренд'),c('article','Артикул'),c('name','Товар'),c('supplier_count','Источники'),c('historical_low_rub','Мин., ₽'),c('historical_high_rub','Макс., ₽')],note:'Исторические цены не заменяют актуальную API-проверку перед оплатой'},
  {id:'suppliers',name:'Статистика API',path:'/api/v1/backoffice/analytics/a2/suppliers',dataKey:'rows',cols:[c('supplier','Поставщик'),c('api_requests_logged','Запросов'),c('avg_logged_duration_ms','Время, мс'),c('offer_snapshots_in_window','Снимков')]},
  {id:'products',name:'Популярность товаров',path:'/api/v1/backoffice/analytics/a2/products',dataKey:'rows',cols:[c('article','Артикул'),c('name','Название'),c('orders_created','Заказов'),c('final_order_value_rub','Стоимость, ₽')]},
  {id:'merchants',name:'Аналитика продавцов',path:'/api/v1/backoffice/analytics/a3/merchants',dataKey:'rows',cols:[c('merchant','Продавец'),c('offers_total','Предложений'),c('orders_created','Заказы')]},
  {id:'customers',name:'Клиенты',path:'/api/v1/backoffice/analytics/a2/customers',dataKey:'rows',cols:[c('customer_id','Клиент'),c('orders_count','Заказы'),c('total_value_rub','Сумма')]}]},
 system:{title:'Мониторинг системы',description:'Ошибки, фоновые задания, попытки закупок и конфигурация. Никаких автоматических повторов.',items:[
  {id:'jobs',name:'Фоновые задания',path:`${MONITOR}/jobs?page=1&size=100`,dataKey:'items',cols:[c('kind','Тип'),c('status','Состояние','status'),c('attempts','Попыток'),c('due_at','По плану','date'),c('error_present','Ошибка','boolean')]},
  {id:'purchases',name:'Закупки поставщиков',path:`${MONITOR}/purchases?page=1&size=100`,dataKey:'items',cols:[c('status','Состояние','status'),c('order_id','Заказ'),c('error_code','Ошибка'),c('created_at','Создан','date')],note:'Просмотр: повторная закупка отсюда недоступна'},
  {id:'refunds',name:'Возвраты',path:`${OPS}/refunds`,cols:[c('id','Возврат'),c('payment_id','Платёж'),c('amount','Сумма'),c('status','Статус','status')]},
  {id:'configuration',name:'Конфигурация',path:`${MONITOR}/configuration`,cols:[c('field','Поле'),c('value','Значение')]}]}
}
function display(v:unknown,format?:Source['cols'][number]['format']){
 if(v===null||v===undefined||v==='')return '—'
 if(format==='status')return <Status value={String(v)}/>
 if(format==='date')return dateText(String(v))
 if(format==='boolean')return <Badge variant={v?'success':'secondary'}>{v?'Да':'Нет'}</Badge>
 if(typeof v==='object')return Array.isArray(v)?`${v.length} знач.`:'Детали'
 return String(v)
}
export function ResourceTable({source,filter}:{source:Source;filter?: (r:EntityRecord)=>boolean}){
 const [editing,setEditing]=useState<EntityRecord|undefined>(undefined)
 const [creating,setCreating]=useState(false)
 const [search,setSearch]=useState('')
 const query=useQuery({queryKey:['resource',source.id,source.path],queryFn:()=>api<unknown>(source.path)})
 const rows=useMemo<EntityRecord[]>(()=>{
  if(query.data===undefined)return []
  if(source.id==='configuration'&&typeof query.data==='object'&&!Array.isArray(query.data))return Object.entries(query.data as EntityRecord).map(([field,value])=>({field,value}))
  const root=source.dataKey&&query.data!==null&&typeof query.data==='object'&&!Array.isArray(query.data)?(query.data as EntityRecord)[source.dataKey]:query.data
  return Array.isArray(root)?root.filter((r):r is EntityRecord=>!!r&&typeof r==='object'&&!Array.isArray(r)).filter(r=>!filter||filter(r)):[]
 },[query.data,source,filter])
 const filtered=useMemo(()=>search?rows.filter(r=>source.cols.some(c=>String(r[c.key]||'').toLocaleLowerCase('ru-RU').includes(search.toLocaleLowerCase('ru-RU')))):rows,[rows,search,source])
 const config=source.entity?entities[source.entity]:null
 const columns=useMemo<ColumnDef<EntityRecord>[]>(()=>{
  const arr:ColumnDef<EntityRecord>[]=source.cols.map(c=>({accessorKey:c.key,header:c.label,cell:({row})=><span className="break-words">{display(row.original[c.key],c.format)}</span>}))
  if(config?.edit&&canAccessPath(source.path,true))arr.push({id:'actions',header:'',cell:({row})=><Button type="button" size="sm" variant="outline" onClick={()=>setEditing(row.original)}><Settings2 size={14}/> Изменить</Button>})
  if(source.entity==='offers'&&canAccessPath(`${WORKSPACE}/inventory`,true))arr.push({id:'stock',header:'Остаток',cell:({row})=><Button size="sm" variant="ghost" onClick={()=>setEditing({...row.original,__stock:true})}>Учёт остатков</Button>})
  return arr
 },[source,config])
 const root=query.data!==null&&typeof query.data==='object'&&!Array.isArray(query.data)?query.data as EntityRecord:null
 const total=typeof root?.total==='number'?root.total:null
 const actionable=Boolean(config?.create)&&source.id!=='inventory'&&canAccessPath(source.path,true)
 return <div className="space-y-4">
  <div className="flex flex-wrap justify-between gap-3"><input type="search" aria-label={`Поиск: ${source.name}`} value={search} onChange={e=>setSearch(e.target.value)} className="h-10 w-full max-w-xs rounded-lg border border-input bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring" placeholder="Фильтр в загруженных строках"/><div className="flex gap-2"><Button size="sm" variant="outline" onClick={()=>void query.refetch()}>Обновить</Button>{actionable&&<Button size="sm" onClick={()=>setCreating(true)}><Plus size={15}/> Создать</Button>}</div></div>
  {source.note&&<p className="flex gap-2 rounded-lg border border-border bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground"><AlertTriangle size={15} className="shrink-0"/>{source.note}</p>}
  {query.isPending?<Loading/>:query.error?<ErrorBox error={query.error} onRetry={()=>void query.refetch()}/>:<DataGrid data={filtered} columns={columns}/>}
  {total!==null&&total>rows.length&&<p className="text-xs text-muted-foreground">Показано {rows.length} из {total}. API ограничивает текущую выдачу. Это не весь список.</p>}
  {!total&&rows.length>=100&&<p className="text-xs text-muted-foreground">API может ограничивать список: поиск выполняется только по полученным строкам.</p>}
  {creating&&source.entity&&<EntityDialog entity={source.entity} onClose={()=>setCreating(false)}/>}
  {editing&&source.entity&&<EntityDialog key={String(editing.id)+(editing.__stock?'stock':'edit')} entity={editing.__stock?'stock':source.entity} row={editing} stockNode={String(editing.node_id||'')} onClose={()=>setEditing(undefined)}/>}
 </div>
}
export function Section({section}:{section:SectionName}){
 const group=groups[section];const [params,setParams]=useSearchParams();const chosen=params.get('tab');const active=group.items.some(i=>i.id===chosen)?chosen!:group.items[0].id
 return <><Heading title={group.title} description={group.description}/>{section==='finance'&&<Link to="/finance/studio" className="mb-5 inline-flex rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted">Финансовый центр и SHADOW →</Link>}{section==='system'&&<Link to="/system/audit" className="mb-5 inline-flex rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted">Приёмка переноса и безопасность →</Link>}<Tabs value={active} onValueChange={v=>setParams({tab:v})}><TabsList className="flex flex-wrap">{group.items.map(i=><TabsTrigger key={i.id} value={i.id}>{i.name}</TabsTrigger>)}</TabsList>{group.items.map(i=><TabsContent key={i.id} value={i.id}><ResourceTable source={i}/></TabsContent>)}</Tabs><p className="mt-6 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><ArrowUpRight size={13}/> <Link className="underline" to={section==='catalog'?'/catalog':section==='integrations'?'/suppliers':'/'}>Вернуться к основному разделу</Link>. Некоторые специализированные сценарии ещё проходят перенос; старый `/operator` остаётся резервом до полной проверки.</p></>
}
