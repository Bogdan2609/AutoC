import {useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {AlertTriangle,CheckCircle2,Globe,LockKeyhole} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Badge} from '@/components/ui/badge'
import {Card,CardContent,CardDescription,CardHeader,CardTitle} from '@/components/ui/card'
import {api,canAccessPath} from '@/lib/api'
import type {ProductIdentity} from '@/lib/types'

/** Publication is ALWAYS a separate operator decision, never a side-effect of saving. */
export function ProductPublication({product,unsaved=false}:{product:ProductIdentity;unsaved?:boolean}){
 const client=useQueryClient()
 const [confirmation,setConfirmation]=useState('')
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [message,setMessage]=useState('')
 const published=product.status==='published'
 const path=published?`/api/v1/backoffice/catalog-editor/products/${product.id}/unpublish`:`/api/v1/backoffice/products/${product.id}/publish`
 const allowed=canAccessPath(path,true)
 const keyword=published?'СНЯТЬ':'ОПУБЛИКОВАТЬ'
 const missing=product.missing_required??[]
 async function commit(){
  if(!allowed||busy||unsaved||confirmation!==keyword)return
  setBusy(true);setError('');setMessage('')
  try{
   await api(path,{method:'POST'})
   await Promise.all([client.invalidateQueries({queryKey:['product-identity',product.id]}),client.invalidateQueries({queryKey:['catalog']})])
   setConfirmation('')
   setMessage(published?'Товар возвращён в черновики.':'Сервер подтвердил публикацию товара.')
  }catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{setBusy(false)}
 }
 return <Card><CardHeader><CardTitle className="flex items-center gap-2"><Globe size={18}/> Публикация товара</CardTitle><CardDescription>Отдельное подтверждаемое действие. Сервер проверяет обязательные характеристики, ограничения комплектов и готовность к публикации.</CardDescription></CardHeader><CardContent className="space-y-4">
  <div className="flex flex-wrap items-center gap-3"><Badge variant={published?'success':'warning'}>{published?'Опубликован':'Не опубликован'}</Badge><span className="text-sm text-muted-foreground">Текущий статус: {product.status}</span></div>
  {missing.length>0&&<div role="alert" className="rounded-lg border border-border bg-muted/40 p-3 text-sm"><p className="mb-2 flex items-center gap-2 font-medium"><AlertTriangle size={16}/> Отсутствуют обязательные данные ({missing.length})</p><ul className="list-inside list-disc text-xs text-muted-foreground">{missing.map((field,i)=><li key={i}>{String(field)}</li>)}</ul></div>}
  {!published&&<div className="flex items-start gap-2 text-xs text-muted-foreground"><LockKeyhole size={16} className="shrink-0"/> Даже при отсутствии замечаний на экране серверная проверка может отклонить публикацию (HTTP 409). Сохранение карточки не запускает публикацию.</div>}
  {unsaved&&<p role="alert" className="text-sm text-destructive">Есть несохранённые изменения карточки. Сначала сохраните их.</p>}
  {!allowed?<p className="text-sm text-muted-foreground">У вашей роли нет права изменять статус публикации.</p>:product.status==='archived'?<p className="text-sm text-muted-foreground">Архивный товар нельзя опубликовать. Сервер запрещает эту операцию.</p>:<div className="flex flex-wrap items-end gap-3"><label className="flex-1 text-xs font-medium">Введите «{keyword}» для подтверждения<input className="mt-2 block h-10 w-full rounded-lg border border-input bg-background px-3 text-sm" autoComplete="off" value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label><Button type="button" variant={published?'outline':'default'} disabled={busy||unsaved||confirmation!==keyword} onClick={()=>void commit()}>{busy?'Выполняется…':published?'Снять с публикации':'Опубликовать товар'}</Button></div>}
  {message&&<p role="status" className="flex items-center gap-2 text-sm"><CheckCircle2 size={16}/>{message}</p>}
  {error&&<p role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">{error} — проверьте условия публикации и обновите карточку перед повторным действием.</p>}
 </CardContent></Card>
}
