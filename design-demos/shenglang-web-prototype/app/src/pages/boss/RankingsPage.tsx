import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ChevronRight,
  Crown,
  Headphones,
  LockKeyhole,
  LogIn,
  Medal,
  RefreshCw,
  ShieldCheck,
  Trophy,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type RankingsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'providers-empty' | 'day' | 'week' | 'unavailable-provider'
type Period = 'day' | 'week' | 'month'

interface RankingsPageProps {
  scenario: RankingsScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMine: () => void
  onProvider: () => void
  onRetry: () => void
}

interface RankItem {
  id: number
  rank: number
  nickname: string
  count: number
  amount: number
  title: string
}

const periods: { value: Period; label: string }[] = [
  { value: 'day', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
]

const providersByPeriod: Record<Period, RankItem[]> = {
  day: [
    { id: 101, rank: 1, nickname: '小满', count: 5, amount: 168, title: '男' },
    { id: 102, rank: 2, nickname: '阿澈', count: 4, amount: 152, title: '男' },
    { id: 103, rank: 3, nickname: '可乐', count: 3, amount: 108, title: '男' },
  ],
  week: [
    { id: 101, rank: 1, nickname: '小满', count: 12, amount: 428, title: '子' },
    { id: 102, rank: 2, nickname: '阿澈', count: 9, amount: 326, title: '男' },
    { id: 103, rank: 3, nickname: '可乐', count: 8, amount: 288, title: '男' },
    { id: 104, rank: 4, nickname: '晚星', count: 6, amount: 216, title: '男' },
    { id: 105, rank: 5, nickname: '北北', count: 4, amount: 128, title: '男' },
  ],
  month: [
    { id: 101, rank: 1, nickname: '小满', count: 32, amount: 1168, title: '伯' },
    { id: 102, rank: 2, nickname: '阿澈', count: 26, amount: 986, title: '子' },
    { id: 103, rank: 3, nickname: '可乐', count: 18, amount: 668, title: '子' },
    { id: 104, rank: 4, nickname: '晚星', count: 12, amount: 428, title: '子' },
    { id: 105, rank: 5, nickname: '北北', count: 8, amount: 268, title: '男' },
  ],
}

const consumersByPeriod: Record<Period, RankItem[]> = {
  day: [
    { id: 201, rank: 1, nickname: '海浪收藏家', count: 1, amount: 328, title: '男' },
    { id: 202, rank: 2, nickname: '云上听风', count: 1, amount: 260, title: '男' },
    { id: 203, rank: 3, nickname: '林间月', count: 1, amount: 188, title: '男' },
  ],
  week: [
    { id: 201, rank: 1, nickname: '海浪收藏家', count: 8, amount: 1280, title: '男' },
    { id: 202, rank: 2, nickname: '云上听风', count: 6, amount: 960, title: '男' },
    { id: 203, rank: 3, nickname: '林间月', count: 5, amount: 728, title: '男' },
    { id: 204, rank: 4, nickname: '星河入梦', count: 3, amount: 468, title: '男' },
    { id: 205, rank: 5, nickname: '南风', count: 2, amount: 268, title: '男' },
  ],
  month: [
    { id: 201, rank: 1, nickname: '海浪收藏家', count: 12, amount: 3680, title: '子' },
    { id: 202, rank: 2, nickname: '云上听风', count: 9, amount: 2860, title: '男' },
    { id: 203, rank: 3, nickname: '林间月', count: 7, amount: 1980, title: '男' },
    { id: 204, rank: 4, nickname: '星河入梦', count: 5, amount: 1260, title: '男' },
    { id: 205, rank: 5, nickname: '南风', count: 3, amount: 880, title: '男' },
  ],
}

export function RankingsPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onMine, onProvider, onRetry }: RankingsPageProps) {
  const initialPeriod: Period = scenario === 'day' ? 'day' : scenario === 'week' ? 'week' : 'month'
  const [period, setPeriod] = useState<Period>(initialPeriod)
  const [switching, setSwitching] = useState(false)
  const periodLabel = periods.find(item => item.value === period)?.label || '本月'
  const providers = scenario === 'empty' || scenario === 'providers-empty' ? [] : providersByPeriod[period].map(item => scenario === 'unavailable-provider' && item.rank === 1 ? { ...item, id: 0, nickname: '神秘用户' } : item)
  const consumers = scenario === 'empty' ? [] : consumersByPeriod[period]

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onMine()
  }

  function changePeriod(next: Period) {
    if (switching || next === period) return
    setSwitching(true)
    window.setTimeout(() => {
      setPeriod(next)
      setSwitching(false)
    }, 350)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="home" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />首页</Button>
          <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">声浪榜单 · 示例</Badge>
        </div>
        <header className="px-4 pb-4 pt-2">
          <p className="text-[11px] font-medium text-primary">声浪俱乐部 · 荣耀时刻</p>
          <h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">一起见证，高光时刻</h1>
          <p className="mt-1 text-xs text-muted-foreground">{periodLabel}已完成订单 · 两类榜单独立排名</p>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Trophy className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录查看声浪榜单</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">看看本周默契搭档与俱乐部支持时刻，发现下一位合拍队友。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
        ) : (
          <div className="space-y-4 px-4 pb-5">
            <div className="grid grid-cols-3 gap-1 rounded-xl border bg-muted/30 p-1" aria-label="榜单周期">{periods.map(item => <button key={item.value} type="button" className={cn('min-h-11 rounded-lg text-xs font-medium text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring', period === item.value && 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90')} aria-pressed={period === item.value} disabled={switching || scenario === 'loading'} onClick={() => changePeriod(item.value)}>{switching && period !== item.value ? item.label : item.label}</button>)}</div>

            {scenario === 'loading' || switching ? (
              <div className="space-y-4" aria-busy="true" aria-label="正在加载榜单"><RankingSkeleton /><RankingSkeleton /></div>
            ) : scenario === 'error' ? (
              <section role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">榜单暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会改变订单或排名数据。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></section>
            ) : (
              <>
                <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.36),rgba(2,6,23,0.72)_75%)] p-5" aria-label={`${periodLabel}榜单概览`}>
                  <div className="absolute -right-7 -top-8 size-32 rounded-full bg-sky-400/10 blur-2xl" />
                  <div className="relative flex items-center gap-4"><div className="grid size-14 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200"><Crown className="size-7" /></div><div><p className="text-[11px] text-sky-200">{periodLabel}声浪风云榜</p><h2 className="mt-1 text-lg font-semibold">每份支持，都有回响</h2><p className="mt-1 text-[10px] text-muted-foreground">两类榜单分别展示前 5 名</p></div></div>
                </section>

                <RankingGroup title="陪玩服务榜" caption="按已完成订单数从高到低排列" metric="完成单数" icon={Headphones} items={providers} kind="providers" onProvider={onProvider} />
                <RankingGroup title="老板支持榜" caption="已完成订单净消费额，已扣除退款" metric="净消费额" icon={ShieldCheck} items={consumers} kind="consumers" />

                <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-primary" /><p>仅统计当前周期内已完成且符合支付状态的订单。周期从今日、本周一或本月一日开始计算。</p></div>
              </>
            )}
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}

function RankingGroup({ title, caption, metric, icon: Icon, items, kind, onProvider }: { title: string; caption: string; metric: string; icon: typeof Trophy; items: RankItem[]; kind: 'providers' | 'consumers'; onProvider?: () => void }) {
  return <section className="overflow-hidden rounded-2xl border bg-card" aria-labelledby={`ranking-${kind}-title`}>
    <div className="flex items-start justify-between gap-3 border-b p-4"><div className="flex min-w-0 gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Icon className="size-4" /></span><div><h2 id={`ranking-${kind}-title`} className="text-sm font-semibold">{title}</h2><p className="mt-1 text-[10px] leading-4 text-muted-foreground">{caption}</p></div></div><Badge variant="secondary" className="shrink-0 text-[9px]">{metric}</Badge></div>
    {items.length ? <div className="space-y-1 p-2">{items.map(item => <RankingRow key={`${kind}-${item.rank}`} item={item} kind={kind} onProvider={onProvider} />)}</div> : <div className="p-7 text-center"><Medal className="mx-auto size-7 text-muted-foreground" /><p className="mt-3 text-xs font-medium">当前周期暂无上榜记录</p><p className="mt-1 text-[10px] text-muted-foreground">完成订单后，榜单会自动更新</p></div>}
  </section>
}

function RankingRow({ item, kind, onProvider }: { item: RankItem; kind: 'providers' | 'consumers'; onProvider?: () => void }) {
  const clickable = kind === 'providers' && item.id > 0
  const content = <><span className={cn('flex w-7 shrink-0 flex-col items-center text-xs font-semibold text-muted-foreground tabular-nums', item.rank <= 3 && 'text-primary', item.rank === 1 && 'text-sm')}><span>{item.rank}</span>{item.rank <= 3 && <span className="mt-0.5 text-[7px] tracking-wider">TOP</span>}</span><Avatar className={cn('size-9 shrink-0 border border-primary/15 bg-primary/10', item.rank === 1 && 'size-10')}><AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">{item.nickname.slice(0, 1)}</AvatarFallback></Avatar><span className="min-w-0 flex-1"><span className="block break-words text-xs font-semibold">{item.nickname}</span><span className="mt-1 block text-[9px] text-muted-foreground">称号 · {item.title}{kind === 'consumers' ? ` · 完成 ${item.count} 单` : ''}</span></span><span className="max-w-[38%] shrink-0 text-right tabular-nums"><span className={cn('block break-words text-sm font-semibold', item.rank === 1 && 'text-primary')}>{kind === 'providers' ? item.count : formatWaveAmount(item.amount)}</span><span className="mt-0.5 block text-[9px] text-muted-foreground">{kind === 'providers' ? '单' : '浪花'}</span></span>{clickable && <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />}</>
  const className = cn('flex min-h-[60px] w-full items-center gap-2 rounded-xl px-2 py-2 text-left', item.rank === 1 && 'bg-primary/[0.07]')
  return clickable ? <button type="button" className={cn(className, 'transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring')} onClick={onProvider} aria-label={`第 ${item.rank} 名，${item.nickname}，完成 ${item.count} 单，查看陪玩`}>{content}</button> : <div className={className} aria-label={`第 ${item.rank} 名，${item.nickname}`}>{content}</div>
}

function RankingSkeleton() {
  return <div className="rounded-2xl border bg-card p-4"><div className="flex gap-3 border-b pb-4"><Skeleton className="size-9 rounded-xl" /><div className="flex-1 space-y-2"><Skeleton className="h-4 w-28" /><Skeleton className="h-3 w-44 max-w-full" /></div></div><div className="mt-3 space-y-3">{Array.from({ length: 3 }, (_, index) => <div key={index} className="flex items-center gap-3"><Skeleton className="size-8 rounded-full" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-4 w-12" /></div>)}</div></div>
}
