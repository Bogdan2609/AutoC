import {useQuery} from '@tanstack/react-query'
import {ArrowRight, ArrowUpRight, Check, ChevronRight, CircleAlert, Clock3, Package, ShieldCheck, Truck, ShoppingBag, Workflow} from 'lucide-react'
import {Link} from 'react-router-dom'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Separator} from '@/components/ui/separator'
import {ErrorBox,Loading} from '@/components/page'
import {api,MONITOR,WORKSPACE} from '@/lib/api'
import type {Summary,Attention,Configuration,Readiness} from '@/lib/types'

const number=(value:number)=>value.toLocaleString('ru-RU')
export function Dashboard(){
 const summary=useQuery({queryKey:['summary'],queryFn:()=>api<Summary>(`${WORKSPACE}/summary`)})
 const attention=useQuery({queryKey:['attention'],queryFn:()=>api<Attention>(`${MONITOR}/attention`)})
 const config=useQuery({queryKey:['config'],queryFn:()=>api<Configuration>(`${MONITOR}/configuration`)})
 const readiness=useQuery({queryKey:['readiness'],queryFn:()=>api<Readiness>(`${MONITOR}/receiving-readiness`)})
 if(summary.isPending||attention.isPending||config.isPending)return <><Intro/><Loading/></>
 if(summary.error||attention.error||config.error)return <><Intro/><ErrorBox error={summary.error||attention.error||config.error} onRetry={()=>{void summary.refetch();void attention.refetch();void config.refetch()}}/></>
 const metrics=[
  {index:'01',name:'Заказы',value:summary.data.orders,description:'Все состояния заказа',icon:ShoppingBag,to:'/orders'},
  {index:'02',name:'Требуют проверки',value:attention.data.review_orders,description:'Ожидают решения оператора',icon:Clock3,to:'/orders?review=true'},
  {index:'03',name:'Товары в работе',value:summary.data.review_products,description:'Черновики и модерация',icon:Package,to:'/catalog'},
  {index:'04',name:'Закупки под контролем',value:attention.data.review_purchases,description:'Без автоматического повтора',icon:Truck,to:'/system?tab=purchases'}
 ]
 const queue=[
  {label:'Ошибки фоновых заданий',value:attention.data.failed_jobs,to:'/system?tab=jobs',hint:'Проверьте причину завершения'},
  {label:'Просроченные задания',value:attention.data.overdue_queued_jobs,to:'/system?tab=jobs',hint:'Ожидают выполнения дольше нормы'},
  {label:'Возвраты на проверке',value:attention.data.review_refunds,to:'/orders?tab=refunds',hint:'Нужна сверка статуса'},
  {label:'Ошибки API поставщиков',value:summary.data.api_errors,to:'/suppliers',hint:'Исторические записи, не текущие сбои'},
  {label:'Маршруты приёмки с замечаниями',value:readiness.data?.action_required??null,to:'/suppliers',hint:'Конфигурационная готовность'}
 ]
 const flags=[
  {label:'API поставщиков',enabled:config.data.supplier_api_enabled},
  {label:'Автозакупка',enabled:config.data.supplier_procurement_enabled},
  {label:'Реальные checkout',enabled:config.data.real_supplier_checkout_enabled},
  {label:'СБП: реквизиты',enabled:config.data.sbp_credentials_configured},
  {label:'Финансы: верификация',enabled:config.data.financial_operations_verified}
 ]
 return <div className="fade-in"><Intro/>
  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(({index,name,value,description,icon:Icon,to})=><Link key={index} to={to} className="group block h-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Card className="relative h-full min-h-[194px] overflow-hidden transition-[box-shadow,border-color] duration-200 group-hover:border-foreground/30 group-hover:shadow-sm"><CardContent className="flex h-full flex-col justify-between gap-7 p-5 md:p-5"><div className="flex items-start justify-between"><span className="text-[10px] font-semibold tracking-[.17em] text-muted-foreground">{index} / METRIC</span><Icon size={20} strokeWidth={1.5} className="text-muted-foreground transition-colors group-hover:text-foreground"/></div><div><p className="text-[35px] font-semibold leading-none tracking-[-.06em] tabular-nums md:text-[42px]">{number(value)}</p><div className="mt-5 flex items-center justify-between gap-2"><div><p className="text-[13px] font-semibold">{name}</p><p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{description}</p></div><ArrowUpRight size={17} className="shrink-0 text-muted-foreground group-hover:text-foreground"/></div></div></CardContent></Card></Link>)}</div>
  <div className="mt-5 grid items-start gap-5 xl:grid-cols-[minmax(0,1.53fr)_minmax(340px,1fr)]">
   <Card className="overflow-hidden"><CardHeader className="flex flex-row items-start justify-between gap-4"><div><div className="mb-2 flex items-center gap-2"><span className="size-1.5 rounded-full bg-foreground"/><span className="text-[10px] font-bold uppercase tracking-[.16em] text-muted-foreground">ACTION REQUIRED</span></div><CardTitle className="text-lg">Очередь внимания</CardTitle><CardDescription>Ситуации, требующие действий, а не просто просмотра статистики.</CardDescription></div><Button asChild variant="outline" size="sm"><Link to="/system">Все события <ArrowUpRight size={14}/></Link></Button></CardHeader>
    <Separator/>
    <CardContent className="p-0 md:p-0">{queue.map((item,i)=><Link key={item.label} to={item.to} className="group flex min-h-[77px] items-center gap-4 border-b border-border px-5 py-4 transition-colors last:border-0 hover:bg-muted/35 md:px-6"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-background text-xs font-semibold tabular-nums text-muted-foreground">{String(i+1).padStart(2,'0')}</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{item.label}</p><p className="mt-1 text-xs text-muted-foreground">{item.hint}</p></div><div className="flex items-center gap-3"><span className="text-lg font-semibold tabular-nums">{item.value===null?'—':number(item.value)}</span><ChevronRight size={16} className="text-muted-foreground transition-transform group-hover:translate-x-0.5"/></div></Link>)}</CardContent>
   </Card>
   <div className="space-y-5"><Card><CardHeader><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[.17em] text-muted-foreground">INTEGRATIONS / STATUS</span><Workflow size={17} className="text-muted-foreground"/></div><CardTitle className="mt-2 text-lg">Конфигурация платформы</CardTitle><CardDescription>Текущие настройки, а не подтверждение успешных реальных операций.</CardDescription></CardHeader><CardContent className="space-y-0">{flags.map(x=><div className="flex min-h-[43px] items-center justify-between gap-4 border-t border-border text-[13px]" key={x.label}><span className="text-muted-foreground">{x.label}</span><Badge variant={x.enabled?'success':'secondary'}><span className="size-1.5 rounded-full bg-current"/>{x.enabled?'Включено':'Отключено'}</Badge></div>)}<div className="mt-4 flex items-start gap-2 rounded-lg bg-muted/65 p-3 text-xs leading-relaxed text-muted-foreground"><CircleAlert size={16} className="mt-0.5 shrink-0"/><span>{config.data.warning}</span></div></CardContent></Card>
    <Card><CardHeader><div className="flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[.17em] text-muted-foreground">DELIVERY / VALIDATION</span><Truck size={18} className="text-muted-foreground"/></div><CardTitle className="mt-2 text-lg">Курьерская приёмка</CardTitle><CardDescription>Проверка настроек маршрутов поставщиков.</CardDescription></CardHeader><CardContent>{readiness.isPending?<Loading/>:readiness.error?<ErrorBox error={readiness.error}/>:<><div className="grid grid-cols-2 gap-3"><div className="rounded-lg border border-border p-4"><p className="text-3xl font-semibold tabular-nums">{number(readiness.data?.ready??0)}</p><p className="mt-2 text-xs text-muted-foreground">Готовых настроек</p></div><div className="rounded-lg border border-border p-4"><p className="text-3xl font-semibold tabular-nums">{number(readiness.data?.action_required??0)}</p><p className="mt-2 text-xs text-muted-foreground">Нужна настройка</p></div></div><p className="mt-3 text-xs leading-relaxed text-muted-foreground">{readiness.data?.note}</p></>}</CardContent></Card>
   </div>
  </div>
  <div className="mt-5 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-muted/35 px-5 py-4"><div className="flex items-center gap-3"><ShieldCheck size={19} strokeWidth={1.6}/><p className="text-xs leading-relaxed text-muted-foreground"><b className="text-foreground">Контроль операций.</b> Данные отображаются из FastAPI. Повторы закупок, оплат и изменение статусов не выполняются с главной страницы.</p></div><Link className="inline-flex items-center gap-2 text-xs font-semibold hover:underline" to="/suppliers">Проверить поставщиков <ArrowRight size={15}/></Link></div>
 </div>
}
function Intro(){return <div className="relative mb-6 overflow-hidden rounded-2xl border border-border bg-card"><div className="adm-dotfield pointer-events-none absolute inset-y-0 right-0 w-[43%] opacity-60" aria-hidden="true"/><div className="relative z-10 flex flex-wrap items-end justify-between gap-6 px-6 py-8 md:px-8 md:py-9"><div><div className="mb-5 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[.18em] text-muted-foreground"><span className="inline-block h-px w-7 bg-foreground"/> AUTOCODEX / OPERATIONS</div><h1 className="text-[32px] font-semibold leading-[1.06] tracking-[-.055em] md:text-[43px]">Всё под контролем<span className="text-muted-foreground">.</span></h1><p className="mt-3 max-w-[550px] text-sm leading-relaxed text-muted-foreground">Состояние заказов, поставок и инфраструктуры — в одном рабочем пространстве. Только подтверждённые данные из системы.</p></div><div className="flex items-center gap-2 rounded-full border border-border bg-background px-3.5 py-2 text-xs font-medium"><Check size={14}/> Операционная панель</div></div></div>}
