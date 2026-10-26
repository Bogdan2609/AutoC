import {useEffect,useRef,useState} from 'react'
import type {FormEvent,ReactNode} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import {AlertTriangle,CheckCircle2,Loader2,X} from 'lucide-react'
import {api,WORKSPACE} from '@/lib/api'
import {Button} from '@/components/ui/button'
import {Input} from '@/components/ui/input'
import {Textarea} from '@/components/ui/textarea'
import {entities} from './entity-config'
import type {EntityRecord,Field} from './entity-config'

function Modal({title,children,onClose}:{title:string;children:ReactNode;onClose:()=>void}){
 const ref=useRef<HTMLDialogElement>(null)
 useEffect(()=>{const d=ref.current;if(!d)return;d.showModal();return()=>{if(d.open)d.close()}},[])
 return <dialog ref={ref} onClose={onClose} onClick={e=>{if(e.target===e.currentTarget)e.currentTarget.close()}} aria-labelledby="entity-dialog-title" className="fixed inset-0 m-auto w-[min(95vw,670px)] max-h-[88vh] overflow-y-auto rounded-2xl border border-border bg-popover p-0 text-popover-foreground shadow-2xl backdrop:bg-black/55">
  <div className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-border bg-popover px-6 py-5"><h2 id="entity-dialog-title" className="text-lg font-semibold tracking-tight">{title}</h2><Button type="button" variant="ghost" size="icon" aria-label="Закрыть" onClick={()=>ref.current?.close()}><X size={18}/></Button></div>
  <div className="px-6 py-5">{children}</div>
 </dialog>
}
const displayString=(v:unknown):string=>v===null||v===undefined?'':typeof v==='string'?v:String(v)
function ProductSelector({value,onChange}:{value:string;onChange:(v:string)=>void}){
 const [query,setQuery]=useState('')
 const data=useQuery({queryKey:['entity-products',query],queryFn:()=>api<{items:{id:string;brand:string;article:string;name:string}[]}>(`${WORKSPACE}/catalog?q=${encodeURIComponent(query.trim())}&size=12`),enabled:query.trim().length>=2})
 return <div className="space-y-2"><Input placeholder="Поиск по артикулу или названию" value={query} onChange={e=>{setQuery(e.target.value);onChange('')}} aria-label="Найти мастер-товар"/>{value&&<p className="rounded-md bg-muted px-3 py-2 text-xs">Выбран UUID: {value}</p>}{query.trim().length>=2&&<div className="max-h-44 overflow-y-auto rounded-lg border border-border">{data.isPending?<p className="p-3 text-xs text-muted-foreground">Поиск…</p>:data.error?<p className="p-3 text-xs text-destructive">Не удалось найти товар</p>:data.data?.items.length?data.data.items.map(p=><button type="button" key={p.id} onClick={()=>{onChange(p.id);setQuery(`${p.brand} · ${p.article} · ${p.name}`)}} className="block w-full border-b border-border px-3 py-2 text-left text-xs hover:bg-muted last:border-0">{p.brand} · {p.article} · {p.name}</button>):<p className="p-3 text-xs text-muted-foreground">Нет результатов</p>}</div>}</div>
}
function FieldControl({field,value,onChange,values}:{field:Field;value:unknown;onChange:(v:unknown)=>void;values:EntityRecord}){
 const list=useQuery({queryKey:['entity-lookup',field.source],queryFn:()=>api<unknown>(field.source!),enabled:!!field.source,staleTime:60_000})
 const lookups=Array.isArray(list.data)?list.data:(list.data&&typeof list.data==='object'&&(field.sourceKey||'items') in list.data?(list.data as Record<string,unknown>)[field.sourceKey||'items']:[])
 const opts=field.options || (Array.isArray(lookups)?lookups.filter((r):r is EntityRecord=>!!r&&typeof r==='object'&&!Array.isArray(r)).map(r=>[displayString(r.id),displayString(r[field.display||'name']||(r.name||r.label||r.code))] as const):[])
 void values
 if(field.type==='product')return <ProductSelector value={displayString(value)} onChange={onChange}/>
 if(field.type==='boolean')return <label className="flex items-center gap-3 rounded-lg border border-border px-4 py-3 text-sm"><input type="checkbox" checked={Boolean(value)} onChange={e=>onChange(e.target.checked)} className="size-4 accent-foreground"/><span>{field.label}</span></label>
 if(field.type==='select')return <select className="flex h-10 w-full rounded-lg border border-input bg-background px-3 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" required={field.required} value={displayString(value)} onChange={e=>onChange(e.target.value)}><option value="">Выберите…</option>{opts.map(([v,label])=><option value={v} key={v}>{label}</option>)}</select>
 if(field.type==='textarea')return <Textarea value={displayString(value)} onChange={e=>onChange(e.target.value)} rows={3}/>
 return <Input type={field.type==='number'?'number':'text'} min={field.type==='number'?field.min:undefined} max={field.type==='number'?field.max:undefined} step={field.type==='number'?'any':undefined} value={displayString(value)} onChange={e=>onChange(e.target.value)} required={field.required} maxLength={600} autoComplete="off" />
}
export function EntityDialog({entity,row,onClose,onSaved,stockNode}:{entity:string;row?:EntityRecord;onClose:()=>void;onSaved?:()=>void;stockNode?:string}){
 const cfg=entities[entity];const editing=!!row
 const fields=editing?(cfg.editFields||cfg.fields):cfg.fields
 const [values,setValues]=useState<EntityRecord>(()=>Object.fromEntries(fields.map(f=>[f.key,row?.[f.key]??(f.key==='node_id'&&stockNode?stockNode:f.defaultValue??(f.type==='boolean'?false:''))])))
 const [error,setError]=useState('');const [sending,setSending]=useState(false);const [ok,setOk]=useState(false)
 const client=useQueryClient()
 const extra=useQuery({queryKey:['entity-extra',entity,row?.id],queryFn:()=>api<EntityRecord>(cfg.loadEdit!(row!)),enabled:!!editing&&!!cfg.loadEdit})
 useEffect(()=>{if(extra.data)setValues(prev=>({...prev,...extra.data}))},[extra.data])
 if(!cfg)return null
 function change(k:string,v:unknown){setValues(prev=>({...prev,[k]:v}));setError('')}
 async function save(e:FormEvent){e.preventDefault();const path=editing?cfg.edit?.(row!):cfg.create;if(!path){setError('Действие недоступно');return}
  const body:EntityRecord={}
  for(const f of fields){const val=values[f.key];if(val===undefined||val===null||val===''){if(f.required){setError(`Поле «${f.label}» обязательно`);return}if(['external_address_id','external_delivery_id','external_requisite_id','parent_id','store_id','unit','merchant_id','valid_until','scope_id','inn','kpp','evidence_reference','cap_amount'].includes(f.key))body[f.key]=null;else if(f.key==='receiving_contact_comment')body[f.key]='';continue}
   if(f.type==='number'){const n=Number(val);if(!Number.isFinite(n)||n<0){setError(`Некорректное число: ${f.label}`);return}body[f.key]=n}
   else if(f.key==='axes'||f.key==='enum_values'||f.key==='roles')body[f.key]=displayString(val).split(',').map(s=>s.trim()).filter(Boolean)
   else body[f.key]=val
  }
  if(entity==='destinations'){
   const a=values.account_id;const d=body.external_delivery_id;
   // Enforce additional client-side guard for ROSSKO, backend remains the authority.
   if(d&&String(d)!=='000000002'&&String(row?.supplier_code||'').toLowerCase()==='rossko'){setError('ROSSKO: допустима только курьерская доставка 000000002');return}
   if(!a&&!editing){setError('Укажите API-аккаунт');return}
   if(!editing){delete body.verified;delete body.enabled}
  }
  if(entity==='nodes'&&Boolean(body.receiving_contact_name)!==Boolean(body.receiving_contact_phone)){setError('ФИО и телефон приёмки указываются вместе');return}
  setSending(true);setError('');try{await api<unknown>(path,{method:editing?(cfg.method||'PATCH'):'POST',body:JSON.stringify(body)});setOk(true);await client.invalidateQueries();onSaved?.();onClose()}catch(err){setError(err instanceof Error?err.message:String(err))}finally{setSending(false)}
 }
 return <Modal title={(editing?'Изменить: ':'Создать: ')+cfg.title} onClose={onClose}><form className="space-y-5" onSubmit={save}>
  {cfg.warning&&<p className="flex items-start gap-2 rounded-lg border border-border bg-muted/50 p-3 text-xs leading-relaxed text-muted-foreground"><AlertTriangle className="mt-0.5 shrink-0" size={16}/>{cfg.warning}</p>}
  {extra.isPending&&cfg.loadEdit&&<p className="text-sm text-muted-foreground">Загружаем детали записи…</p>}
  {extra.error&&<p role="alert" className="text-sm text-destructive">Не удалось загрузить дополнительные данные: {String(extra.error)}</p>}
  <div className="grid gap-4 sm:grid-cols-2">{fields.map(f=><div key={f.key} className={f.type==='textarea'||f.type==='product'?'sm:col-span-2':'space-y-2'}>{f.type!=='boolean'&&<label className="mb-2 block text-xs font-semibold" htmlFor={`edit-${f.key}`}>{f.label}{f.required&&' *'}</label>}<FieldControl field={f} value={values[f.key]} onChange={v=>change(f.key,v)} values={values}/>{f.help&&<p className="mt-1 text-xs text-muted-foreground">{f.help}</p>}</div>)}</div>
  {error&&<p className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</p>}
  {ok&&<p className="flex items-center gap-2 text-sm"><CheckCircle2 size={16}/> Сохранено</p>}
  <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4"><Button type="button" variant="outline" onClick={onClose}>Отмена</Button><Button type="submit" disabled={sending||(!!cfg.loadEdit&&extra.isPending)}>{sending?<Loader2 size={15} className="animate-spin"/>:null}Сохранить</Button></div>
 </form></Modal>
}
