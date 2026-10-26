import * as React from 'react'
import {cn} from '@/lib/utils'
export function Separator({orientation='horizontal',decorative=true,className,...props}:React.ComponentProps<'div'>&{orientation?:'horizontal'|'vertical';decorative?:boolean}){
 return <div data-slot="separator" role={decorative?'none':'separator'} aria-orientation={decorative?undefined:orientation} className={cn('shrink-0 bg-border',orientation==='horizontal'?'h-px w-full':'h-full w-px',className)} {...props}/>
}
