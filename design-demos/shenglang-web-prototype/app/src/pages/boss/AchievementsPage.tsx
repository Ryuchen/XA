import {
  AlertCircle,
  ArrowLeft,
  Award,
  Check,
  ChevronRight,
  Crown,
  Flame,
  Gem,
  LockKeyhole,
  LogIn,
  Medal,
  ReceiptText,
  RefreshCw,
  Sparkles,
  Target,
  Trophy,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type AchievementsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'no-progress' | 'near-unlock' | 'all-unlocked'

interface AchievementsPageProps {
  scenario: AchievementsScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onMembership: () => void
  onRetry: () => void
}

type Metric = 'orders' | 'amount'
interface Achievement {
  code: string
  title: string
  desc: string
  icon: LucideIcon
  metric: Metric
  target: number
}

const definitions: Achievement[] = [
  { code: 'first_order', title: '初出茅庐', desc: '完成首单，开启声浪旅程', icon: Target, metric: 'orders', target: 1 },
  { code: 'regular', title: '常客', desc: '完成 5 单，默契渐渐成形', icon: Flame, metric: 'orders', target: 5 },
  { code: 'veteran', title: '资深玩家', desc: '完成 10 单，解锁资深身份', icon: Trophy, metric: 'orders', target: 10 },
  { code: 'big_spender', title: '消费达人', desc: '累计净消费达到 1,000 浪花', icon: Gem, metric: 'amount', target: 1000 },
  { code: 'whale', title: '俱乐部之星', desc: '累计净消费达到 5,000 浪花', icon: Crown, metric: 'amount', target: 5000 },
]

function format(value: number) {
  return formatWaveAmount(value)
}

export function AchievementsPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onMembership, onRetry }: AchievementsPageProps) {
  const orderCount = scenario === 'all-unlocked' ? 12 : scenario === 'near-unlock' ? 9 : scenario === 'no-progress' ? 0 : 6
  const amount = scenario === 'all-unlocked' ? 5200 : scenario === 'near-unlock' ? 4980 : scenario === 'no-progress' ? 0 : 3680
  const items = definitions.map((item, index) => {
    const current = item.metric === 'orders' ? orderCount : amount
    const unlocked = current >= item.target
    const percent = unlocked ? 100 : Math.min(100, Math.max(0, current / item.target * 100))
    return { ...item, index, current, unlocked, percent, remaining: Math.max(0, item.target - current) }
  })
  const active = items.filter(item => !item.unlocked).sort((a, b) => b.percent - a.percent || a.index - b.index)
  const unlocked = items.filter(item => item.unlocked)

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2">
          <Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button>
          <Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">成就图鉴 · 示例</Badge>
        </div>
        <header className="px-4 pb-4 pt-2">
          <p className="text-[11px] font-medium text-primary">声浪俱乐部 · 成长印记</p>
          <h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">我的成就</h1>
          <p className="mt-1 text-xs text-muted-foreground">每一次并肩，都值得被记住</p>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Award className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录查看你的成就</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">同步完成订单与累计净消费，点亮属于你的俱乐部印记。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
        ) : scenario === 'loading' ? (
          <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载我的成就"><Skeleton className="h-40 w-full rounded-3xl" /><Skeleton className="h-48 w-full rounded-2xl" /><Skeleton className="h-40 w-full rounded-2xl" /><Skeleton className="h-40 w-full rounded-2xl" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">成就暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会改变订单、消费或成就状态。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
        ) : scenario === 'empty' ? (
          <section className="px-4"><div className="rounded-3xl border border-dashed bg-card/60 p-7 text-center"><Medal className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-4 text-xl font-semibold">成就内容准备中</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">当前还没有可展示的成就，完成订单与净消费记录会继续保留。</p><Button type="button" variant="outline" className="mt-6 h-11 w-full" onClick={onOrders}><ReceiptText className="size-4" />查看完成订单</Button></div></section>
        ) : (
          <div className="space-y-5 px-4 pb-5">
            <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(145deg,rgba(30,58,138,0.38),rgba(2,6,23,0.72)_72%)] p-5" aria-labelledby="achievement-overview-title">
              <div className="absolute -right-8 -top-10 size-36 rounded-full bg-sky-400/10 blur-2xl" />
              <div className="relative flex items-start justify-between gap-4">
                <div><p id="achievement-overview-title" className="text-[11px] text-sky-200">成就收集进度</p><p className="mt-2 text-[34px] font-semibold leading-none tracking-[-0.04em] tabular-nums">{unlocked.length}<span className="ml-1 text-base font-normal text-muted-foreground">/ {items.length}</span></p><p className="mt-2 text-[11px] text-muted-foreground">已点亮 {unlocked.length} 枚俱乐部印记</p></div>
                <div className="grid size-14 shrink-0 place-items-center rounded-2xl border border-sky-200/20 bg-sky-300/10 text-sky-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]"><Trophy className="size-7" /></div>
              </div>
              <Progress value={unlocked.length / items.length * 100} className="relative mt-5 h-2 bg-white/10" aria-label={`成就收集进度 ${unlocked.length}/${items.length}`} />
              <div className="relative mt-4 grid grid-cols-2 gap-3 border-t border-white/10 pt-4"><div><p className="text-lg font-semibold tabular-nums">{active.length}</p><p className="mt-0.5 text-[10px] text-muted-foreground">进行中</p></div><div className="border-l border-white/10 pl-4"><p className="text-lg font-semibold text-success tabular-nums">{unlocked.length}</p><p className="mt-0.5 text-[10px] text-muted-foreground">已达成</p></div></div>
            </section>

            {active.length > 0 && <section aria-labelledby="active-achievements-title">
              <div className="mb-3 flex items-end justify-between gap-3"><div><h2 id="active-achievements-title" className="text-base font-semibold">继续点亮</h2><p className="mt-1 text-[10px] text-muted-foreground">按完成进度排列，先完成最近目标</p></div><Badge variant="secondary" className="text-[9px]">{active.length} 项进行中</Badge></div>
              <div className="space-y-3">{active.map((item, index) => <AchievementCard key={item.code} item={item} recent={index === 0} />)}</div>
            </section>}

            {unlocked.length > 0 && <section aria-labelledby="unlocked-achievements-title">
              <div className="mb-3 flex items-end justify-between gap-3"><div><h2 id="unlocked-achievements-title" className="text-base font-semibold">已达成</h2><p className="mt-1 text-[10px] text-muted-foreground">已经点亮的声浪印记</p></div><span className="text-[10px] text-success">{unlocked.length} 枚已解锁</span></div>
              <div className="grid grid-cols-2 gap-2">{unlocked.map(item => <UnlockedCard key={item.code} item={item} />)}</div>
            </section>}

            <section className="overflow-hidden rounded-2xl border bg-card" aria-label="成就关联业务">
              <button type="button" className="flex min-h-[68px] w-full items-center gap-3 px-4 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={onOrders}><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><ReceiptText className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-xs font-semibold">查看完成订单</span><span className="mt-1 block text-[10px] text-muted-foreground">已完成 {orderCount} 单 · 累计净消费 {format(amount)} 浪花</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button>
              <button type="button" className="flex min-h-[68px] w-full items-center gap-3 border-t px-4 text-left focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={onMembership}><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Sparkles className="size-4" /></span><span className="min-w-0 flex-1"><span className="block text-xs font-semibold">设置会员名片标签</span><span className="mt-1 block text-[10px] text-muted-foreground">已达成成就可以展示在个人名片</span></span><ChevronRight className="size-4 shrink-0 text-muted-foreground" /></button>
            </section>

            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-primary" /><p>仅统计已完成且已支付或部分退款的订单；消费按退款后的净额计算。成就自动更新，无需手动领取。</p></div>
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}

