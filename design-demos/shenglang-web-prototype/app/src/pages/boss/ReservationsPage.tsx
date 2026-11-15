import { useState } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, CalendarCheck2, CalendarClock, CheckCircle2, ChevronRight, Clock3, LoaderCircle, LogIn, RefreshCw, XCircle } from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import { bossReservations, type Reservation } from '@/pages/boss/reservation-data'

export type ReservationsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'more-loading' | 'more-error' | 'all-statuses'

interface ReservationsPageProps {
  scenario: ReservationsScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onBrowse: () => void
  onOpen: (reservation: Reservation) => void
  onRetry: () => void
}

const statusStyles: Record<Reservation['status'], string> = {
  PENDING: 'border-warning/25 bg-warning/10 text-warning',
  CONFIRMED: 'border-primary/25 bg-primary/10 text-primary',
  CONVERTED: 'border-success/25 bg-success/10 text-success',
  REJECTED: 'border-border bg-muted/45 text-muted-foreground',
  CANCELLED: 'border-border bg-muted/45 text-muted-foreground',
  EXPIRED: 'border-border bg-muted/45 text-muted-foreground',
}

export function ReservationsPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onBrowse, onOpen, onRetry }: ReservationsPageProps) {
  const [expanded, setExpanded] = useState(scenario === 'all-statuses')
  const [moreBusy, setMoreBusy] = useState(scenario === 'more-loading')
  const [moreError, setMoreError] = useState('')
  const empty = scenario === 'empty'
  const items = empty ? [] : expanded ? bossReservations : bossReservations.slice(0, 4)
  const hasMore = !expanded && !empty && ['default', 'more-loading', 'more-error'].includes(scenario)
  const nextReservation = bossReservations.find(item => item.status === 'CONFIRMED' || item.status === 'PENDING')
  const activeCount = bossReservations.filter(item => item.status === 'CONFIRMED' || item.status === 'PENDING').length
  const showSummary = !['guest', 'loading', 'error', 'empty'].includes(scenario) && nextReservation

  function loadMore() {
    if (moreBusy || !hasMore) return
    if (scenario === 'more-error' && !moreError) {
      setMoreError('更多预约暂时没有加载成功，已显示的记录不受影响。')
      return
    }
    setMoreError('')
    setMoreBusy(true)
    window.setTimeout(() => { setExpanded(true); setMoreBusy(false) }, 450)
  }
  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onBack()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-32 md:pb-12">
        <div className="flex items-center justify-between px-4 py-2 md:px-6">
          <Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-sm" onClick={onBack}><ArrowLeft className="size-4" />个人中心</Button>
          <Badge variant="outline" className="rounded-md text-xs">示例预约</Badge>
        </div>

        {scenario === 'guest' ? <GuestState onLogin={onLogin} /> : scenario === 'loading' ? (
          <div className="space-y-6 px-4 pt-2 md:px-6" aria-busy="true" aria-label="正在加载预约列表"><Skeleton className="h-36 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /></div>
        ) : scenario === 'error' ? <ErrorState onRetry={onRetry} onBack={onBack} /> : empty ? <EmptyState onBrowse={onBrowse} /> : (
          <>
            <header className="px-4 pb-6 pt-2 md:px-6">
              <h1 className="text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">我的预约</h1>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">查看服务时段与确认进度，核对报价后再决定是否成单。</p>
            </header>

            <div className="px-4 md:px-6">
              {showSummary && <section className="rounded-[12px] border border-primary/15 bg-primary/[0.055] p-5" aria-labelledby="next-reservation-title">
                <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    <div className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary"><CalendarClock className="size-5" /></div>
                    <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 id="next-reservation-title" className="text-sm font-semibold">下一次预约</h2><Badge variant="outline" className={cn('rounded-md text-xs', statusStyles[nextReservation.status])}>{nextReservation.statusText}</Badge></div><p className="mt-2 text-lg font-semibold tabular-nums">{nextReservation.startText}</p><p className="mt-1 truncate text-xs text-muted-foreground">{nextReservation.service} · {nextReservation.provider}</p></div>
                  </div>
                  <div className="flex items-center justify-between gap-4 border-t pt-4 sm:shrink-0 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0"><div><p className="text-2xl font-semibold text-primary tabular-nums">{activeCount}</p><p className="mt-1 text-xs text-muted-foreground">进行中预约</p></div><Button type="button" variant="outline" className="h-11 rounded-[8px] bg-background" onClick={() => onOpen(nextReservation)}>查看安排<ArrowRight className="size-4" /></Button></div>
                </div>
              </section>}

              <div className="mb-3 mt-6 flex items-end justify-between gap-3"><div><h2 className="text-base font-semibold">预约记录</h2><p className="mt-1 text-xs text-muted-foreground">共 {bossReservations.length} 条示例记录</p></div><span className="text-xs text-muted-foreground">预约不扣款</span></div>
              <section className="space-y-3" aria-label="预约记录">{items.map(item => <ReservationCard key={item.id} reservation={item} onClick={() => onOpen(item)} />)}</section>
              {moreError && <div role="alert" className="mt-3 rounded-[8px] border border-destructive/25 bg-destructive/5 p-3 text-xs leading-5 text-destructive">{moreError}</div>}
              {hasMore && <Button type="button" variant="outline" className="mt-4 h-12 w-full rounded-[8px]" onClick={loadMore} disabled={moreBusy}>{moreBusy ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}{moreBusy ? '正在加载' : moreError ? '重试加载更多预约' : '加载更多预约'}</Button>}
              {!hasMore && <p className="py-5 text-center text-xs text-muted-foreground">已显示全部预约</p>}
            </div>
          </>
        )}
      </main>
    </PhoneFrame>
  )
}

