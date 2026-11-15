import { cn } from '@/lib/utils'

interface FilterItem<T extends string> {
  value: T
  label: string
  count?: number
}

interface FilterBarProps<T extends string> {
  items: FilterItem<T>[]
  value: T
  onChange: (value: T) => void
  ariaLabel: string
  className?: string
  columns?: 2 | 4 | 5 | 6
}

const columnClasses = {
  2: 'md:grid-cols-2',
  4: 'md:grid-cols-4',
  5: 'md:grid-cols-5',
  6: 'md:grid-cols-6',
} as const

export function FilterBar<T extends string>({ items, value, onChange, ariaLabel, className, columns }: FilterBarProps<T>) {
  return (
    <nav aria-label={ariaLabel} className={cn('phone-scrollbar overflow-x-auto rounded-[8px] bg-muted/60 p-1', className)}>
      <div className={cn('flex min-w-max', columns && 'md:grid md:min-w-0', columns && columnClasses[columns])}>
        {items.map(item => (
          <button
            key={item.value || 'all'}
            type="button"
            aria-pressed={value === item.value}
            onClick={() => onChange(item.value)}
            className={cn(
              'flex min-h-11 shrink-0 items-center justify-center gap-1.5 rounded-[6px] px-4 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:px-2',
              value === item.value && 'bg-background text-foreground shadow-sm',
            )}
          >
            <span>{item.label}</span>
            {item.count !== undefined && item.count > 0 && <span className="min-w-5 rounded-md bg-primary/10 px-1.5 py-0.5 text-xs leading-4 text-primary">{item.count}</span>}
          </button>
        ))}
      </div>
    </nav>
  )
}
