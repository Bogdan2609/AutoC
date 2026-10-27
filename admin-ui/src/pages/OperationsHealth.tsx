import {useQuery} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {Activity,AlertTriangle,CheckCircle2,Clock3,RefreshCw,ShieldCheck} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Tabs,TabsContent,TabsList,TabsTrigger} from '@/components/ui/tabs'
import {Heading,Loading,ErrorBox} from '@/components/page'
import {api} from '@/lib/api'

const ROOT='/api/v1/backoffice/operations-health'
type Summary={read_only:boolean;live_money_approved:boolean;jobs:Record<string,number>;payments:Record<string,number>;refunds:Record<string,number>;purchases:Record<string,number>}
type Job={id:string;kind:string;status:string;attempts:number;severity:string;code:string;error_type:string|null;retry_safe:boolean}
type Item={id:string;status:string;next_action?:string;manual_review_required?:boolean;retry_create_allowed?:boolean;provider_reference_present?:boolean}
type Factory={read_only:boolean;live_supplier_order_dispatch:boolean;accounts:{id:string;label:string;supplier:string;enabled:boolean;read_enabled:boolean;requests_per_hour:number;destinations:number;verified_courier_routes:number}[]}

function ReadRows({title,rows}:{title:string;rows:Item[]}){
 return <Card><CardHeader><CardTitle>{title}</CardTitle><CardDescription>Только сохранённые состояния. Повтор внешней операции отсюда невозможен.</CardDescription></CardHeader><CardContent><div className="space-y-2">{rows.length?rows.map(r=><div key={r.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-2 text-xs"><span className="font-mono break-all">{r.id}</span><span className="flex gap-2"><Badge variant="outline">{r.status}</Badge>{r.manual_review_required&&<Badge variant="warning">Ручная сверка</Badge>}</span></div>):<p className="text-sm text-muted-foreground">Требующих внимания записей нет</p>}</div></CardContent></Card>
}
export function OperationsHealth(){
 const overview=useQuery({queryKey:['v043-summary'],queryFn:()=>api<Summary>(`${ROOT}/summary`),refetchInterval:30000})
 const jobs=useQuery({queryKey:['v043-jobs'],queryFn:()=>api<{jobs:Job[]}>(`${ROOT}/jobs`)})
 const payments=useQuery({queryKey:['v043-payments'],queryFn:()=>api<{items:Item[]}>(`${ROOT}/payments`)})
 const refunds=useQuery({queryKey:['v043-refunds'],queryFn:()=>api<{items:Item[]}>(`${ROOT}/refunds`)})
 const purchases=useQuery({queryKey:['v043-purchases'],queryFn:()=>api<{items:Item[]}>(`${ROOT}/purchases`)})
 const suppliers=useQuery({queryKey:['v043-supplier-factory'],queryFn:()=>api<Factory>(`${ROOT}/supplier-factory`)})
 const refresh=()=>{void Promise.all([overview.refetch(),jobs.refetch(),payments.refetch(),refunds.refetch(),purchases.refetch(),suppliers.refetch()])}
 return <>
 <div className="flex items-start justify-between gap-4"><Heading title="Эксплуатация и контроль" description="Очереди worker, ошибки, ручные сверки, платежи и подключения API"/><Button variant="outline" onClick={refresh}><RefreshCw size={16}/> Обновить</Button></div>
 <div className="mb-5 flex gap-2 flex-wrap"><Badge variant="outline"><ShieldCheck size={13} className="mr-1"/> Только чтение</Badge><Badge variant="warning">Реальные закупки и платежи: NO-GO</Badge></div>
 {overview.isPending?<Loading/>:overview.error?<ErrorBox error={overview.error}/>:<div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
 {([{title:'Очередь worker',value:overview.data.jobs.pending,icon:Clock3},{title:'Ошибки заданий',value:overview.data.jobs.failed,icon:AlertTriangle},{title:'Ручные разборы',value:overview.data.jobs.manual_review,icon:Activity},{title:'Просроченные lease',value:overview.data.jobs.lease_expired,icon:ShieldCheck}]).map(({title,value,icon:Icon})=><Card key={title}><CardContent className="p-5"><Icon size={19} className="text-muted-foreground"/><div className="mt-3 text-3xl font-semibold">{value}</div><div className="text-sm text-muted-foreground">{title}</div></CardContent></Card>)}</div>}
 <Tabs defaultValue="jobs"><TabsList className="flex flex-wrap"><TabsTrigger value="jobs">Worker</TabsTrigger><TabsTrigger value="money">Платежи и возвраты</TabsTrigger><TabsTrigger value="purchases">Закупки</TabsTrigger><TabsTrigger value="suppliers">Фабрика поставщиков</TabsTrigger></TabsList>
 <TabsContent value="jobs">{jobs.isPending?<Loading/>:jobs.error?<ErrorBox error={jobs.error}/>:<Card><CardHeader><CardTitle>Состояния заданий</CardTitle><CardDescription>Ручная проверка — отдельный бизнес-статус, не программная ошибка. Heartbeat продлевает lease текущему поколению исполнения.</CardDescription></CardHeader><CardContent className="space-y-2">{jobs.data.jobs.map(j=><div key={j.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-2 text-xs"><div><div className="font-medium">{j.kind}</div><code className="text-muted-foreground">{j.id.slice(0,8)}</code>{j.error_type&&<div className="text-destructive">{j.error_type}</div>}</div><span className="flex flex-wrap gap-2"><Badge variant="outline">{j.status}</Badge><Badge variant={j.severity==='error'?'danger':j.severity==='action_required'?'warning':'secondary'}>{j.code}</Badge><span className="text-muted-foreground">Попыток: {j.attempts}</span></span></div>)}{!jobs.data.jobs.length&&<p className="text-sm text-muted-foreground">Активных задач и проблем нет</p>}</CardContent></Card>}</TabsContent>
 <TabsContent value="money"><div className="space-y-4">{payments.isPending||refunds.isPending?<Loading/>:payments.error||refunds.error?<ErrorBox error={payments.error||refunds.error}/>:<><ReadRows title="Платежи на сверке" rows={payments.data.items}/><ReadRows title="Возвраты на сверке" rows={refunds.data.items}/></>}</div></TabsContent>
 <TabsContent value="purchases">{purchases.isPending?<Loading/>:purchases.error?<ErrorBox error={purchases.error}/>:<ReadRows title="Неопределённые и ручные закупки" rows={purchases.data.items}/>}</TabsContent>
 <TabsContent value="suppliers">{suppliers.isPending?<Loading/>:suppliers.error?<ErrorBox error={suppliers.error}/>:<Card><CardHeader><CardTitle>Аккаунты и курьерские направления</CardTitle><CardDescription>Без отображения логинов, секретов и адресов. Флаги закупок не разрешают отправку заказов.</CardDescription></CardHeader><CardContent className="space-y-3">{suppliers.data.accounts.map(a=><div key={a.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 text-sm"><div><b>{a.supplier.toUpperCase()}</b> · {a.label}<div className="text-xs text-muted-foreground">API-лимит {a.requests_per_hour}/час · направлений {a.destinations} · проверенных курьерских {a.verified_courier_routes}</div></div><Badge variant={a.enabled&&a.read_enabled?'success':'secondary'}>{a.enabled&&a.read_enabled?'Чтение включено':'Чтение отключено'}</Badge></div>)}<Link to="/suppliers/control" className="inline-flex items-center text-sm text-primary underline"><CheckCircle2 size={15} className="mr-2"/> Центр интеграций</Link></CardContent></Card>}</TabsContent>
 </Tabs>
 <Link to="/system/preproduction" className="mt-5 inline-flex text-sm text-primary underline">Матрица предпродакшен-приёмки v0.44</Link>
 <p className="mt-5 text-xs text-muted-foreground">Данные формируются из внутренней базы AutoC. Не являются подтверждением состояния банка или поставщика в реальном времени.</p>
 </>
}