function ReservationCard({ reservation, onClick }: { reservation: Reservation; onClick: () => void }) {
  const { status } = reservation
  const hint = status === 'CONFIRMED' ? '核对实时报价后可成单' : status === 'PENDING' ? '等待陪玩确认，暂不会扣款' : status === 'CONVERTED' ? '已生成订单，可查看服务进度' : status === 'EXPIRED' ? '可沿用原服务重新预约' : '查看处理说明'
  return (
    <button type="button" className="group block w-full rounded-[12px] border bg-card p-4 text-left shadow-sm transition-colors hover:border-primary/35 focus-visible:ring-2 focus-visible:ring-ring md:p-5" onClick={onClick} aria-label={`${reservation.service}，${reservation.provider}，${reservation.startText}，${reservation.statusText}`}>
      <span className="grid gap-4 md:grid-cols-[minmax(0,1.35fr)_minmax(180px,.85fr)_120px_20px] md:items-center md:gap-5">
        <span className="min-w-0">
          <span className="flex items-center justify-between gap-3"><span className="truncate font-mono text-xs text-muted-foreground">{reservation.reservationNo}</span><Badge variant="outline" className={cn('shrink-0 rounded-md text-xs md:hidden', statusStyles[status])}>{reservation.statusText}</Badge></span>
          <span className="mt-2 flex flex-wrap items-center gap-2"><span className="text-base font-semibold">{reservation.service}</span><Badge variant="outline" className={cn('hidden rounded-md text-xs md:inline-flex', statusStyles[status])}>{status === 'CONVERTED' ? <CheckCircle2 className="size-3" /> : ['REJECTED', 'CANCELLED', 'EXPIRED'].includes(status) ? <XCircle className="size-3" /> : <Clock3 className="size-3" />}{reservation.statusText}</Badge></span>
          <span className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><Avatar className="size-6 border"><AvatarFallback className="bg-primary/10 text-xs text-primary">{reservation.providerInitial}</AvatarFallback></Avatar><span>{reservation.provider}</span><span aria-hidden="true">·</span><span className="tabular-nums">{reservation.rounds} 份服务</span></span>
        </span>
        <span className="rounded-[8px] bg-muted/45 p-3 md:bg-transparent md:p-0"><span className="block text-xs text-muted-foreground">预约时间 · 北京时间</span><span className="mt-1 block text-sm font-semibold tabular-nums">{reservation.startText}</span><span className="mt-1 block text-xs text-muted-foreground md:hidden">{hint}</span></span>
        <span className="flex items-end justify-between gap-3 md:block md:text-right"><span><span className="block text-xs text-muted-foreground">预约时预估</span><span className="mt-1 block text-lg font-semibold tabular-nums">{formatWaveAmount(reservation.estimatedAmount)} <span className="text-xs font-normal text-muted-foreground">浪花</span></span></span><span className="flex items-center text-xs font-medium text-primary md:hidden">查看详情<ChevronRight className="size-4" /></span></span>
        <ChevronRight className="hidden size-5 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary md:block" />
      </span>
    </button>
  )
}

function GuestState({ onLogin }: { onLogin: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div className="mx-auto max-w-xl rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><CalendarCheck2 className="size-6" /></div><h1 className="mt-4 text-xl font-semibold">预约只属于当前账号</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看确认进度、实时报价与成单状态。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></div></section>
}
function ErrorState({ onRetry, onBack }: { onRetry: () => void; onBack: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">预约列表暂时没有加载成功</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会取消预约或生成订单。</p><Button type="button" className="mt-6 h-11 rounded-[8px]" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button><Button type="button" variant="ghost" className="ml-2 h-11 rounded-[8px]" onClick={onBack}>返回我的</Button></div></section>
}
function EmptyState({ onBrowse }: { onBrowse: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div className="rounded-[12px] border bg-card p-8 text-center shadow-sm"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/50 text-muted-foreground"><CalendarClock className="size-6" /></div><h1 className="mt-4 text-xl font-semibold">还没有预约</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">选好服务和陪玩后，就可以先锁定合适的时间。</p><Button type="button" className="mt-6 h-12 rounded-[8px]" onClick={onBrowse}>去选择服务<ArrowRight className="size-4" /></Button></div></section>
}
