import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: string
  description: string
  action?: ReactNode
  children?: ReactNode
  className?: string
  compact?: boolean
}

export function PageHeader({ title, description, action, children, className, compact = false }: PageHeaderProps) {
  return (
    <header className={cn('px-4 pb-6 pt-2 md:px-6 md:pt-4', compact && 'pb-4', className)}>
      <div className={cn('flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between', compact && 'flex-row items-center gap-3')}>
        <div className="min-w-0">
          <h1 className={cn('text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]', compact && 'text-[22px]')}>{title}</h1>
          <p className={cn('mt-2 text-sm leading-6 text-muted-foreground', compact && 'mt-1 text-xs leading-5')}>{description}</p>
        </div>
        {action && <div className={cn('flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0 sm:justify-end', compact && 'w-auto shrink-0 flex-nowrap')}>{action}</div>}
      </div>
      {children}
    </header>
  )
}
