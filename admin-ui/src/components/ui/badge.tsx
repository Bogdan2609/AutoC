import * as React from 'react'
import {cn} from '@/lib/utils'
export function Badge({className,variant='secondary',...props}:React.ComponentProps<'span'>&{variant?:'secondary'|'success'|'warning'|'danger'|'outline'}){
 const kinds={secondary:'border-transparent bg-secondary text-secondary-foreground',success:'border-emerald-600/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',warning:'border-amber-600/20 bg-amber-500/10 text-amber-800 dark:text-amber-300',danger:'border-red-600/20 bg-red-500/10 text-red-700 dark:text-red-300',outline:'border-border bg-transparent text-foreground'}
 return <span data-slot="badge" className={cn('inline-flex max-w-full items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-semibold leading-none tracking-[.01em]',kinds[variant],className)} {...props}/>
}
