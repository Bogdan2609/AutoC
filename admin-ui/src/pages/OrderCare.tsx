import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {AlertTriangle,LockKeyhole,ArrowUpRight} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Card,CardContent,CardHeader,CardTitle,CardDescription} from '@/components/ui/card'
import {Button} from '@/components/ui/button'
import {AdminAction} from '@/features/AdminAction'
import {ErrorBox,Loading,Status} from '@/components/page'
import {api,OPS,MONITOR} from '@/lib/api'
import type {OrderDetail} from '@/lib/types'
import {shortId} from '@/lib/utils'

export function OrderCare({detail}:{detail:OrderDetail}){
 const [selected,setSelected]=useState<string|null>(null)
 const eligible=detail.items.filter(i=>['procurement_manual_review','supplier_rejected'].includes(i.status))
 return <div className="space-y-4"><Card><CardHeader><CardTitle>Центр решений по заказу</CardTitle><CardDescription>Без автоматического повторения закупки, без списания оплаты и без подмены товара. Все опасные решения проверяет backend.</CardDescription></CardHeader><CardContent className="space-y-4">
  <div className="grid gap-2 md:grid-cols-3">{[{label:'Позиции заказа',value:detail.items.length},{label:'На ручной проверке',value:eligible.length},{label:'Привязанных платежей',value:detail.payments.length}].map(item=><div key={item.label} className="rounded-xl border border-border p-4"><p className="text-xs text-muted-foreground">{item.label}</p><p className="mt-2 text-2xl font-semibold">{item.value}</p></div>)}</div>
  <div className="rounded-lg border border-border bg-muted/30 p-4 text-xs text-muted-foreground"><AlertTriangle className="mr-2 inline" size={16}/> Отказ или неопределённый результат поставщика нельзя исправлять повторным запросом на закупку. При предложении замены требуется согласие покупателя и последующая живая проверка предложения.</div>
  {eligible.length?eligible.map(i=><div key={i.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 text-sm"><div><p className="font-medium">{i.title}</p><p className="mt-1 text-xs text-muted-foreground">{i.supplier} · строка {shortId(i.id)} · {i.quantity} шт.</p></div><div className="flex items-center gap-2"><Status value={i.status}/><Button variant="outline" size="sm" onClick={()=>setSelected(selected===i.id?null:i.id)}>{selected===i.id?'Закрыть':'Предложить замену'}</Button></div></div>):<p className="text-sm text-muted-foreground">Строк, допускающих предложение замены, сейчас нет.</p>}
  {selected&&<AdminAction key={selected} spec={{title:'Предложение альтернативы покупателю',description:'Только подтверждённый идентичный товар. Сервер проверит совпадение мастер-товара, остаток и допустимый статус. Это НЕ закупка.',path:`${OPS}/lines/${selected}/propose-replacement`,queryKeys:['alternative_item_id','reason'],confirmation:'ПРЕДЛОЖИТЬ',fields:[{key:'alternative_item_id',label:'UUID альтернативной позиции поставщика',required:true},{key:'reason',label:'Причина замены',initial:'Supplier cannot fulfill',required:true}],submitLabel:'Создать предложение замены'}}/>}
  <p className="flex items-center gap-2 text-xs text-muted-foreground"><LockKeyhole size={15}/> Критические операции с оплатой и фактической закупкой остаются за проверенным backend-процессом.</p>
 </CardContent></Card></div>
}
export function SupplierPurchaseInspector({attemptId}:{attemptId:string}){
 const query=useQuery({queryKey:['purchase-inspector',attemptId],queryFn:()=>api<unknown>(`${MONITOR}/purchases?status=&page=1&size=100`)})
 return <Card><CardHeader><CardTitle>Попытка закупки {shortId(attemptId)}</CardTitle><CardDescription>Только просмотр. Не отправляйте заказ поставщику повторно без отдельной сверки.</CardDescription></CardHeader><CardContent>{query.isPending?<Loading/>:query.error?<ErrorBox error={query.error}/>:<div className="text-sm"><Badge>Реестр попыток</Badge><Link to="/orders?tab=purchases" className="ml-3 inline-flex items-center gap-1 underline">Открыть журнал <ArrowUpRight size={13}/></Link></div>}</CardContent></Card>
}
