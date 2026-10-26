import {useEffect,useState} from 'react'
import {Download,ImageOff} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {apiBinary} from '@/lib/api'

/** Binary preview uses the same RBAC/session as JSON APIs. Never put tokens into an <img src>. */
export function SecureMediaPreview({id,kind,mimeType,revision}:{id:string;kind:string;mimeType:string;revision?:string}){
 const [url,setUrl]=useState<string|null>(null)
 const [error,setError]=useState('')
 const visual=kind!=='document'&&['image/png','image/jpeg','image/webp'].includes(mimeType)
 useEffect(()=>{
  if(!visual)return
  const controller=new AbortController()
  let objectUrl:string|null=null
  setUrl(null);setError('')
  void apiBinary(`/api/v1/backoffice/catalog-v021/media/${encodeURIComponent(id)}/preview?variant=thumb`,controller.signal)
   .then(blob=>{
    if(controller.signal.aborted)return
    if(!['image/png','image/jpeg','image/webp'].includes(blob.type))throw Error('Неожиданный тип файла')
    objectUrl=URL.createObjectURL(blob);setUrl(objectUrl)
   })
   .catch(err=>{if(!controller.signal.aborted)setError(err instanceof Error?err.message:'Недоступно')})
  return ()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[id,mimeType,visual,revision])
 if(!visual)return <div className="flex h-32 items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground">Предпросмотр для этого типа недоступен</div>
 return <div className="flex h-40 items-center justify-center overflow-hidden rounded-lg border border-border bg-muted/40">{url?<img src={url} alt="Предпросмотр медиафайла" className="h-full w-full object-contain"/>:error?<span className="flex items-center gap-2 p-3 text-xs text-muted-foreground"><ImageOff size={18}/>{error}</span>:<span className="text-xs text-muted-foreground">Загрузка миниатюры…</span>}</div>
}
export function SecureMediaDownload({id,filename,disabled=false}:{id:string;filename:string;disabled?:boolean}){
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 async function download(){
  setBusy(true);setError('')
  try{
   const blob=await apiBinary(`/api/v1/backoffice/catalog-v021/media/${encodeURIComponent(id)}/preview?variant=original`)
   const url=URL.createObjectURL(blob)
   try{
    const link=document.createElement('a');link.href=url;link.download=filename.split(/[\\/]/).pop()||'media';document.body.append(link);link.click();link.remove()
   }finally{window.setTimeout(()=>URL.revokeObjectURL(url),30_000)}
  }catch(e){setError(e instanceof Error?e.message:'Файл недоступен')}
  finally{setBusy(false)}
 }
 return <div className="flex flex-wrap items-center gap-2"><Button size="sm" variant="outline" type="button" disabled={disabled||busy} onClick={()=>void download()}><Download size={14}/>{busy?'Загрузка…':'Скачать оригинал'}</Button>{error&&<span role="alert" className="text-xs text-destructive">{error}</span>}</div>
}
