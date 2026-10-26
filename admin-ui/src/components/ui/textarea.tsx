import * as React from 'react'
import {cn} from '@/lib/utils'
export const Textarea=React.forwardRef<HTMLTextAreaElement,React.ComponentProps<'textarea'>>(function Textarea({className,...props},ref){return <textarea data-slot="textarea" ref={ref} className={cn('min-h-28 w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/35 disabled:cursor-not-allowed disabled:opacity-50',className)} {...props}/>})
