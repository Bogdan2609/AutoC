import {useState} from 'react'
import type {FormEvent,ReactNode} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {Button} from '@/components/ui/button'
import {Card,CardContent,CardHeader,CardTitle,CardDescription} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {Textarea} from '@/components/ui/textarea'
import {api,canAccessPath} from '@/lib/api'

type Kind='text'|'number'|'boolean'|'date'|'textarea'|'select'|'json'
export type FieldSpec={key:string;label:string;kind?:Kind;required?:boolean;options?:string[];initial?:string;placeholder?:string}
export type ActionSpec={title:string;description?:string;path:string;method?:'POST'|'PUT'|'PATCH'|'DELETE';fields:FieldSpec[];queryKeys?:string[];confirmation?:string;submitLabel?:string;normalize?:(fields:Record<string,unknown>)=>Record<string,unknown>}
/** Explicit mutation surface. Server validations remain authoritative; no operation fires from an effect. */
export function AdminAction({spec,onComplete,children}:{spec:ActionSpec;onComplete?:()=>void;children?:ReactNode}){
 const client=useQueryClient()
 const allowed=canAccessPath(spec.path,true)
 const [values,setValues]=useState<Record<string,string>>(()=>Object.fromEntries(spec.fields.map(f=>[f.key,f.initial??(f.kind==='boolean'?'false':'')])))
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [message,setMessage]=useState('');const [ack,setAck]=useState('');const [result,setResult]=useState<unknown>(null)
 async function submit(e:FormEvent<HTMLFormElement>){e.preventDefault();setError('');setMessage('');setResult(null)
  if(spec.confirmation && ack!==spec.confirmation){setError(`Для подтверждения введите: ${spec.confirmation}`);return}
  const payload:Record<string,unknown>={};
  for(const f of spec.fields){const raw=(values[f.key]??'').trim();if(f.required&&!raw){setError(`Заполните поле «${f.label}»`);return}
   if(!raw&&f.kind!=='boolean')continue
   if(f.kind==='number'){const n=Number(raw);if(!Number.isFinite(n)){setError(`Неверное число: ${f.label}`);return}payload[f.key]=n}
   else if(f.kind==='boolean')payload[f.key]=raw==='true'
   else if(f.kind==='json'){try{payload[f.key]=JSON.parse(raw)}catch{setError(`Неверный JSON: ${f.label}`);return}}
   else payload[f.key]=raw
  }
  const body=spec.normalize?spec.normalize(payload):payload;let path=spec.path
  if(spec.queryKeys?.length){const search=new URLSearchParams();for(const key of spec.queryKeys){const v=body[key];if(v!==undefined&&v!==null)search.set(key,String(v));delete body[key]}const q=search.toString();if(q)path+=`${path.includes('?')?'&':'?'}${q}`}
  setBusy(true)
  try{const response=await api<unknown>(path,{method:spec.method??'POST',...(spec.method==='DELETE'?{}:{body:JSON.stringify(body)})});setResult(response);setMessage('Операция подтверждена сервером.');await client.invalidateQueries();onComplete?.()}
  catch(err){setError(err instanceof Error?err.message:String(err))}finally{setBusy(false)}
 }
 if(!allowed)return <Card><CardContent className="p-5 text-sm text-muted-foreground">Операция недоступна для вашей роли.</CardContent></Card>
 return <Card><CardHeader><CardTitle className="text-base">{spec.title}</CardTitle>{spec.description&&<CardDescription>{spec.description}</CardDescription>}</CardHeader><CardContent><form onSubmit={submit} className="space-y-3">{children}
  <div className="grid gap-3 md:grid-cols-2">{spec.fields.map(f=><label key={f.key} className={f.kind==='textarea'||f.kind==='json'?'md:col-span-2':''}><span className="mb-1.5 block text-xs font-medium">{f.label}{f.required?' *':''}</span>{f.kind==='textarea'||f.kind==='json'?<Textarea value={values[f.key]??''} rows={f.kind==='json'?5:3} placeholder={f.placeholder} onChange={e=>setValues(p=>({...p,[f.key]:e.target.value}))}/>:f.kind==='boolean'||f.kind==='select'?<select className="h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" value={values[f.key]??''} onChange={e=>setValues(p=>({...p,[f.key]:e.target.value}))}>{(f.kind==='boolean'?['false','true']:f.options??[]).map(x=><option key={x} value={x}>{x==='true'?'Да':x==='false'?'Нет':x}</option>)}</select>:<Input type={f.kind==='date'?'date':f.kind==='number'?'number':'text'} step={f.kind==='number'?'any':undefined} value={values[f.key]??''} placeholder={f.placeholder} onChange={e=>setValues(p=>({...p,[f.key]:e.target.value}))}/>}</label>)}</div>
  {spec.confirmation&&<label className="block text-sm"><span className="mb-1.5 block font-medium">Подтверждение: введите «{spec.confirmation}»</span><Input autoComplete="off" value={ack} onChange={e=>setAck(e.target.value)} /></label>}
  {error&&<p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}{message&&<p role="status" className="rounded-lg bg-muted p-3 text-sm">{message}</p>}{result!==null&&<details className="rounded-lg border border-border p-3"><summary className="cursor-pointer text-sm font-medium">Ответ сервера</summary><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(result,null,2)}</pre></details>}
  <Button disabled={busy||Boolean(spec.confirmation&&ack!==spec.confirmation)} type="submit">{busy?'Выполняется…':spec.submitLabel??'Сохранить'}</Button>
 </form></CardContent></Card>
}
