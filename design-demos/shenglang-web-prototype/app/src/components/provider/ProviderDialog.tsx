import type { ComponentProps, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

export function ProviderDialogContent({ className, ...props }: ComponentProps<typeof DialogContent>) {
  return <DialogContent
    className={cn('prototype-dialog max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-[12px] p-5 sm:p-6', className)}
    {...props}
  />
}

interface ProviderDialogHeaderProps {
  icon: LucideIcon
  title: ReactNode
  description: ReactNode
  badge?: ReactNode
  tone?: 'default' | 'danger'
  className?: string
}

export function ProviderDialogHeader({ icon: Icon, title, description, badge, tone = 'default', className }: ProviderDialogHeaderProps) {
  return <DialogHeader data-layout="provider-dialog-heading" className={cn('flex-row items-start gap-3 pr-7 text-left', className)}>
    <span className={cn('grid size-10 shrink-0 place-items-center rounded-[8px]', tone === 'danger' ? 'bg-destructive/10 text-destructive' : 'bg-primary/10 text-primary')}>
      <Icon className="size-5" aria-hidden="true" />
    </span>
    <div className="min-w-0 flex-1 space-y-2 pt-0.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <DialogTitle className="text-pretty text-left text-lg font-semibold leading-6">{title}</DialogTitle>
        {badge}
      </div>
      <DialogDescription className="text-pretty text-left text-sm leading-6">{description}</DialogDescription>
    </div>
  </DialogHeader>
}

export function ProviderDialogFooter({ className, ...props }: ComponentProps<typeof DialogFooter>) {
  return <DialogFooter
    data-size="lg"
    className={cn('gap-2 [&_button]:h-10 [&_button]:rounded-[8px] sm:[&_button]:min-w-24', className)}
    {...props}
  />
}
