import * as TabsPrimitive from '@radix-ui/react-tabs'
import {cn} from '@/lib/utils'
export const Tabs=TabsPrimitive.Root
export function TabsList({className,...props}:React.ComponentProps<typeof TabsPrimitive.List>){return <TabsPrimitive.List data-slot="tabs-list" className={cn('inline-flex min-h-9 flex-wrap items-center gap-0.5 rounded-md bg-muted p-1 text-muted-foreground',className)} {...props}/>}
export function TabsTrigger({className,...props}:React.ComponentProps<typeof TabsPrimitive.Trigger>){return <TabsPrimitive.Trigger data-slot="tabs-trigger" className={cn('inline-flex cursor-pointer items-center justify-center rounded-sm px-3 py-1.5 text-sm font-medium whitespace-nowrap transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-xs',className)} {...props}/>}
export function TabsContent({className,...props}:React.ComponentProps<typeof TabsPrimitive.Content>){return <TabsPrimitive.Content data-slot="tabs-content" className={cn('mt-5 min-w-0 outline-none',className)} {...props}/>}
