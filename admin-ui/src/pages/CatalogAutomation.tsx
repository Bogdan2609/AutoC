import {useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {AlertTriangle,Loader2} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {Heading,Loading,ErrorBox,Status} from '@/components/page'
import {api} from '@/lib/api'
const base='/api/v1/admin/draft-materialization/automation'
type Run={id:string;status:string;scanned:number;created_drafts:number;reviewed:number;automation?:{mode?:string;stage?:string;legacy_recovered?:number}}
export function CatalogAutomation(){
 const [limit,setLimit]=useState('1000');const [legacy,setLegacy]=useState('200');const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [message,setMessage]=useState('')
 const client=useQueryClient();const history=useQuery({queryKey:['automation','runs'],queryFn:()=>api<{items:Run[]}>(`${base}/runs?limit=20`)});const stats=useQuery({queryKey:['automation','stats'],queryFn:()=>api<{rule_revision:string;preflight_statuses:Record<string,number>}>(`${base}/statistics`)})
 const active=history.data?.items.some(r=>['queued','processing'].includes(r.status))
 async function exec(work:()=>Promise<unknown>){setBusy(true);setError('');setMessage('');try{await work();setMessage('Операция принята сервером; следите за статусом задания.');await client.invalidateQueries({queryKey:['automation']})}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}
 function start(mode:'preview'|'drafts'){
  const n=Number(limit), l=Number(legacy);if(!Number.isInteger(n)||n<1||n>350000||!Number.isInteger(l)||l<0||l>2000){setError('Допустимые значения: кандидаты 1–350000, старые карточки 0–2000');return}
  if(mode==='drafts'&&!window.confirm(`Создать до ${n} ЧЕРНОВИКОВ без публикации товаров?`))return
  void exec(()=>api(`${base}/runs`,{method:'POST',body:JSON.stringify({mode,limit:n,legacy_limit:l,confirm_draft_only:mode==='drafts'})}))
 }
 return <><Heading title="Автоматизация каталога" description="Preflight, проверка классификации и создание только черновиков с аудитом и историей задач."/><p className="mb-5 flex items-center gap-2 rounded-lg border border-border bg-muted/40 p-3 text-xs text-muted-foreground"><AlertTriangle size={16}/> Автоматизация не публикует карточки. Публикация производится вручную после проверки.</p>{error&&<p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}{message&&<p className="mb-4 text-sm">{message}</p>}
 <Card className="mb-5"><CardHeader><CardTitle>Настройки выполнения</CardTitle><CardDescription>Превью не создаёт и не изменяет товары. Ревизия правил: {stats.data?.rule_revision||'—'}.</CardDescription></CardHeader><CardContent className="flex flex-wrap items-end gap-3"><label className="text-xs">Число кандидатов<Input className="mt-1.5 w-40" type="number" min="1" max="350000" value={limit} onChange={e=>setLimit(e.target.value)}/></label><label className="text-xs">Проверить старые карточки<Input className="mt-1.5 w-40" type="number" min="0" max="2000" value={legacy} onChange={e=>setLegacy(e.target.value)}/></label><Button disabled={busy||active} variant="outline" onClick={()=>start('preview')}>Запустить Preflight</Button><Button disabled={busy||active} onClick={()=>start('drafts')}>{busy&&<Loader2 size={15} className="animate-spin"/>} Создать черновики</Button></CardContent></Card>
 <div className="mb-5 grid gap-3 sm:grid-cols-3">{Object.entries(stats.data?.preflight_statuses||{}).map(([k,v])=><Card key={k} className="p-4"><p className="text-2xl font-semibold">{v}</p><p className="mt-1 text-xs text-muted-foreground">{k}</p></Card>)}</div>
 <Card><CardHeader><CardTitle>История заданий</CardTitle></CardHeader><CardContent>{history.isPending?<Loading/>:history.error?<ErrorBox error={history.error}/>:<div className="space-y-3">{history.data?.items.map(r=><div className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 text-sm" key={r.id}><div><p className="font-medium">{r.id.slice(0,12)} · {r.automation?.mode||'—'}</p><p className="mt-1 text-xs text-muted-foreground">Этап: {r.automation?.stage||'—'} · проверено: {r.scanned} · черновиков: {r.created_drafts} · на проверке: {r.reviewed}</p></div><div className="flex items-center gap-2"><Status value={r.status}/>{r.status==='failed'&&<Button size="sm" variant="outline" disabled={busy} onClick={()=>{if(window.confirm('Возобновить задачу с сохранённого курсора без публикации?'))void exec(()=>api(`${base}/runs/${encodeURIComponent(r.id)}/resume`,{method:'POST'}))}}>Возобновить</Button>}</div></div>)}{!history.data?.items.length&&<p className="text-sm text-muted-foreground">Заданий нет</p>}</div>}</CardContent></Card></>
}
