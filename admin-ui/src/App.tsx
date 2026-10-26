import {useEffect, useRef, useState} from 'react'
import type {FormEvent,ReactNode} from 'react'
import {BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate} from 'react-router-dom'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {
  Activity, ArrowRight, Boxes, ChartColumnIncreasing, ChevronRight, ChevronsLeft,
  ChevronsRight, CircleHelp, ClipboardList, Command, ExternalLink, LayoutGrid,
  LogOut, Menu, Moon, PackageSearch, RefreshCw, Search, Settings2, ShieldCheck,
  Store, Sun, Truck, Warehouse, Wallet, X
} from 'lucide-react'
import type {LucideIcon} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '@/components/ui/card'
import {Input} from '@/components/ui/input'
import {Separator} from '@/components/ui/separator'
import {api,setOperatorToken,restoreStaffSession,loginStaff,logoutAuth,clearOperatorAuth,currentStaff,canAccess,WORKSPACE} from '@/lib/api'
import {cn} from '@/lib/utils'
import {Dashboard} from '@/pages/Dashboard'
import {Orders,OrderView} from '@/pages/Orders'
import {Catalog,ProductView} from '@/pages/Catalog'
import {Suppliers,SupplierView} from '@/pages/Suppliers'
import {Section} from '@/pages/Section'
import {SupplierFeeds} from '@/pages/SupplierFeeds'
import {CatalogAutomation} from '@/pages/CatalogAutomation'
import {CategoryTemplates} from '@/pages/CategoryTemplates'
import {BrandDirectory,BrandStudio,FamilyStudio} from '@/pages/CatalogStudio'
import {SupplierControl} from '@/pages/SupplierControl'
import {FinanceStudio} from '@/pages/FinanceStudio'
import {MigrationAudit} from '@/pages/MigrationAudit'
import {Staff,MySessions,MySecurity} from '@/pages/Staff'

const client=new QueryClient({defaultOptions:{queries:{staleTime:30_000,refetchOnWindowFocus:false,retry:1}}})
type NavigationItem = {
  to: string
  name: string
  icon: LucideIcon
  description: string
}
type NavigationGroup = { caption: string; items: NavigationItem[] }

const groups: NavigationGroup[] = [
 {caption:'Рабочее пространство', items:[
  {to:'/',name:'Обзор',icon:LayoutGrid,description:'Сводка и важные задачи'},
  {to:'/orders',name:'Заказы',icon:ClipboardList,description:'Продажи и закупки'},
  {to:'/catalog',name:'Каталог',icon:Boxes,description:'Товары и характеристики'},
  {to:'/suppliers',name:'Поставщики',icon:Truck,description:'Подключения и маршруты'},
 ]},
 {caption:'Управление', items:[
  {to:'/warehouses',name:'Склады и логистика',icon:Warehouse,description:'Остатки и приёмка'},
  {to:'/marketplace',name:'Маркетплейс',icon:Store,description:'Продавцы и предложения'},
  {to:'/finance',name:'Финансы',icon:Wallet,description:'Платежи и расчёты'},
  {to:'/analytics',name:'Аналитика',icon:ChartColumnIncreasing,description:'Показатели и цены'},
 ]},
 {caption:'Платформа',items:[
  {to:'/system',name:'Система',icon:Settings2,description:'Мониторинг и настройки'},
  {to:'/system/staff',name:'Сотрудники',icon:ShieldCheck,description:'Роли, права и аудит'},
  {to:'/system/sessions',name:'Мои сеансы',icon:ShieldCheck,description:'Устройства и активные сеансы'},
  {to:'/system/security',name:'Безопасность входа',icon:ShieldCheck,description:'Смена личного пароля'},
 ]},
]
const navigation: NavigationItem[] = groups.flatMap(g => g.items)
function navigationScope(to:string):string{
 if(to==='/system/staff')return 'staff'
 if(to==='/system/sessions'||to==='/system/security')return 'sessions'
 if(to==='/')return 'analytics'
 if(to.startsWith('/orders'))return 'orders'
 if(to.startsWith('/catalog'))return 'catalog'
 if(to.startsWith('/suppliers'))return 'suppliers'
 if(to.startsWith('/warehouses'))return 'warehouses'
 if(to.startsWith('/marketplace'))return 'marketplace'
 if(to.startsWith('/finance'))return 'finance'
 if(to.startsWith('/analytics'))return 'analytics'
 return 'system'
}
function canShowNavigation(to:string){return (to==='/system/sessions'||to==='/system/security')&&!currentStaff()?false:canAccess(navigationScope(to))}
function ScopeGate({scope,children}:{scope:string;children:ReactNode}){
 return canAccess(scope)?<>{children}</>:<section role="alert" className="mx-auto max-w-xl rounded-2xl border border-border p-8 text-center"><ShieldCheck className="mx-auto mb-4" size={30}/><h2 className="text-xl font-semibold">Нет доступа к разделу</h2><p className="mt-2 text-sm text-muted-foreground">Раздел или действие не разрешены ролью сотрудника. Права определяет сервер.</p><Link to="/" className="mt-5 inline-block text-sm underline">Вернуться на главную</Link></section>
}

