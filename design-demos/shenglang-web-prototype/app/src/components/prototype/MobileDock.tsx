import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface MobileDockItem {
  key: string
  label: string
  icon: LucideIcon
  unread?: number
}

interface MobileDockProps {
  items: MobileDockItem[]
  active: string
  ariaLabel: string
  onNavigate: (key: string, label: string) => void
}

export function MobileDock({ items, active, ariaLabel, onNavigate }: MobileDockProps) {
  return (
    <nav className="absolute inset-x-3 bottom-3 z-30 rounded-[16px] border border-white/20 bg-background/55 px-2 py-2 shadow-[0_12px_36px_rgba(2,6,23,0.22),inset_0_1px_0_rgba(255,255,255,0.16)] backdrop-blur-2xl backdrop-saturate-150 md:hidden" aria-label={ariaLabel}>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}>
        {items.map(item => {
          const Icon = item.icon
          const selected = item.key === active
          return (
            <button
              key={item.key}
              type="button"
              className={cn(
                'group relative flex size-12 flex-col items-center justify-center justify-self-center gap-1 rounded-[12px] text-[10px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
                selected && 'text-primary',
              )}
              onClick={() => onNavigate(item.key, item.label)}
              aria-current={selected ? 'page' : undefined}
              aria-label={`${item.label}${selected ? '，当前栏目' : ''}${item.unread ? `，${item.unread} 条未读示例消息` : ''}`}
            >
              <span className={cn(
                'relative grid size-7 place-items-center rounded-[9px] transition-[transform,background-color,box-shadow,color] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:-translate-y-0.5 group-hover:scale-105 motion-reduce:transform-none motion-reduce:transition-none',
                selected && '-translate-y-1 scale-110 bg-gradient-to-b from-sky-400 to-blue-600 text-white shadow-[0_8px_18px_rgba(37,99,235,0.32),inset_0_1px_0_rgba(255,255,255,0.45)]',
              )}>
                <Icon className="size-5" strokeWidth={selected ? 2.3 : 1.9} />
                {item.unread && (
                  <span className="absolute -right-2.5 -top-1.5 grid min-w-4 place-items-center rounded-full bg-destructive px-1 text-[9px] font-semibold leading-4 text-destructive-foreground">{item.unread}</span>
                )}
              </span>
              <span className={cn('leading-none transition-colors', selected && 'font-semibold text-primary')}>{item.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