type AchievementItem = Achievement & { index: number; current: number; unlocked: boolean; percent: number; remaining: number }

function AchievementCard({ item, recent }: { item: AchievementItem; recent: boolean }) {
  const Icon = item.icon
  const unit = item.metric === 'amount' ? '浪花' : '单'
  return <article className={cn('rounded-2xl border bg-card p-4', recent && 'border-primary/35 bg-primary/[0.055]')} aria-labelledby={`achievement-${item.code}`}>
    <div className="flex items-start gap-3"><div className={cn('grid size-11 shrink-0 place-items-center rounded-xl bg-muted/60 text-muted-foreground', recent && 'bg-primary/15 text-primary')}><Icon className="size-5" /></div><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-2"><h3 id={`achievement-${item.code}`} className="break-words text-sm font-semibold">{item.title}</h3>{recent ? <Badge className="shrink-0 px-2 text-[9px]">最近目标</Badge> : <Badge variant="secondary" className="shrink-0 text-[9px]">进行中</Badge>}</div><p className="mt-1 break-words text-[10px] leading-4 text-muted-foreground">{item.desc}</p></div></div>
    <div className="mt-4 flex items-end justify-between gap-3"><span className="text-[10px] text-muted-foreground">{item.metric === 'amount' ? '累计净消费' : '完成订单'}</span><p className="text-right tabular-nums"><span className="text-lg font-semibold">{format(item.current)}</span><span className="ml-1 text-[10px] text-muted-foreground">/ {format(item.target)} {unit}</span></p></div>
    <Progress value={item.percent} className="mt-2 h-2" aria-label={`${item.title}完成进度 ${Math.floor(item.percent)}%`} />
    <p className="mt-2 text-[10px] font-medium text-primary">还差 {format(item.remaining)} {unit}即可点亮</p>
  </article>
}

function UnlockedCard({ item }: { item: AchievementItem }) {
  const Icon = item.icon
  return <article className="relative min-w-0 overflow-hidden rounded-2xl border border-success/20 bg-success/[0.035] p-3" aria-label={`${item.title}，已达成`}><div className="flex items-center justify-between gap-2"><div className="grid size-9 place-items-center rounded-xl bg-success/10 text-success"><Icon className="size-4" /></div><span className="grid size-5 place-items-center rounded-full bg-success text-success-foreground"><Check className="size-3" /></span></div><h3 className="mt-3 break-words text-xs font-semibold">{item.title}</h3><p className="mt-1 text-[9px] text-success">已达成</p><p className="mt-2 border-t pt-2 text-[9px] leading-4 text-muted-foreground tabular-nums">{item.metric === 'amount' ? `${format(item.current)} 浪花` : `${item.current} 单`}</p></article>
}
