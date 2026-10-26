import * as React from 'react'
import {cn} from '@/lib/utils'
export function Table({className,...props}:React.ComponentProps<'table'>){return <div data-slot="table-container" className="table-scroller"><table data-slot="table" className={cn('w-full caption-bottom text-left text-sm',className)} {...props}/></div>}
export function TableHeader({className,...props}:React.ComponentProps<'thead'>){return <thead data-slot="table-header" className={cn('[&_tr]:border-b',className)} {...props}/>}
export function TableBody({className,...props}:React.ComponentProps<'tbody'>){return <tbody data-slot="table-body" className={cn('[&_tr:last-child]:border-0',className)} {...props}/>}
export function TableRow({className,...props}:React.ComponentProps<'tr'>){return <tr data-slot="table-row" className={cn('border-b border-border transition-colors hover:bg-muted/35',className)} {...props}/>}
export function TableHead({className,...props}:React.ComponentProps<'th'>){return <th data-slot="table-head" className={cn('h-11 whitespace-nowrap px-5 text-[11px] font-semibold tracking-[.025em] text-muted-foreground',className)} {...props}/>}
export function TableCell({className,...props}:React.ComponentProps<'td'>){return <td data-slot="table-cell" className={cn('px-5 py-4 align-middle text-[13px]',className)} {...props}/>}
