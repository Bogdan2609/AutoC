import * as React from 'react'
import {cn} from '@/lib/utils'
export function Card({className,...props}:React.ComponentProps<'div'>){return <div data-slot="card" className={cn('flex flex-col rounded-xl border border-border bg-card text-card-foreground surface-shadow',className)} {...props}/>}
export function CardHeader({className,...props}:React.ComponentProps<'div'>){return <div data-slot="card-header" className={cn('flex flex-col gap-1 px-5 pt-5 pb-4 md:px-6 md:pt-6',className)} {...props}/>}
export function CardTitle({className,...props}:React.ComponentProps<'h3'>){return <h3 data-slot="card-title" className={cn('text-base font-semibold tracking-[-.025em]',className)} {...props}/>}
export function CardDescription({className,...props}:React.ComponentProps<'p'>){return <p data-slot="card-description" className={cn('text-sm leading-relaxed text-muted-foreground',className)} {...props}/>}
export function CardContent({className,...props}:React.ComponentProps<'div'>){return <div data-slot="card-content" className={cn('px-5 pb-5 md:px-6 md:pb-6',className)} {...props}/>}
export function CardFooter({className,...props}:React.ComponentProps<'div'>){return <div data-slot="card-footer" className={cn('flex items-center px-5 pb-5 md:px-6 md:pb-6',className)} {...props}/>}
