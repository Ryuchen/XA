import { useMemo, useState } from 'react'
import {
  AlertCircle,
  Check,
  ChevronRight,
  ClipboardList,
  Clock3,
  Gift,
  Inbox,
  ShoppingBag,
  Star,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { FilterBar } from '@/components/prototype/FilterBar'
import { HeaderSupportButton } from '@/components/prototype/HeaderSupportButton'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import { aftersaleOrder, bossOrders, paymentPendingOrder, type BossOrder, type OrderStatus } from '@/pages/boss/order-data'

export type OrdersScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'pending' | 'grabbed' | 'in-progress' | 'review' | 'refund' | 'gifts' | 'payment-pending' | 'aftersale'
type Filter = '' | 'pending' | 'grabbed' | 'in_progress' | 'completed' | 'cancelled'

interface OrdersPageProps {
  scenario: OrdersScenario
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onCatalog: () => void
  onGifts: () => void
  onOpen: (order: BossOrder) => void
  onRetry: () => void
}

const filters: { value: Filter; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待接单' },
  { value: 'grabbed', label: '已接单' },
  { value: 'in_progress', label: '服务中' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

const filterStatus: Record<Exclude<Filter, ''>, OrderStatus> = {
  pending: 'PENDING',
  grabbed: 'GRABBED',
  in_progress: 'IN_SERVICE',
  completed: 'COMPLETED',
  cancelled: 'CANCELLED',
}

export function OrdersPage({ scenario, onLogin, onHome, onCompanions, onCatalog, onGifts, onOpen, onRetry }: OrdersPageProps) {
  const initialFilter: Filter = scenario === 'pending' ? 'pending' : scenario === 'grabbed' ? 'grabbed' : scenario === 'in-progress' ? 'in_progress' : scenario === 'review' ? 'completed' : scenario === 'refund' ? 'cancelled' : ''
  const [filter, setFilter] = useState<Filter>(initialFilter)
  const [onlyGifts, setOnlyGifts] = useState(scenario === 'gifts')

  const sourceOrders = useMemo(() => {
    if (scenario === 'empty') return []
    const next = [...bossOrders]
    if (scenario === 'payment-pending') next.unshift(paymentPendingOrder)
    if (scenario === 'aftersale') next[2] = aftersaleOrder
    return next
  }, [scenario])

  const visibleOrders = sourceOrders.filter(order => {
    const statusMatches = !filter || order.status === filterStatus[filter]
    return statusMatches && (!onlyGifts || order.gift)
  })

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="orders" onNavigate={handleDockNavigate} />}>
      <main className="pb-36 md:pb-12">
        <PageHeader title="我的订单" description="查看接单、履约、评价与退款进度。" action={<HeaderSupportButton />} />

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6">
            <Card className="rounded-[12px] py-0 text-center"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><ClipboardList className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后查看订单</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看接单、服务、评价与退款进度。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button></CardContent></Card>
          </section>
        ) : (
          <>
            <section className="px-4 pb-6 md:px-6" aria-label="订单筛选">
              <FilterBar ariaLabel="订单状态筛选" items={filters} value={filter} onChange={setFilter} columns={6} />
              <div className="mt-3 flex min-h-11 items-center justify-between gap-3">
                <button type="button" aria-pressed={onlyGifts} onClick={() => setOnlyGifts(!onlyGifts)} className={cn('flex min-h-11 items-center gap-2 rounded-[8px] border px-3 text-sm text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring', onlyGifts && 'border-primary/30 bg-primary/10 text-primary')}>
                  <Gift className="size-4" />只看礼物{onlyGifts && <Check className="size-4" />}
                </button>
                {!['loading', 'error'].includes(scenario) && <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{visibleOrders.length} 笔示例订单</span>}
              </div>
            </section>

            {scenario === 'loading' ? (
              <div className="grid gap-4 px-4 md:grid-cols-2 md:px-6" aria-busy="true" aria-label="正在加载订单列表">{[236, 236, 220, 220].map((height, index) => <Skeleton key={index} className="w-full rounded-[12px]" style={{ height }} />)}</div>
            ) : scenario === 'error' ? (
              <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">订单暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，已有订单不会受到影响。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px] md:max-w-64" onClick={onRetry}>重新加载</Button></div></section>
            ) : visibleOrders.length ? (
              <section className="px-4 md:px-6" aria-label="订单列表">
                <div className="grid items-stretch gap-4 md:grid-cols-2">
                  {visibleOrders.map(order => <OrderCard key={order.id} order={order} onOpen={() => onOpen(order)} />)}
                </div>
                <p className="py-6 text-center text-xs text-muted-foreground">已展示当前筛选下的全部示例订单</p>
              </section>
            ) : (
              <section className="px-4 pb-8 pt-8 text-center md:px-6">
                <div className="rounded-[12px] border bg-card px-4 py-8 md:py-12"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/60 text-muted-foreground"><Inbox className="size-6" /></div>
                  <h2 className="mt-4 text-lg font-semibold">{onlyGifts ? '还没有这类礼物订单' : '还没有这类订单'}</h2>
                  <p className="mx-auto mt-2 max-w-[320px] text-sm leading-6 text-muted-foreground">{filter || onlyGifts ? '可以切换状态查看其他订单，或重新选择服务。' : '下单后，接单与服务进度会在这里更新。'}</p>
                  <div className="mx-auto mt-6 grid max-w-[280px] gap-3">
                    <Button type="button" className="h-11 rounded-[8px]" onClick={onlyGifts ? onGifts : onCatalog}><ShoppingBag className="size-4" />{onlyGifts ? '去挑选礼物' : '去选择服务'}</Button>
                    {(filter || onlyGifts) && <Button type="button" variant="ghost" className="h-11 rounded-[8px]" onClick={() => { setFilter(''); setOnlyGifts(false) }}>查看全部订单</Button>}
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </main>

    </PhoneFrame>
  )
}

function OrderCard({ order, onOpen }: { order: BossOrder; onOpen: () => void }) {
  const ActionIcon = order.evaluate ? Star : order.aftersale ? WalletCards : order.gift ? Gift : order.status === 'PENDING' ? Clock3 : ChevronRight
  return (
    <button type="button" onClick={onOpen} className="group flex min-h-[232px] w-full flex-col rounded-[12px] border bg-card p-4 text-left shadow-sm transition-[border-color,box-shadow] hover:border-primary/30 hover:shadow-md focus-visible:ring-2 focus-visible:ring-ring md:p-5" aria-label={`订单 ${order.orderNo}，${order.statusText}，${order.service}，${order.amountLabel || '实付'} ${formatWaveAmount(order.amount)} 浪花`}>
      <div className="flex items-center justify-between gap-3">
        <span className="truncate font-mono text-xs tracking-wide text-muted-foreground">{order.orderNo}</span>
        <Badge variant="outline" className={cn('shrink-0 rounded-md', statusClass(order))}><span className="size-1.5 rounded-full bg-current" />{order.statusText}</Badge>
      </div>
      <div className="mt-4 flex items-start justify-between gap-4">
        <div className="min-w-0"><p className="text-xs text-muted-foreground">{order.game}</p><h2 className="mt-1 text-base font-semibold leading-6">{order.service}</h2></div>
        <div className="shrink-0 text-right"><p className="text-xs text-muted-foreground">{order.amountLabel || '实付'}</p><p className="mt-1 text-xl font-semibold tabular-nums">{formatWaveAmount(order.amount)}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花</span></p></div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 rounded-[8px] bg-muted/40 p-3 text-xs">
        <div className="min-w-0"><p className="text-muted-foreground">{order.gift ? '赠礼对象' : '陪玩安排'}</p><p className="mt-1 truncate font-medium">{order.provider}</p></div>
        <div><p className="text-muted-foreground">数量 / 下单时间</p><p className="mt-1 truncate font-medium tabular-nums">{order.quantity} {order.unit} · {order.created.slice(5)}</p></div>
      </div>
      {order.deadline && <p className="mt-3 rounded-[8px] bg-warning/5 p-3 text-xs leading-5 text-warning">{order.deadline}</p>}
      {order.refund && <p className="mt-3 text-xs leading-5 text-success">{order.refund}</p>}
      <div className="mt-4 flex min-h-11 items-center justify-between gap-3 border-t pt-3">
        <span className={cn('flex min-w-0 items-center gap-2 text-xs leading-5 text-muted-foreground', (order.evaluate || order.aftersale) && 'text-primary')}><ActionIcon className="size-4 shrink-0" /><span className="line-clamp-2">{order.progress}</span></span>
        <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-primary">查看详情<ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5" /></span>
      </div>
    </button>
  )
}

function statusClass(order: BossOrder) {
  if (order.paymentUnconfirmed) return 'border-warning/25 bg-warning/10 text-warning hover:bg-warning/10'
  if (order.status === 'PENDING') return 'border-warning/25 bg-warning/10 text-warning hover:bg-warning/10'
  if (order.status === 'COMPLETED') return 'border-success/25 bg-success/10 text-success hover:bg-success/10'
  if (order.status === 'CANCELLED') return 'border-border bg-muted/50 text-muted-foreground hover:bg-muted/50'
  return 'border-primary/25 bg-primary/10 text-primary hover:bg-primary/10'
}