function Logo({compact=false}:{compact?:boolean}){
 return <Link to="/" aria-label="Autocodex — главная" className="group flex min-w-0 items-center gap-3">
  <span className="relative flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-primary text-primary-foreground shadow-sm"><span className="relative z-10 text-[16px] font-black tracking-[-.12em]">ac<span className="text-[9px] align-top">/</span></span><span className="pointer-events-none absolute -right-2 -bottom-4 size-9 rotate-45 border border-primary-foreground/35"/></span>
  {!compact&&<span className="min-w-0 leading-none"><span className="block text-[16px] font-bold tracking-[-.065em] text-sidebar-foreground">autocodex<span className="text-muted-foreground">.</span></span><span className="mt-1.5 block text-[9px] font-bold tracking-[.18em] text-muted-foreground">OPERATIONS STUDIO</span></span>}
 </Link>
}
function Login({onSuccess}:{onSuccess:()=>void}){
 const [mode,setMode]=useState<'staff'|'legacy'>('staff')
 const [token,setToken]=useState('');const [email,setEmail]=useState('');const [password,setPassword]=useState('')
 const [pending,setPending]=useState(false);const [error,setError]=useState('')
 async function login(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setPending(true);setError('')
  try{
   if(mode==='staff')await loginStaff(email.trim(),password)
   else{setOperatorToken(token.trim());try{await api(`${WORKSPACE}/summary`)}catch(err){clearOperatorAuth();throw err}}
   setPassword('');setToken('');onSuccess()
  }catch(e){setError(e instanceof Error?e.message:'Ошибка авторизации')}
  finally{setPending(false)}
 }
 return <main className="relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-12">
  <div className="adm-dotfield absolute inset-y-0 right-0 -z-10 w-[42%] opacity-55" aria-hidden="true"/>
  <div className="w-full max-w-[420px]"><div className="mb-11"><Logo/><div className="mt-12 flex items-center gap-3 text-[10px] font-semibold uppercase tracking-[.2em] text-muted-foreground"><span className="h-px w-7 bg-foreground"/> Рабочее пространство / 01</div><h1 className="mt-4 text-[36px] font-semibold leading-[1.08] tracking-[-.055em]">Управление<br/>без лишнего шума.</h1><p className="mt-4 max-w-xs text-sm leading-relaxed text-muted-foreground">Каталог, поставщики и заказы в одной операционной системе.</p></div>
   <Card className="rounded-2xl"><CardHeader><CardTitle className="text-[18px]">Вход в Autocodex</CardTitle><CardDescription>{mode==='staff'?'Личная учётная запись сотрудника. Сеанс хранится в защищённом HttpOnly cookie.':'Временный аварийный вход общим административным токеном.'}</CardDescription></CardHeader><CardContent>
    <div className="mb-5 flex gap-2" role="group" aria-label="Способ входа"><Button type="button" variant={mode==='staff'?'default':'outline'} onClick={()=>{setMode('staff');setError('')}}>Сотрудник</Button><Button type="button" variant={mode==='legacy'?'default':'outline'} onClick={()=>{setMode('legacy');setError('')}}>Админ-токен</Button></div>
    <form onSubmit={login} className="space-y-4">
     {mode==='staff'?<><label className="block space-y-2 text-xs font-semibold">Email<Input autoFocus type="email" autoComplete="username" value={email} onChange={e=>setEmail(e.target.value)} required placeholder="operator@example.com"/></label><label className="block space-y-2 text-xs font-semibold">Пароль<Input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required /></label></>:<label className="block space-y-2 text-xs font-semibold">Административный токен<Input autoFocus type="password" value={token} onChange={e=>setToken(e.target.value)} autoComplete="off" spellCheck={false} required placeholder="Токен аварийного доступа" /></label>}
     {error&&<p role="alert" className="text-sm text-destructive">{error}</p>}
     <Button className="h-11 w-full justify-between" type="submit" disabled={pending||!(mode==='staff'?email&&password:token.trim())}>{pending?'Проверяем доступ…':'Войти в Autocodex'}<ArrowRight size={17}/></Button>
    </form>
   </CardContent></Card><p className="mt-5 flex items-center justify-center gap-2 text-xs text-muted-foreground"><ShieldCheck size={14}/> Учетные записи · Проверка прав на сервере</p>
  </div>
 </main>
}
function ThemeButton(){
 const [dark,setDark]=useState(()=>{try{return localStorage.getItem('autocodex-admin-theme')==='dark'}catch{return false}})
 useEffect(()=>{document.documentElement.classList.toggle('dark',dark);try{localStorage.setItem('autocodex-admin-theme',dark?'dark':'light')}catch{/* storage disabled */}},[dark])
 return <Button variant="ghost" size="icon" onClick={()=>setDark(v=>!v)} title={dark?'Светлая тема':'Тёмная тема'} aria-label={dark?'Светлая тема':'Тёмная тема'}>{dark?<Sun size={18}/>:<Moon size={18}/>}</Button>
}
function QuickSearch({navigate}:{navigate:(path:string)=>void}){
 const dialog=useRef<HTMLDialogElement>(null)
 const [query,setQuery]=useState('')
 const [shown,setShown]=useState(false)
 const queryLower=query.trim().toLocaleLowerCase('ru-RU')
 const choices=navigation.filter(n=>canShowNavigation(n.to)).filter(n=>(n.name+' '+n.description).toLocaleLowerCase('ru-RU').includes(queryLower))
 useEffect(()=>{const handler=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();dialog.current?.showModal();setShown(true)}};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[])
 function close(){dialog.current?.close();setShown(false);setQuery('')}
 function go(path:string){close();navigate(path)}
 function submit(event:FormEvent){event.preventDefault();if(query.trim())go('/catalog?'+new URLSearchParams({q:query.trim()}).toString())}
 return <><Button variant="outline" className="h-9 w-9 gap-3 px-0 text-muted-foreground sm:w-[240px] sm:justify-start sm:px-3 xl:w-[290px]" onClick={()=>{dialog.current?.showModal();setShown(true)}} aria-label="Глобальный поиск"><Search size={16}/><span className="hidden flex-1 text-left text-xs font-normal sm:block">Найти раздел или артикул...</span><kbd className="hidden rounded border border-border px-1.5 py-0.5 font-mono text-[10px] md:inline">Ctrl K</kbd></Button>
 <dialog ref={dialog} onClose={()=>{setShown(false);setQuery('')}} aria-label="Поиск и переход" className="fixed inset-0 m-auto w-[min(94vw,560px)] max-h-[80vh] overflow-hidden rounded-2xl border border-border bg-popover p-0 text-popover-foreground shadow-2xl backdrop:bg-black/40">
  {shown&&<div><form onSubmit={submit} className="flex items-center gap-3 border-b border-border px-5 py-4"><Search className="shrink-0 text-muted-foreground" size={19}/><input autoFocus type="search" aria-label="Поиск раздела или товара" className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground" placeholder="Раздел, товар, артикул…" value={query} onChange={e=>setQuery(e.target.value)}/><button type="button" onClick={close} aria-label="Закрыть поиск"><X size={17}/></button></form><div className="max-h-[50vh] overflow-y-auto p-3"><p className="px-3 py-2 text-[10px] font-bold uppercase tracking-[.17em] text-muted-foreground">Быстрые переходы</p>{choices.length?choices.map(({to,name,icon:Icon,description})=><button key={to} type="button" className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors hover:bg-accent" onClick={()=>go(to)}><Icon size={18} className="text-muted-foreground"/><span className="flex-1"><span className="block text-sm font-medium">{name}</span><span className="block text-xs text-muted-foreground">{description}</span></span><ChevronRight size={16} className="text-muted-foreground"/></button>):<p className="p-4 text-sm text-muted-foreground">Раздел не найден. Нажмите Enter для поиска по каталогу.</p>}</div><div className="border-t border-border bg-muted/35 px-5 py-3 text-xs text-muted-foreground">Enter — найти в каталоге · Esc — закрыть</div></div>}
 </dialog></>
}
function Shell({onLogout}:{onLogout:()=>void}){
 const [collapsed,setCollapsed]=useState(false)
 const [mobileOpen,setMobileOpen]=useState(false)
 const location=useLocation();const navigate=useNavigate()
 const isCompact=collapsed&&!mobileOpen
 useEffect(()=>{setMobileOpen(false)},[location.pathname])
 useEffect(()=>{if(!mobileOpen)return;const handler=(e:KeyboardEvent)=>{if(e.key==='Escape')setMobileOpen(false)};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler)},[mobileOpen])
 const current=navigation.find(n=>n.to==='/'?location.pathname==='/':location.pathname===n.to||location.pathname.startsWith(n.to+'/'))||navigation[0]
 return <div className="flex min-h-screen bg-background">
  {mobileOpen&&<button type="button" onClick={()=>setMobileOpen(false)} aria-label="Закрыть навигацию" className="fixed inset-0 z-30 bg-black/45 lg:hidden"/>}
  <aside aria-label="Основная навигация" className={cn('adm-rail fixed inset-y-0 left-0 z-40 flex flex-col transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:h-screen',collapsed?'w-[266px] lg:w-[78px]':'w-[266px]',mobileOpen?'translate-x-0':'-translate-x-full lg:translate-x-0')}>
   <div className="flex h-[82px] shrink-0 items-center justify-between gap-1 px-5"><Logo compact={isCompact}/><Button variant="ghost" size="icon-sm" className="hidden shrink-0 text-muted-foreground lg:inline-flex" onClick={()=>setCollapsed(x=>!x)} title={collapsed?'Развернуть меню':'Свернуть меню'} aria-label={collapsed?'Развернуть меню':'Свернуть меню'}>{collapsed?<ChevronsRight size={17}/>:<ChevronsLeft size={17}/>}</Button><Button variant="ghost" size="icon-sm" className="lg:hidden" onClick={()=>setMobileOpen(false)} aria-label="Закрыть меню"><X size={18}/></Button></div>
   <Separator className="bg-sidebar-border"/>
   <div className={cn('flex-1 overflow-y-auto py-6',isCompact?'px-3':'px-5')}><div className="space-y-7">{groups.map(g=><nav key={g.caption} aria-label={g.caption}><p className={cn('mb-3 px-3 text-[10px] font-bold uppercase tracking-[.17em] text-muted-foreground',isCompact&&'sr-only')}>{g.caption}</p><div className="space-y-1">{g.items.filter(n=>canShowNavigation(n.to)).map(({to,name,icon:Icon})=><NavLink to={to} key={to} end={to==='/'} title={isCompact?name:undefined} className={({isActive})=>cn('adm-nav-item relative flex min-h-10 items-center gap-3 rounded-lg px-3 text-[13px] font-medium transition-colors',isCompact&&'justify-center px-0',isActive&&'is-active')}><Icon size={18} strokeWidth={1.8} className="shrink-0"/>{!isCompact&&<span className="truncate">{name}</span>}</NavLink>)}</div></nav>)}</div></div>
   <div className={cn('mt-auto border-t border-sidebar-border p-3',!isCompact&&'p-5')}>
    {!isCompact&&<div className="mb-4 rounded-xl border border-sidebar-border bg-sidebar-accent/40 p-3"><div className="flex items-center gap-2"><span className="flex size-7 items-center justify-center rounded-lg bg-background"><ShieldCheck size={15}/></span><span className="text-xs font-semibold">Режим оператора</span></div><p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">Критические закупки и платежи выполняются только через проверенные серверные операции.</p></div>}
    <Button variant="ghost" title="Выход" onClick={onLogout} className={cn('h-10 w-full justify-start text-muted-foreground',isCompact&&'justify-center px-0')}><LogOut size={17}/>{!isCompact&&<span>Завершить сеанс</span>}</Button>
   </div>
  </aside>
  <div className="flex min-w-0 flex-1 flex-col"><header className="sticky top-0 z-20 flex h-[72px] shrink-0 items-center justify-between gap-3 border-b border-border bg-background/95 px-4 backdrop-blur-xl md:px-8">
   <div className="flex min-w-0 items-center gap-3"><Button variant="ghost" size="icon" className="lg:hidden" onClick={()=>setMobileOpen(true)} aria-label="Открыть навигацию"><Menu size={20}/></Button><span className="hidden items-center gap-2 text-xs text-muted-foreground sm:inline-flex">Рабочее пространство <ChevronRight size={14}/></span><span className="truncate text-sm font-semibold tracking-tight">{current.name}</span></div>
   <div className="flex items-center gap-1.5 sm:gap-2.5"><QuickSearch navigate={navigate}/><Separator orientation="vertical" className="mx-1 hidden h-6 sm:block"/><ThemeButton/><Button variant="ghost" size="icon" title="Обновить данные" aria-label="Обновить данные" onClick={()=>client.invalidateQueries()}><RefreshCw size={17}/></Button></div>
  </header>
   <main className="mx-auto w-full max-w-[1580px] flex-1 px-4 pt-7 pb-12 md:px-8 md:pt-9 xl:px-10"><Routes><Route path="/" element={<ScopeGate scope="analytics"><Dashboard/></ScopeGate>}/><Route path="/orders" element={<ScopeGate scope="orders"><Orders/></ScopeGate>}/><Route path="/orders/:orderId" element={<ScopeGate scope="orders"><OrderView/></ScopeGate>}/><Route path="/catalog" element={<ScopeGate scope="catalog"><Catalog/></ScopeGate>}/><Route path="/catalog/manage" element={<ScopeGate scope="catalog"><Section section="catalog"/></ScopeGate>}/><Route path="/catalog/automation" element={<ScopeGate scope="catalog"><CatalogAutomation/></ScopeGate>}/><Route path="/catalog/templates" element={<ScopeGate scope="catalog"><CategoryTemplates/></ScopeGate>}/><Route path="/catalog/brands" element={<ScopeGate scope="catalog"><BrandDirectory/></ScopeGate>}/><Route path="/catalog/brands/:brandId" element={<ScopeGate scope="catalog"><BrandStudio/></ScopeGate>}/><Route path="/catalog/families" element={<ScopeGate scope="catalog"><FamilyStudio/></ScopeGate>}/><Route path="/catalog/:productId" element={<ScopeGate scope="catalog"><ProductView/></ScopeGate>}/><Route path="/suppliers" element={<ScopeGate scope="suppliers"><Suppliers/></ScopeGate>}/><Route path="/suppliers/manage" element={<ScopeGate scope="suppliers"><Section section="integrations"/></ScopeGate>}/><Route path="/suppliers/feeds" element={<ScopeGate scope="suppliers"><SupplierFeeds/></ScopeGate>}/><Route path="/suppliers/control" element={<ScopeGate scope="suppliers"><SupplierControl/></ScopeGate>}/><Route path="/suppliers/:code" element={<ScopeGate scope="suppliers"><SupplierView/></ScopeGate>}/><Route path="/warehouses" element={<ScopeGate scope="warehouses"><Section section="warehouses"/></ScopeGate>}/><Route path="/marketplace" element={<ScopeGate scope="marketplace"><Section section="marketplace"/></ScopeGate>}/><Route path="/finance" element={<ScopeGate scope="finance"><Section section="finance"/></ScopeGate>}/><Route path="/finance/studio" element={<ScopeGate scope="finance"><FinanceStudio/></ScopeGate>}/><Route path="/analytics" element={<ScopeGate scope="analytics"><Section section="analytics"/></ScopeGate>}/><Route path="/system" element={<ScopeGate scope="system"><Section section="system"/></ScopeGate>}/><Route path="/system/audit" element={<ScopeGate scope="system"><MigrationAudit/></ScopeGate>}/><Route path="/system/staff" element={<ScopeGate scope="staff"><Staff/></ScopeGate>}/><Route path="/system/sessions" element={<ScopeGate scope="sessions"><MySessions/></ScopeGate>}/><Route path="/system/security" element={<ScopeGate scope="sessions"><MySecurity/></ScopeGate>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes>
    <footer className="mt-14 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5 text-xs text-muted-foreground"><div className="flex items-center gap-2"><span className="size-1.5 rounded-full bg-foreground"/> Autocodex Studio · v0.12 (операционный аудит) <span className="text-border">/</span> данные из FastAPI</div><a className="inline-flex items-center gap-1.5 transition-colors hover:text-foreground" href="/operator" target="_blank" rel="noreferrer">Старая панель (резерв) <ExternalLink size={13}/></a></footer>
   </main>
  </div>
 </div>
}
function App(){
 const [auth,setAuth]=useState(false)
 const [restoring,setRestoring]=useState(true)
 useEffect(()=>{void restoreStaffSession().then(ok=>{setAuth(ok);setRestoring(false)})},[])
 useEffect(()=>{const expired=()=>{client.clear();setAuth(false)};window.addEventListener('autocodex-admin-auth-expired',expired);return()=>window.removeEventListener('autocodex-admin-auth-expired',expired)},[])
 async function logout(){try{await logoutAuth()}catch{clearOperatorAuth()}client.clear();setAuth(false)}
 return <QueryClientProvider client={client}><BrowserRouter basename="/admin">{restoring?<main className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">Проверка сеанса…</main>:auth?<Shell onLogout={()=>void logout()}/>:<Login onSuccess={()=>setAuth(true)}/>}</BrowserRouter></QueryClientProvider>
}
export default App
