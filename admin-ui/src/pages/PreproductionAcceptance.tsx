import {useQuery} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {AlertTriangle,ClipboardCheck,RefreshCw,ShieldAlert} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Heading,Loading,ErrorBox} from '@/components/page'
import {api} from '@/lib/api'

type Gate={id:string;name:string;kind:string;required_evidence:string;status:string}
type Acceptance={schema:string;read_only:boolean;live_money_decision:'NO_GO';automated_go_possible:boolean;gates:Gate[];note:string}
const ROOT='/api/v1/backoffice/preproduction-v044/gates'
const groups:Record<string,string>={technical:'Технические испытания',external:'Банк и поставщики',legal:'Юридические и налоговые условия',governance:'Решение о вводе в эксплуатацию'}
export function PreproductionAcceptance(){
 const query=useQuery({queryKey:['v044-preproduction'],queryFn:()=>api<Acceptance>(ROOT)})
 return <>
  <div className="flex items-start justify-between gap-4"><Heading title="Предпродакшен-приёмка" description="Матрица доказательств v0.44 · ни один пункт не отмечается выполненным без подтверждения"/><Button variant="outline" onClick={()=>void query.refetch()}><RefreshCw size={15}/> Обновить</Button></div>
  <div className="my-5 flex flex-wrap gap-2"><Badge variant="warning"><ShieldAlert size={13}/> Реальные деньги: NO-GO</Badge><Badge variant="outline">Только чтение</Badge><Badge variant="outline">Решение принимает ответственное лицо</Badge></div>
  {query.isPending?<Loading/>:query.error?<ErrorBox error={query.error}/>:<>
    <Card><CardHeader><CardTitle className="flex items-center gap-2"><AlertTriangle size={17}/> Выпуск не авторизован</CardTitle><CardDescription>Эта страница — техническая матрица, а не подписанное свидетельство или разрешение банковских операций. Результаты принимаются только по отдельным артефактам испытаний.</CardDescription></CardHeader><CardContent className="flex flex-wrap gap-4 text-sm"><span>Критериев: <b>{query.data.gates.length}</b></span><span>Неподтверждённых: <b>{query.data.gates.filter(g=>g.status!=='VERIFIED').length}</b></span><span>Автоматический GO: <b>запрещён</b></span></CardContent></Card>
    {Object.entries(groups).map(([kind,title])=><Card key={kind} className="mt-4"><CardHeader><CardTitle className="flex items-center gap-2"><ClipboardCheck size={17}/>{title}</CardTitle></CardHeader><CardContent className="space-y-3">{query.data.gates.filter(g=>g.kind===kind).map(g=><div key={g.id} className="flex flex-wrap items-start justify-between gap-2 border-b border-border py-2 last:border-b-0"><div className="max-w-3xl text-sm"><div className="font-medium">{g.id} · {g.name}</div><p className="mt-1 text-xs text-muted-foreground">{g.required_evidence}</p></div><Badge variant="warning">Нет подтверждения</Badge></div>)}</CardContent></Card>)}
    <p className="mt-5 text-xs text-muted-foreground">Документы с результатами и контрольными суммами формирует offline-скрипт scripts/acceptance_report_v044.py. Автоматической загрузки доказательств или выдачи допуска здесь нет.</p>
  </>}
  <Link to="/system/operations" className="mt-5 inline-block text-sm text-primary underline">Вернуться к эксплуатационному мониторингу</Link>
 </>
}
