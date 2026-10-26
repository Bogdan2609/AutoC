import {useEffect,useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {Plus,Save,Trash2} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {ErrorBox,Loading} from '@/components/page'
import {api,CATALOG_EDITOR} from '@/lib/api'
import type {ProductIdentity} from '@/lib/types'
const C='/api/v1/backoffice/catalog-v021'
type Category={id:string;name:string;parent_id:string|null;active:boolean;slug:string}
type Attribute={attribute_id:string;code:string;name:string;required:boolean;active:boolean;data_type:'text'|'integer'|'decimal'|'boolean'|'enum';value:string|number|boolean|null;enum_values?:string[];group_name:string;unit?:string|null}
export function ProductCategories({product,identity}:{product:string;identity:ProductIdentity}){
 const qc=useQueryClient();const [selected,setSelected]=useState('');const [primary,setPrimary]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('')
 const categories=useQuery({queryKey:['catalog-categories'],queryFn:()=>api<{items:Category[]}>(`${C}/categories`)})
 const ids=new Set(identity.categories.map(c=>c.category_id))
 async function change(fn:()=>Promise<unknown>){setBusy(true);setError('');try{await fn();await qc.invalidateQueries({queryKey:['product-identity',product]});await qc.invalidateQueries({queryKey:['product-attribute-form',product]})}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}
 return <Card><CardHeader><CardTitle>Категории товара</CardTitle><CardDescription>Основная категория определяет шаблон характеристик. Публикационные ограничения проверяются сервером.</CardDescription></CardHeader><CardContent className="space-y-4">
 {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
 <div className="space-y-2">{identity.categories.map(item=><div key={item.category_id} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm"><span>{categories.data?.items.find(c=>c.id===item.category_id)?.name||item.category_id} {item.is_primary&&<span className="text-xs text-muted-foreground">· основная</span>}</span><Button type="button" size="icon-sm" variant="ghost" title="Удалить связь" aria-label="Удалить связь категории" disabled={busy} onClick={()=>{if(window.confirm('Отвязать категорию? Для опубликованной основной категории сначала необходимо снять товар с публикации.'))void change(()=>api(`${C}/products/${product}/categories/${item.category_id}`,{method:'DELETE'}))}}><Trash2 size={15}/></Button></div>)}</div>
 {categories.isPending?<Loading/>:categories.error?<ErrorBox error={categories.error}/>:<div className="flex flex-wrap items-center gap-3"><select className="h-10 min-w-56 flex-1 rounded-lg border border-input bg-background px-3 text-sm" aria-label="Добавить категорию" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Категория…</option>{categories.data.items.filter(c=>c.active&&!ids.has(c.id)).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={primary} onChange={e=>setPrimary(e.target.checked)}/> Основная</label><Button type="button" disabled={!selected||busy} onClick={()=>void change(()=>api(`${C}/products/${product}/categories`,{method:'POST',body:JSON.stringify({category_id:selected,is_primary:primary})}))}><Plus size={15}/> Привязать</Button></div>}
 </CardContent></Card>
}
export function ProductAttributes({product}:{product:string}){
 const qc=useQueryClient();const [values,setValues]=useState<Record<string,string|boolean>>({});const [error,setError]=useState('');const [saved,setSaved]=useState('');const [busy,setBusy]=useState(false)
 const list=useQuery({queryKey:['product-attribute-form',product],queryFn:()=>api<{items:Attribute[];missing_required:string[]}>(`${CATALOG_EDITOR}/products/${product}/attribute-form`)})
 useEffect(()=>{if(list.data)setValues(Object.fromEntries(list.data.items.map(a=>[a.attribute_id,a.data_type==='boolean'?Boolean(a.value):a.value===null?'':String(a.value)])))},[list.data])
 async function save(){if(!list.data)return;setBusy(true);setSaved('');setError('');try{const items:{attribute_id:string;value:string|number|boolean}[]=[];for(const a of list.data.items){const raw=values[a.attribute_id];if(raw===''||raw===undefined){if(a.required&&a.active)throw Error(`Обязательная характеристика не заполнена: ${a.name}`);continue}let value:string|number|boolean=raw
  if(a.data_type==='integer'||a.data_type==='decimal'){const n=Number(raw);if(!Number.isFinite(n)||a.data_type==='integer'&&!Number.isInteger(n))throw Error(`Некорректное число для ${a.name}`);value=n}
  if(a.data_type==='boolean')value=Boolean(raw)
  items.push({attribute_id:a.attribute_id,value})}
  if(!items.length)throw Error('Нет значений для сохранения')
  await api(`${C}/products/${product}/attribute-values`,{method:'PUT',body:JSON.stringify({items})});await qc.invalidateQueries({queryKey:['product-identity',product]});await qc.invalidateQueries({queryKey:['product-attribute-form',product]});setSaved('Характеристики обновлены')
 }catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}}
 return <Card><CardHeader><CardTitle>Характеристики</CardTitle><CardDescription>Форма формируется из шаблона категорий. Изменения сохраняются одним атомарным запросом.</CardDescription></CardHeader><CardContent>
 {list.isPending?<Loading/>:list.error?<ErrorBox error={list.error}/>:<div className="space-y-5">
 {list.data.items.map(a=><label key={a.attribute_id} className="block space-y-2 text-sm"><span className="font-medium">{a.name} {a.required&&<span className="text-destructive">*</span>}</span><p className="text-xs text-muted-foreground">{a.group_name} · {a.code} {a.unit?'· '+a.unit:''}</p>{a.data_type==='boolean'?<input className="size-4" type="checkbox" checked={Boolean(values[a.attribute_id])} onChange={e=>setValues(v=>({...v,[a.attribute_id]:e.target.checked}))}/>:a.data_type==='enum'?<select className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" value={String(values[a.attribute_id]??'')} onChange={e=>setValues(v=>({...v,[a.attribute_id]:e.target.value}))}><option value="">Выберите…</option>{(a.enum_values||[]).map(v=><option key={v} value={v}>{v}</option>)}</select>:<Input type={a.data_type==='integer'||a.data_type==='decimal'?'number':'text'} step={a.data_type==='integer'?'1':a.data_type==='decimal'?'any':undefined} value={String(values[a.attribute_id]??'')} onChange={e=>setValues(v=>({...v,[a.attribute_id]:e.target.value}))}/>}</label>)}
 {!list.data.items.length&&<p className="text-sm text-muted-foreground">Привяжите категорию, чтобы появился набор характеристик.</p>}
 {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}{saved&&<p role="status" className="text-sm">{saved}</p>}
 <Button disabled={busy||!list.data.items.length} onClick={()=>void save()}><Save size={15}/> {busy?'Сохранение…':'Сохранить характеристики'}</Button>
 </div>}
 </CardContent></Card>
}
