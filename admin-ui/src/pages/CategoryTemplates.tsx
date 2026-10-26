import {useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {Link} from 'react-router-dom'
import {ArrowLeft,Save} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {ErrorBox,Heading,Loading} from '@/components/page'
import {api} from '@/lib/api'

const base='/api/v1/backoffice/catalog-v021'
type Category={id:string;name:string;active:boolean}
type Attribute={id:string;name:string;code:string;active:boolean;filterable:boolean}
type Binding={attribute_id:string;name:string;code:string;required:boolean;show_in_filters:boolean;sort_order:number;group_name:string;direct:boolean;source_category_id:string}
export function CategoryTemplates(){
 const qc=useQueryClient()
 const [category,setCategory]=useState('')
 const [attribute,setAttribute]=useState('')
 const [required,setRequired]=useState(false)
 const [filter,setFilter]=useState(false)
 const [order,setOrder]=useState('0')
 const [group,setGroup]=useState('Основные')
 const [error,setError]=useState('')
 const [message,setMessage]=useState('')
 const [busy,setBusy]=useState(false)
 const categories=useQuery({queryKey:['catalog-categories'],queryFn:()=>api<{items:Category[]}>(`${base}/categories`)})
 const attributes=useQuery({queryKey:['catalog-attributes'],queryFn:()=>api<{items:Attribute[]}>(`${base}/attributes`)})
 const template=useQuery({queryKey:['category-template',category],queryFn:()=>api<{items:Binding[]}>(`${base}/categories/${encodeURIComponent(category)}/template`),enabled:!!category})
 const existing=template.data?.items.find(a=>a.attribute_id===attribute)
 function choose(id:string){setAttribute(id);const prev=template.data?.items.find(a=>a.attribute_id===id);setRequired(prev?.required||false);setFilter(prev?.show_in_filters||false);setOrder(String(prev?.sort_order||0));setGroup(prev?.group_name||'Основные');setMessage('');setError('')}
 async function save(){if(!category||!attribute)return;const n=Number(order);if(!Number.isInteger(n)||n<0||n>100000){setError('Порядок должен быть целым числом 0–100000');return}if(!group.trim()){setError('Укажите название группы');return}const selected=attributes.data?.items.find(a=>a.id===attribute);if(filter&&!selected?.filterable){setError('Сначала разрешите фильтрацию в определении характеристики');return}if(required&&!window.confirm('Обязательная характеристика может затронуть существующие опубликованные карточки. Выполнить серверную проверку?'))return
 setBusy(true);setError('');setMessage('');try{await api(`${base}/categories/${category}/template/${attribute}`,{method:'PUT',body:JSON.stringify({required,show_in_filters:filter,sort_order:n,group_name:group.trim()})});await qc.invalidateQueries({queryKey:['category-template',category]});setMessage('Шаблон сохранён. Изменения проверены сервером.')}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}
 return <><Link to="/catalog/manage?tab=categories" className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft size={15}/> К категориям</Link><Heading title="Шаблоны характеристик" description="Привязка характеристик к категориям, наследование, обязательность и фильтры. Проверки опубликованных товаров выполняет backend."/>
 <div className="grid items-start gap-5 lg:grid-cols-[1fr_1.5fr]"><Card><CardHeader><CardTitle>Категория и характеристика</CardTitle><CardDescription>Выберите активную категорию, затем характеристику для настройки.</CardDescription></CardHeader><CardContent className="space-y-4">{categories.isPending||attributes.isPending?<Loading/>:categories.error?<ErrorBox error={categories.error}/>:attributes.error?<ErrorBox error={attributes.error}/>:<><label className="block space-y-2 text-xs font-semibold">Категория<select className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" value={category} onChange={e=>{setCategory(e.target.value);setAttribute('');setMessage('');setError('')}}><option value="">Выберите категорию…</option>{categories.data.items.filter(x=>x.active).map(x=><option value={x.id} key={x.id}>{x.name}</option>)}</select></label><label className="block space-y-2 text-xs font-semibold">Характеристика<select className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" value={attribute} onChange={e=>choose(e.target.value)}><option value="">Выберите характеристику…</option>{attributes.data.items.filter(x=>x.active).map(x=><option value={x.id} key={x.id}>{x.name} · {x.code}</option>)}</select></label></>}
 {attribute&&<div className="space-y-4 border-t border-border pt-4"><p className="text-xs text-muted-foreground">{existing?'Установленное правило / можно изменить':'Новая привязка характеристики'}</p><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={required} onChange={e=>setRequired(e.target.checked)}/> Обязательная характеристика</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={filter} onChange={e=>setFilter(e.target.checked)}/> Использовать в фильтрах</label><label className="block space-y-2 text-xs">Порядок<Input type="number" min="0" max="100000" value={order} onChange={e=>setOrder(e.target.value)}/></label><label className="block space-y-2 text-xs">Группа<Input maxLength={80} value={group} onChange={e=>setGroup(e.target.value)}/></label><Button disabled={busy} onClick={()=>void save()}><Save size={15}/> {busy?'Сохраняем…':'Сохранить правило'}</Button></div>}{error&&<p role="alert" className="text-sm text-destructive">{error}</p>}{message&&<p role="status" className="text-sm">{message}</p>}</CardContent></Card>
 <Card><CardHeader><CardTitle>Текущий шаблон</CardTitle><CardDescription>Унаследованные характеристики отмечены отдельно; новое правило создаёт прямую привязку категории.</CardDescription></CardHeader><CardContent>{!category?<p className="text-sm text-muted-foreground">Выберите категорию.</p>:template.isPending?<Loading/>:template.error?<ErrorBox error={template.error}/>:<div className="divide-y divide-border">{!template.data?.items.length&&<p className="text-sm text-muted-foreground">Правил пока нет.</p>}{template.data?.items.map(x=><button key={x.attribute_id} type="button" onClick={()=>choose(x.attribute_id)} className="flex w-full items-center justify-between gap-3 py-3 text-left hover:bg-muted/40"><span className="text-sm font-medium">{x.name}<span className="block text-xs font-normal text-muted-foreground">{x.code} · {x.group_name} · {x.direct?'Прямая':'Унаследована'}</span></span><span className="shrink-0 text-xs text-muted-foreground">{x.required?'Обяз.':'Доп.'}{x.show_in_filters?' · Фильтр':''}</span></button>)}</div>}</CardContent></Card></div></>
}
