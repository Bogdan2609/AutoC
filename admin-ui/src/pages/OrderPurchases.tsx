import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {AlertTriangle,ShieldCheck} from 'lucide-react'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Button} from '@/components/ui/button'
import {Badge} from '@/components/ui/badge'
import {Empty,ErrorBox,Loading,Status} from '@/components/page'
import {api,MONITOR,canAccessPath,qs} from '@/lib/api'
import type {Page} from '@/lib/types'
import {dateText,shortId} from '@/lib/utils'
import {PurchaseAttemptInspector} from '@/features/PurchaseAttemptDetail'

type PurchaseAttempt={id:string;order_id:string|null;order_line_id:string|null;account_id:string;destination_id:string;status:string;remote_order_ids:string[];error_code:string|null;created_at:string}
const risky=new Set(['manual_review','dispatch_unknown','unknown','sent_unverified','supplier_rejected','rejected'])
export function OrderPurchases({orderId}:{orderId:string}){
 const [page,setPage]=useState(1)
 const [selected,setSelected]=useState<string|null>(null)
 const path=`${MONITOR}/purchases`
 const allowed=canAccessPath(path)
 const purchases=useQuery({queryKey:['order-purchases',orderId,page],queryFn:()=>api<Page<PurchaseAttempt>>(`${path}?${qs({order_id:orderId,page,size:20})}`),enabled:allowed&&!!orderId})
 return <Card><CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck size={18}/> Попытки закупки по этому заказу</CardTitle><CardDescription>Точный серверный фильтр по UUID заказа. Сведения только для диагностики; повторная закупка отсюда недоступна.</CardDescription></CardHeader><CardContent className="space-y-3">
  {!allowed?<p className="text-sm text-muted-foreground">Просмотр журнала закупок недоступен для вашей роли.</p>:purchases.isPending?<Loading/>:purchases.error?<ErrorBox error={purchases.error}/>:purchases.data?.items.length? <>
   {purchases.data.items.map(row=><div key={row.id} className="space-y-2 rounded-xl border border-border p-4 text-sm"><div className="flex flex-wrap items-center justify-between gap-2"><b title={row.id}>Попытка {shortId(row.id)}</b><Status value={row.status}/></div><p className="text-xs text-muted-foreground">Создана: {dateText(row.created_at)} · Строка: {shortId(row.order_line_id||undefined)}</p><p className="text-xs text-muted-foreground">Аккаунт: {shortId(row.account_id)} · Адрес: {shortId(row.destination_id)}</p><p className="text-xs text-muted-foreground">Код ошибки: {row.error_code||'—'} · Внешних заказов: {row.remote_order_ids?.length??0}</p>{risky.has(row.status)&&<p role="alert" className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs"><AlertTriangle className="shrink-0" size={16}/> Требуется ручная сверка с поставщиком. Не отправляйте повторный заказ при неизвестном результате.</p>}<Button type="button" size="sm" variant="outline" onClick={()=>setSelected(selected===row.id?null:row.id)}>{selected===row.id?'Скрыть детали':'Детали попытки'}</Button>{selected===row.id&&<PurchaseAttemptInspector attemptId={row.id} expectedOrderId={orderId}/>}</div>)}
   <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>Страница {purchases.data.page} · всего {purchases.data.total}</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Назад</Button><Button size="sm" variant="outline" disabled={page*(purchases.data?.size??20)>=(purchases.data?.total??0)} onClick={()=>setPage(p=>p+1)}>Далее</Button></div></div>
  </>:<Empty text="Для этого заказа попыток закупки не найдено"/>}
  <p className="flex items-center gap-2 text-xs text-muted-foreground"><Badge variant="outline">Только чтение</Badge> Исторические записи не подтверждают фактическую доставку.</p>
 </CardContent></Card>
}
