import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardHeader,CardTitle,CardDescription} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {AdminAction} from '@/features/AdminAction'
import {ErrorBox,Loading} from '@/components/page'
import {api} from '@/lib/api'

const FIN='/api/v1/backoffice/finance'

export function ShadowLedgerActions({book}:{book:string}){
 const [entry,setEntry]=useState('')
 const accounts=useQuery({queryKey:['ledger-accounts',book],queryFn:()=>api<{id:string;code:string;title:string}[]>(`${FIN}/ledger/books/${book}/accounts`)})
 const balance=useQuery({queryKey:['ledger-balance',book],queryFn:()=>api<unknown>(`${FIN}/ledger/books/${book}/trial-balance`)})
 return <div className="space-y-4">
  <Card><CardHeader><CardTitle>Счета и обороты · SHADOW</CardTitle><CardDescription>Цифры не отражают реальные остатки денег.</CardDescription></CardHeader><CardContent>{accounts.isPending?<Loading/>:accounts.error?<ErrorBox error={accounts.error}/>:<div className="space-y-1 text-sm">{(accounts.data??[]).map(a=><p className="border-b py-2" key={a.id}><b>{a.code}</b> · {a.title}<span className="ml-2 text-xs text-muted-foreground">{a.id}</span></p>)}</div>}{balance.data!==undefined&&<details className="mt-4"><summary className="cursor-pointer text-sm">Показать обороты</summary><pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(balance.data,null,2)}</pre></details>}</CardContent></Card>
  <AdminAction spec={{title:'Новая SHADOW-проводка',description:'Для двойной записи передайте два или более дебетовых/кредитовых счёта с равными суммами. Идентификаторы SIM должны быть уникальны.',path:`${FIN}/ledger/books/${book}/entries`,confirmation:'SHADOW',fields:[{key:'source_ref',label:'SIM: источник',required:true},{key:'idempotency_key',label:'Ключ идемпотентности',required:true},{key:'event_kind',label:'Вид события (UPPER_CASE)',initial:'TEST_OPERATION',required:true},{key:'memo',label:'Основание',required:true},{key:'lines',label:'Строки JSON: [{"account_id":"UUID","side":"DEBIT","amount":"100.00"},{"account_id":"UUID","side":"CREDIT","amount":"100.00"}]',kind:'json',required:true,initial:'[]'}]}}/>
  <Card className="p-4"><label className="block text-xs">UUID тестовой проводки для сторнирования<Input className="mt-2" value={entry} onChange={e=>setEntry(e.target.value)}/></label></Card>
  {entry&&<AdminAction spec={{title:'Сторнировать проводку',path:`${FIN}/ledger/books/${book}/entries/${encodeURIComponent(entry)}/reverse`,confirmation:'СТОРНИРОВАТЬ SHADOW',fields:[{key:'idempotency_key',label:'Ключ идемпотентности',required:true},{key:'reason',label:'Причина',required:true}]}}/>}
 </div>
}
export function ShadowPaymentActions({payment}:{payment:string}){
 const [refund,setRefund]=useState('')
 const details=useQuery({queryKey:['shadow-payment',payment],queryFn:()=>api<unknown>(`${FIN}/payments-sim/payments/${encodeURIComponent(payment)}`)})
 return <div className="space-y-4">
  <Card><CardHeader><CardTitle>Тестовый платёж и запланированные чеки</CardTitle></CardHeader><CardContent>{details.isPending?<Loading/>:details.error?<ErrorBox error={details.error}/>:<pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(details.data,null,2)}</pre>}<Button size="sm" className="mt-3" variant="outline" onClick={()=>void details.refetch()}>Обновить</Button></CardContent></Card>
  <div className="grid gap-4 xl:grid-cols-2"><AdminAction spec={{title:'Запросить SHADOW-возврат',description:'Только на успешном тестовом платеже. Сервер проверит остаток и идемпотентность.',path:`${FIN}/payments-sim/payments/${encodeURIComponent(payment)}/refunds`,confirmation:'SHADOW REFUND',fields:[{key:'request_key',label:'SIM: идентификатор возврата',required:true},{key:'amount',label:'Сумма ₽',required:true}]}}/><AdminAction spec={{title:'Создать план чека (не фискализация)',path:`${FIN}/payments-sim/payments/${encodeURIComponent(payment)}/receipt-plans`,confirmation:'SHADOW RECEIPT',fields:[{key:'request_key',label:'SIM: ключ',required:true},{key:'kind',label:'Тип',kind:'select',options:['PREPAYMENT','OFFSET','REFUND'],initial:'PREPAYMENT'},{key:'amount',label:'Сумма ₽',required:true},{key:'vat_note',label:'Примечание НДС',initial:'UNCONFIRMED'}]}}/></div>
  <Card className="p-4"><label className="text-xs">UUID SHADOW-возврата<Input className="mt-2" value={refund} onChange={e=>setRefund(e.target.value)}/></label></Card>{refund&&<AdminAction spec={{title:'Мок-результат тестового возврата',path:`${FIN}/payments-sim/refunds/${encodeURIComponent(refund)}/mock-result`,confirmation:'SHADOW REFUND',fields:[{key:'status',label:'Результат',kind:'select',options:['SUCCEEDED','REJECTED'],initial:'SUCCEEDED'}]}}/>}
 </div>
}
export function ShadowRegisterActions(){
 const [book,setBook]=useState('')
 const base=`${FIN}/tax-registers-sim`
 const state=useQuery({queryKey:['registers-advanced'],queryFn:()=>api<{books:{id:string;book_ref:string}[];documents:{id:string;document_ref:string}[]}>(`${base}/overview`)})
 const entries=useQuery({queryKey:['register-rows',book],queryFn:()=>api<unknown>(`${base}/books/${book}/rows`),enabled:!!book})
 return <div className="space-y-4">
  <AdminAction spec={{title:'Новый SHADOW-документ',path:`${base}/documents`,confirmation:'SHADOW',fields:[{key:'document_ref',label:'SIM:F5:DOC:…',required:true},{key:'document_date',label:'Дата документа (2026)',kind:'date',required:true,initial:'2026-10-23'},{key:'kind',label:'Тип',kind:'select',options:['SALE','PURCHASE','AGENT_REPORT','ADVANCE','REFUND','EXPENSE'],initial:'SALE'},{key:'memo',label:'Комментарий',initial:'SIMULATION ONLY'}]}}/>
  {state.isPending?<Loading/>:state.error?<ErrorBox error={state.error}/>:<Card><CardHeader><CardTitle>Документы / регистры</CardTitle></CardHeader><CardContent className="space-y-2 text-xs"><p>Документов: {state.data?.documents?.length??0}</p>{state.data?.documents?.map(d=><p key={d.id}>{d.document_ref} · {d.id}</p>)}<label className="mt-4 block font-medium">Выберите регистр<select className="mt-2 block h-10 w-full rounded-lg border border-input bg-background px-3" value={book} onChange={e=>setBook(e.target.value)}><option value="">Не выбран</option>{state.data?.books?.map(b=><option key={b.id} value={b.id}>{b.book_ref}</option>)}</select></label></CardContent></Card>}
  {book&&<><AdminAction spec={{title:'SHADOW-строка налогового регистра',path:`${base}/books/${book}/rows`,confirmation:'SHADOW',fields:[{key:'request_key',label:'SIM:F5:ROW:…',required:true},{key:'document_id',label:'UUID тестового документа',required:true},{key:'event_kind',label:'Тип операции',kind:'select',options:['OWN_SALE','AGENCY_SELLER_SALE','AGENCY_PLATFORM_FEE','PURCHASE','ADVANCE','REFUND','EXPENSE'],initial:'OWN_SALE'},{key:'operation_date',label:'Дата операции (2026)',kind:'date',required:true,initial:'2026-10-23'},{key:'gross_amount',label:'Сумма ₽',required:true},{key:'vat_scheme',label:'НДС',kind:'select',options:['UNRESOLVED','STANDARD','USN_5','USN_7','EXEMPT'],initial:'UNRESOLVED'},{key:'tax_class',label:'Налоговый класс',kind:'select',options:['STANDARD_GOODS','REDUCED_10','ZERO','EXEMPT'],initial:'STANDARD_GOODS'},{key:'correction_of_id',label:'UUID исходной строки (только возврат)'}]}}/><Card><CardHeader><CardTitle>Записи регистра · просмотр</CardTitle></CardHeader><CardContent>{entries.isPending?<Loading/>:entries.error?<ErrorBox error={entries.error}/>:<pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(entries.data,null,2)}</pre>}</CardContent></Card></>}
 </div>
}
