import {useQuery,useQueryClient} from '@tanstack/react-query'
import {useState} from 'react'
import {AlertTriangle,ShieldCheck,ExternalLink} from 'lucide-react'
import {Link} from 'react-router-dom'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Button} from '@/components/ui/button'
import {Badge} from '@/components/ui/badge'
import {ErrorBox,Loading,Status} from '@/components/page'
import {api,canAccessPath,MONITOR} from '@/lib/api'
import {dateText,shortId} from '@/lib/utils'

/** Read-only allowlisted response; no customer or warehouse contact details. */
export type PurchaseAttemptDetail = {
 id:string;order_id:string|null;order_line_id:string|null;account_id:string;
 destination_id:string;status:string;remote_order_ids:string[];error_code:string|null;
 quantity:number;created_at:string;sent_at:string|null;completed_at:string|null;
 manual_reconciliation_required:boolean;advisory:string;verified?:boolean;
 last_reconciled_at?:string|null;cancellation_requested?:boolean
}
export function PurchaseAttemptInspector({attemptId,expectedOrderId}:{attemptId:string;expectedOrderId?:string}){
 const [busy,setBusy]=useState<'reconcile'|'cancel'|null>(null)
 const [actionError,setActionError]=useState<string|null>(null)
 const qc=useQueryClient()
 const mutationBase=`/api/v1/admin/supplier-purchases/${encodeURIComponent(attemptId)}`
 const canMutate=canAccessPath(`${mutationBase}/reconcile`,true)
 async function execute(action:'reconcile'|'cancel'){
  if(busy)return
  if(action==='cancel'&&!window.confirm('Подтвердить ЗАЯВКУ на отмену поставщику? Реальная отмена НЕ выполняется этим действием.'))return
  setBusy(action);setActionError(null)
  try{
   const url=action==='reconcile'?`${mutationBase}/reconcile`:`${mutationBase}/request-cancellation`
   await api(url,{method:'POST'})
   await Promise.all([qc.invalidateQueries({queryKey:['purchase-attempt',attemptId]}),
    qc.invalidateQueries({queryKey:['order-purchases']}),qc.invalidateQueries({queryKey:['orders']})])
  }catch(e){setActionError(e instanceof Error?e.message:'Операция не выполнена')}
  finally{setBusy(null)}
 }
 const path=`${MONITOR}/purchases/${encodeURIComponent(attemptId)}`
 const allowed=canAccessPath(path)
 const query=useQuery({queryKey:['purchase-attempt',attemptId],queryFn:()=>api<PurchaseAttemptDetail>(path),enabled:allowed&&!!attemptId})
 return <Card data-testid="purchase-attempt-detail" className="border-border">
  <CardHeader><CardTitle className="flex flex-wrap items-center gap-2 text-base"><ShieldCheck size={18}/> Диагностика попытки {shortId(attemptId)} <Badge variant="outline">Только чтение</Badge></CardTitle><CardDescription>История отправки поставщику. Без сверки с поставщиком нельзя считать результат окончательным и нельзя повторять неизвестную закупку.</CardDescription></CardHeader>
  <CardContent className="space-y-3">
   {!allowed?<p className="text-sm text-muted-foreground">Нет права просматривать закупочные попытки.</p>:query.isPending?<Loading/>:query.error?<ErrorBox error={query.error} onRetry={()=>void query.refetch()}/>:query.data&&<>
    {expectedOrderId&&query.data.order_id!==expectedOrderId?<p role="alert" className="rounded-lg border border-destructive p-3 text-sm text-destructive">Попытка не принадлежит открытому заказу. Проверьте ссылку и данные.</p>:<>
     <div className="grid gap-3 text-sm md:grid-cols-2">
      <div><p className="text-xs text-muted-foreground">Статус</p><Status value={query.data.status}/></div>
      <div><p className="text-xs text-muted-foreground">Количество</p><p className="font-medium">{query.data.quantity} шт.</p></div>
      <div><p className="text-xs text-muted-foreground">Аккаунт поставщика</p><p className="break-all font-mono text-xs">{query.data.account_id}</p></div>
      <div><p className="text-xs text-muted-foreground">Разрешённый адрес</p><p className="break-all font-mono text-xs">{query.data.destination_id}</p></div>
      <div><p className="text-xs text-muted-foreground">Создана / отправлена</p><p>{dateText(query.data.created_at)} / {query.data.sent_at?dateText(query.data.sent_at):'Не зафиксировано'}</p></div>
      <div><p className="text-xs text-muted-foreground">Завершена / ошибка</p><p>{query.data.completed_at?dateText(query.data.completed_at):'Не зафиксировано'} · {query.data.error_code||'—'}</p></div>
     </div>
     <div className="rounded-lg bg-muted/40 p-3 text-sm"><p className="font-medium">Номера поставщика</p><p className="mt-1 break-all font-mono text-xs">{query.data.remote_order_ids?.length?query.data.remote_order_ids.join(' · '):'Номер поставщика не записан'}</p></div>
     {query.data.manual_reconciliation_required&&<p role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/40 p-3 text-sm"><AlertTriangle size={17} className="shrink-0"/> Требуется ручная сверка с поставщиком. Не повторяйте закупку автоматически.</p>}
     {query.data.last_reconciled_at&&<p className="text-xs text-muted-foreground">Последняя сверка: {dateText(query.data.last_reconciled_at)}</p>}
     {query.data.cancellation_requested&&<p role="status" className="text-sm">Заявка на отмену передана на ручную проверку. Поставщик ещё не подтвердил отмену.</p>}
     {actionError&&<p role="alert" className="rounded-md border border-destructive px-3 py-2 text-sm text-destructive">{actionError}</p>}
     {canMutate&&<div className="flex flex-wrap items-center gap-2">
       {['accepted_unverified','accepted','manual_review','dispatch_unknown'].includes(query.data.status)&&query.data.remote_order_ids?.length>0&&
        <Button variant="outline" size="sm" disabled={busy!==null} onClick={()=>void execute('reconcile')}>{busy==='reconcile'?'Сверка...':'Сверить заказ через GetOrders'}</Button>}
       {query.data.status==='verified'&&!query.data.cancellation_requested&&
        <Button variant="outline" size="sm" disabled={busy!==null} onClick={()=>void execute('cancel')}>{busy==='cancel'?'Обработка...':'Запросить отмену (вручную)'}</Button>}
       <p className="w-full text-xs text-muted-foreground">Сверка — только чтение API ROSSKO. Отмена не отправляется автоматически.</p>
      </div>}
     {query.data.order_id&&<Link className="inline-flex items-center gap-1 text-sm underline" to={`/orders/${query.data.order_id}`}>Открыть заказ <ExternalLink size={13}/></Link>}
     <p className="text-xs text-muted-foreground">UUID попытки: <span className="break-all font-mono">{query.data.id}</span></p>
    </>}
   </>}
  </CardContent>
 </Card>
}
