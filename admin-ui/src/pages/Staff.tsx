import {useState} from 'react'
import type {FormEvent} from 'react'
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query'
import {ShieldCheck,UserPlus,History,AlertTriangle,MonitorSmartphone} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Input} from '@/components/ui/input'
import {Card,CardContent,CardHeader,CardTitle,CardDescription} from '@/components/ui/card'
import {Badge} from '@/components/ui/badge'
import {api,currentStaff,isStaffOwner} from '@/lib/api'
import type {StaffIdentity,StaffRole} from '@/lib/api'
import {ErrorBox,Heading,Loading} from '@/components/page'

const ROLES: {id:StaffRole;label:string}[]=[
 {id:'owner',label:'Владелец — все права'},
 {id:'operator',label:'Оператор — заказы, склады и предложения'},
 {id:'catalog_manager',label:'Каталог — товары и атрибуты'},
 {id:'supplier_manager',label:'Поставщики — API и прайсы'},
 {id:'finance_manager',label:'Финансы — настройки и модели'},
 {id:'analyst',label:'Аналитик — только чтение'},
 {id:'viewer',label:'Наблюдатель — ограниченное чтение'},
]
type Audit={at:string;actor_id:string|null;actor_kind:string;action:string;path:string;scope:string}
const ROOT='/api/v1/admin/staff'
type MySession={id:string;created_at:string;expires_at:string;current:boolean}
export function MySessions(){
 const client=useQueryClient()
 const [error,setError]=useState('')
 const sessions=useQuery({queryKey:['staff','sessions'],queryFn:()=>api<MySession[]>(`${ROOT}/sessions`),enabled:!!currentStaff()})
 const revoke=useMutation({mutationFn:(id:string)=>api<{status:string;current:boolean}>(`${ROOT}/sessions/${id}`,{method:'DELETE'}),onSuccess:response=>{
  if(response.current){window.location.assign('/admin/');return}
  void client.invalidateQueries({queryKey:['staff','sessions']});setError('')
 },onError:e=>setError(e instanceof Error?e.message:'Не удалось отозвать сеанс')})
 const revokeOthers=useMutation({mutationFn:()=>api<{revoked:number}>(`${ROOT}/sessions/revoke-others`,{method:'POST'}),onSuccess:()=>{void client.invalidateQueries({queryKey:['staff','sessions']});setError('')},onError:e=>setError(e instanceof Error?e.message:'Ошибка отзыва сеансов')})
 if(!currentStaff())return <p className="text-sm text-muted-foreground">Управление сеансами доступно только при личном входе сотрудника.</p>
 return <Card><CardHeader><CardTitle className="flex items-center gap-2"><MonitorSmartphone size={18}/>Мои административные сеансы</CardTitle><CardDescription>Сеансы админки отделены от аккаунтов покупателей. Можно завершить вход на другом устройстве без смены пароля.</CardDescription></CardHeader><CardContent className="space-y-3">
  <Button variant="outline" disabled={revokeOthers.isPending} onClick={()=>{if(window.confirm('Завершить все другие административные сеансы?'))revokeOthers.mutate()}}>Завершить другие сеансы</Button>
  {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
  {sessions.isPending?<Loading/>:sessions.error?<ErrorBox error={sessions.error} onRetry={()=>void sessions.refetch()}/>:sessions.data?.map(s=><div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border p-3" key={s.id}>
   <div className="space-y-1 text-xs"><strong>{s.current?'Текущий сеанс':'Другой сеанс'}</strong><div className="text-muted-foreground">Создан: {new Date(s.created_at).toLocaleString('ru-RU')} · до {new Date(s.expires_at).toLocaleString('ru-RU')}</div></div>
   <Button size="sm" variant="outline" disabled={revoke.isPending} onClick={()=>{if(window.confirm(s.current?'Завершить текущий сеанс и выйти?':'Завершить выбранный сеанс?'))revoke.mutate(s.id)}}>Завершить</Button>
  </div>)}
 </CardContent></Card>
}

export function Staff(){
 const client=useQueryClient()
 const [email,setEmail]=useState('');const [displayName,setDisplayName]=useState('');const [password,setPassword]=useState('')
 const [role,setRole]=useState<StaffRole>('viewer');const [error,setError]=useState('');const [success,setSuccess]=useState('')
 const [auditOpen,setAuditOpen]=useState(false)
 const people=useQuery({queryKey:['staff'],queryFn:()=>api<StaffIdentity[]>(ROOT),enabled:isStaffOwner()})
 const journal=useQuery({queryKey:['staff','audit'],queryFn:()=>api<Audit[]>(`${ROOT}/audit?limit=40`),enabled:isStaffOwner()&&auditOpen})
 const update=useMutation({mutationFn:({id,change}:{id:string;change:{role?:StaffRole;enabled?:boolean}})=>api<StaffIdentity>(`${ROOT}/${id}`,{method:'PATCH',body:JSON.stringify(change)}),onSuccess:()=>{void client.invalidateQueries({queryKey:['staff']});setError('')},onError:e=>setError(e instanceof Error?e.message:'Не удалось обновить')})
 const create=useMutation({mutationFn:()=>api<StaffIdentity>(ROOT,{method:'POST',body:JSON.stringify({email,password,display_name:displayName,role})}),onSuccess:()=>{setEmail('');setDisplayName('');setPassword('');setRole('viewer');setSuccess('Сотрудник создан');setError('');void client.invalidateQueries({queryKey:['staff']})},onError:e=>setError(e instanceof Error?e.message:'Не удалось создать')})
 function submit(e:FormEvent){e.preventDefault();setSuccess('');setError('');create.mutate()}
 if(!isStaffOwner())return <div><Heading title="Сотрудники и доступ" description="Раздел доступен только владельцу"/><p className="text-sm text-muted-foreground">У текущей учётной записи нет прав управления сотрудниками.</p></div>
 return <div className="space-y-6">
  <Heading title="Сотрудники и доступ" description="Серверные роли и индивидуальные учётные записи. Ограничения применяются в FastAPI независимо от интерфейса."/>
  <div className="flex items-start gap-2 rounded-xl border border-border bg-muted/35 px-4 py-3 text-xs text-muted-foreground"><ShieldCheck size={17} className="shrink-0"/><span>Общий X-Admin-Token пока остаётся аварийным доступом. После проверки входа владельца его нужно отключить через ADMIN_LEGACY_TOKEN_ENABLED=false. Не отправляйте пароль или токен в чат.</span></div>
  <Card><CardHeader><CardTitle className="flex items-center gap-2"><UserPlus size={18}/>Новый сотрудник</CardTitle><CardDescription>Создаётся отдельная учётная запись. Пароль хранится на сервере только в виде Argon2id-хеша.</CardDescription></CardHeader><CardContent><form onSubmit={submit} className="grid gap-3 md:grid-cols-2">
   <label className="space-y-1 text-xs font-medium">Имя<Input required maxLength={160} value={displayName} onChange={e=>setDisplayName(e.target.value)} placeholder="Имя сотрудника"/></label>
   <label className="space-y-1 text-xs font-medium">Email<Input type="email" required value={email} onChange={e=>setEmail(e.target.value)} placeholder="operator@example.com"/></label>
   <label className="space-y-1 text-xs font-medium">Пароль<Input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={password} onChange={e=>setPassword(e.target.value)} placeholder="Не менее 12 символов"/></label>
   <label className="space-y-1 text-xs font-medium">Роль<select aria-label="Роль нового сотрудника" value={role} onChange={e=>setRole(e.target.value as StaffRole)} className="flex h-10 w-full rounded-lg border border-input bg-background px-3 text-sm">{ROLES.map(r=><option value={r.id} key={r.id}>{r.label}</option>)}</select></label>
   <div className="md:col-span-2"><Button type="submit" disabled={create.isPending}>{create.isPending?'Создаём…':'Создать сотрудника'}</Button></div>
  </form></CardContent></Card>
  {error&&<p role="alert" className="flex gap-2 text-sm text-destructive"><AlertTriangle size={16}/>{error}</p>}{success&&<p role="status" className="text-sm">{success}</p>}
  <MySessions/>
  <Card><CardHeader><CardTitle>Учётные записи</CardTitle><CardDescription>Роль и активность проверяются при каждом запросе. Отключение запрещает дальнейшие действия немедленно.</CardDescription></CardHeader><CardContent>
   {people.isPending?<Loading/>:people.error?<ErrorBox error={people.error} onRetry={()=>void people.refetch()}/>:<div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm"><thead><tr className="border-b text-xs text-muted-foreground"><th className="p-3">Сотрудник</th><th className="p-3">Роль</th><th className="p-3">Доступ</th><th className="p-3">Действия</th></tr></thead><tbody>{people.data?.map(user=><tr key={user.id} className="border-b border-border/60"><td className="p-3"><div className="font-medium">{user.display_name}</div><div className="text-xs text-muted-foreground">{user.email}</div></td><td className="p-3"><select aria-label={`Роль ${user.email}`} value={user.role} disabled={update.isPending} className="rounded-lg border border-input bg-background p-2 text-xs" onChange={e=>update.mutate({id:user.id,change:{role:e.target.value as StaffRole}})}>{ROLES.map(r=><option value={r.id} key={r.id}>{r.label}</option>)}</select></td><td className="p-3"><Badge variant={user.enabled?'success':'secondary'}>{user.enabled?'Активен':'Отключён'}</Badge></td><td className="p-3"><Button variant="outline" size="sm" disabled={update.isPending||user.user_id===currentStaff()?.user_id} onClick={()=>{if(window.confirm(user.enabled?`Отключить доступ ${user.email}?`:`Включить доступ ${user.email}?`))update.mutate({id:user.id,change:{enabled:!user.enabled}})}}>{user.enabled?'Отключить':'Включить'}</Button></td></tr>)}</tbody></table></div>}
  </CardContent></Card>
  <Card><CardHeader><CardTitle className="flex items-center gap-2"><History size={18}/>Журнал административных действий</CardTitle><CardDescription>Фиксируются попытки изменить данные через защищённые административные API. Не является доказательством успешности каждой операции.</CardDescription></CardHeader><CardContent><Button variant="outline" onClick={()=>setAuditOpen(x=>!x)}>{auditOpen?'Скрыть':'Показать последние 40 записей'}</Button>{auditOpen&&(journal.isPending?<Loading/>:journal.error?<ErrorBox error={journal.error} onRetry={()=>void journal.refetch()}/>:<div className="mt-4 space-y-2">{journal.data?.map((entry,i)=><div key={`${entry.at}-${i}`} className="flex flex-wrap gap-2 rounded-lg border p-3 text-xs"><span>{new Date(entry.at).toLocaleString('ru-RU')}</span><strong>{entry.action}</strong><code className="break-all">{entry.path}</code><span className="text-muted-foreground">{entry.scope} · {entry.actor_kind}</span></div>)}</div>)}</CardContent></Card>
 </div>
}

/** Password rotation always invalidates every existing administrative session.
 * No password or CSRF value is written to browser storage. */
export function MySecurity(){
 const client=useQueryClient()
 const [currentPassword,setCurrentPassword]=useState('')
 const [newPassword,setNewPassword]=useState('')
 const [confirmation,setConfirmation]=useState('')
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const available=Boolean(currentStaff())
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setError('')
  if(!available){setError('Войдите с личной учётной записью сотрудника');return}
  if(newPassword.length<12){setError('Новый пароль должен содержать не менее 12 символов');return}
  if(newPassword!==confirmation){setError('Подтверждение не совпадает с новым паролем');return}
  if(newPassword===currentPassword){setError('Новый пароль должен отличаться от текущего');return}
  setBusy(true)
  try{
   await api<{status:string;sessions_revoked:boolean}>('/api/v1/admin/staff/change-password',{
    method:'POST',body:JSON.stringify({current_password:currentPassword,new_password:newPassword})
   })
   setCurrentPassword('');setNewPassword('');setConfirmation('')
   client.clear()
   // The server has revoked every admin session and cleared the HttpOnly cookie.
   // A full navigation discards all in-memory credentials and cached API data.
   window.location.replace('/admin/')
  }catch(e){setError(e instanceof Error?e.message:'Не удалось изменить пароль')}
  finally{setBusy(false)}
 }
 return <div className="space-y-6">
  <Heading title="Безопасность входа" description="Изменение личного пароля и принудительное завершение всех административных сеансов."/>
  {!available?<Card><CardContent className="p-5 text-sm text-muted-foreground">Для смены пароля войдите по email и паролю сотрудника. Аварийный административный токен не подходит.</CardContent></Card>:
  <Card className="max-w-xl"><CardHeader><CardTitle>Сменить пароль</CardTitle><CardDescription>После сохранения вход на всех устройствах завершится. Повторно войдите с новым паролем. Сеансы покупателей не затрагиваются.</CardDescription></CardHeader><CardContent>
   <form className="space-y-4" onSubmit={submit}>
    <label className="block space-y-1.5 text-sm font-medium">Текущий пароль<Input type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={e=>setCurrentPassword(e.target.value)}/></label>
    <label className="block space-y-1.5 text-sm font-medium">Новый пароль<Input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={newPassword} onChange={e=>setNewPassword(e.target.value)}/><span className="block text-xs font-normal text-muted-foreground">Не менее 12 символов. Используйте уникальный пароль.</span></label>
    <label className="block space-y-1.5 text-sm font-medium">Повторите новый пароль<Input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label>
    {error&&<p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
    <Button disabled={busy||!currentPassword||!newPassword||!confirmation} type="submit">{busy?'Проверка и завершение сеансов…':'Обновить пароль и завершить сеансы'}</Button>
   </form>
  </CardContent></Card>}
 </div>
}
