import type {ReactNode} from 'react'
import type {ColumnDef} from '@tanstack/react-table'
import {flexRender,getCoreRowModel,getSortedRowModel,useReactTable,type SortingState} from '@tanstack/react-table'
import {useState} from 'react'
import {AlertTriangle,ArrowDown,ArrowDownUp,ArrowUp,ChevronLeft,ChevronRight,Inbox,RefreshCw} from 'lucide-react'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Card} from '@/components/ui/card'
import {Skeleton} from '@/components/ui/skeleton'
import {Table,TableHeader,TableBody,TableRow,TableHead,TableCell} from '@/components/ui/table'
export function Heading({title,description,actions}:{title:string;description:string;actions?:ReactNode}) {
 return <div className="mb-7 flex flex-wrap items-end justify-between gap-4"><div className="max-w-3xl"><div className="mb-3 flex items-center gap-2 text-[10px] font-bold uppercase tracking-[.17em] text-muted-foreground"><span className="inline-block h-px w-6 bg-foreground"/> AUTOCODEX / WORKSPACE</div><h1 className="text-[30px] font-semibold tracking-[-.055em] md:text-[38px]">{title}<span className="text-muted-foreground">.</span></h1><p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">{description}</p></div>{actions&&<div className="flex flex-wrap gap-2">{actions}</div>}</div>
}
export function ErrorBox({error,onRetry}:{error:unknown;onRetry?:()=>void}) {
 const message=error instanceof Error?error.message:'Не удалось получить данные'
 return <Card className="p-6"><div className="flex items-start gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-destructive/10 text-destructive"><AlertTriangle size={19}/></span><div className="min-w-0 space-y-2"><p className="font-semibold">Ошибка загрузки</p><p className="break-words text-sm text-muted-foreground">{message}</p>{onRetry&&<Button variant="outline" onClick={onRetry}><RefreshCw size={15}/> Повторить запрос</Button>}</div></div></Card>
}
export function Loading(){return <Card className="space-y-4 p-6" role="status" aria-label="Загрузка данных"><Skeleton className="h-5 w-40"/><Skeleton className="h-9 w-full"/><Skeleton className="h-9 w-4/5"/><span className="sr-only">Получаем актуальные данные…</span></Card>}
export function Empty({text='Записей пока нет'}:{text?:string}){return <div className="flex flex-col items-center gap-3 px-5 py-14 text-center"><span className="flex size-12 items-center justify-center rounded-xl border border-border bg-muted/40"><Inbox size={23} className="text-muted-foreground"/></span><p className="text-sm font-medium">{text}</p><p className="text-xs text-muted-foreground">Измените фильтры или повторите поиск.</p></div>}
export function DataGrid<T>({data,columns,empty='Нет записей'}:{data:T[];columns:ColumnDef<T>[];empty?:string}){
 const [sorting,setSorting]=useState<SortingState>([])
 const table=useReactTable({data,columns,state:{sorting},onSortingChange:setSorting,getCoreRowModel:getCoreRowModel(),getSortedRowModel:getSortedRowModel()})
 if(!data.length)return <Card><Empty text={empty}/></Card>
 return <Card className="overflow-hidden"><Table><TableHeader>{table.getHeaderGroups().map(group=><TableRow key={group.id} className="bg-muted/35 hover:bg-muted/35">{group.headers.map(h=><TableHead key={h.id}><button type="button" disabled={!h.column.getCanSort()} className="inline-flex items-center gap-1.5 whitespace-nowrap text-left disabled:cursor-default" onClick={h.column.getToggleSortingHandler()}>{flexRender(h.column.columnDef.header,h.getContext())}{h.column.getCanSort()&&(h.column.getIsSorted()==='asc'?<ArrowUp size={13}/>:h.column.getIsSorted()==='desc'?<ArrowDown size={13}/>:<ArrowDownUp size={13} className="opacity-50"/>)}</button></TableHead>)}</TableRow>)}</TableHeader><TableBody>{table.getRowModel().rows.map(row=><TableRow key={row.id}>{row.getVisibleCells().map(cell=><TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell,cell.getContext())}</TableCell>)}</TableRow>)}</TableBody></Table></Card>
}
export function Pagination({page,size,total,onChange}:{page:number;size:number;total:number;onChange:(page:number)=>void}){
 const last=Math.max(1,Math.ceil(total/size));return <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"><span className="tabular-nums">Всего: {total.toLocaleString('ru-RU')} · Страница {page} / {last}</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={page<=1} onClick={()=>onChange(page-1)}><ChevronLeft size={15}/> Назад</Button><Button variant="outline" size="sm" disabled={page>=last} onClick={()=>onChange(page+1)}>Далее <ChevronRight size={15}/></Button></div></div>
}
export function Status({value}:{value:string|null|undefined}){
 const v=(value||'unknown').toLowerCase();const danger=/(failed|error|rejected|unknown|exception)/.test(v);const warn=/(draft|review|pending|queued|disabled|unverified)/.test(v);const success=/(active|ready|published|completed|success|paid|verified)/.test(v)
 return <Badge variant={danger?'danger':warn?'warning':success?'success':'outline'}><span className="size-1.5 shrink-0 rounded-full bg-current"/>{value||'—'}</Badge>
}
