import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  CalendarCheck,
  Check,
  ChevronRight,
  Gift,
  LoaderCircle,
  LockKeyhole,
  LogIn,
  RefreshCw,
  Sparkles,
  Trophy,
  WalletCards,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type CheckinScenario = 'default' | 'guest' | 'loading' | 'error' | 'ineligible' | 'checked' | 'makeup' | 'no-cards' | 'submitting' | 'submit-error' | 'empty-rewards' | 'full-attendance'

interface CheckinPageProps {
  scenario: CheckinScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onWallet: () => void
  onRetry: () => void
}

const weekdays = ['日', '一', '二', '三', '四', '五', '六']
const rewards = [
  { seq: 3, name: '初见回响', amount: 6, description: '累计签到 3 天' },
  { seq: 7, name: '默契相伴', amount: 18, description: '累计签到 7 天' },
  { seq: 15, name: '逐浪好礼', amount: 38, description: '累计签到 15 天' },
  { seq: 30, name: '全勤礼赠', amount: 88, description: '本月签到 30 天' },
]

export function CheckinPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onWallet, onRetry }: CheckinPageProps) {
  const initialChecked = scenario === 'full-attendance' ? Array.from({ length: 30 }, (_, index) => index + 1) : scenario === 'checked' ? [1, 2, 3] : ['makeup', 'no-cards'].includes(scenario) ? [1] : [1, 2]
  const [checkedDays, setCheckedDays] = useState<number[]>(initialChecked)
  const [cards, setCards] = useState(scenario === 'no-cards' ? 0 : scenario === 'full-attendance' ? 3 : 2)
  const [submitting, setSubmitting] = useState(scenario === 'submitting')
  const [makeupDay, setMakeupDay] = useState<number | null>(null)
  const [feedback, setFeedback] = useState('')
  const [issue, setIssue] = useState(scenario === 'submit-error' ? '签到结果暂未确认，请刷新日历核对，避免重复操作。' : '')
  const eligible = scenario !== 'ineligible'
  const todayChecked = checkedDays.includes(3)
  const spend = eligible ? 168 : 86
  const threshold = 128
  const makeupAvailable = ['makeup', 'no-cards'].includes(scenario) && !checkedDays.includes(2) ? [2] : []
  const continuous = scenario === 'full-attendance' ? 30 : checkedDays.includes(3) ? checkedDays.length : checkedDays.includes(2) ? 2 : 1
  const busy = submitting

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function complete(day: number) {
    setSubmitting(true)
    setFeedback('')
    setIssue('')
    window.setTimeout(() => {
      setSubmitting(false)
      setMakeupDay(null)
      if (scenario === 'submit-error') {
        setIssue('签到结果暂未确认，请刷新日历核对，避免重复操作。')
        return
      }
      setCheckedDays(current => current.includes(day) ? current : [...current, day].sort((a, b) => a - b))
      if (day !== 3) setCards(current => Math.max(0, current - 1))
      setFeedback(`${day === 3 ? '签到' : '补签'}成功，获得 6 浪花。`)
    }, 600)
  }

  function checkin() {
    if (busy || todayChecked || !eligible || issue) return
    complete(3)
  }

  function openMakeup(day: number) {
    if (busy || !makeupAvailable.includes(day)) return
    if (!cards) {
      setFeedback('暂无补签卡，当日消费满 200 浪花可获得 1 张。')
      return
    }
    setMakeupDay(day)
  }

  function refreshStatus() {
    setIssue('')
    setFeedback('签到状态已刷新，请以当前日历与钱包记录为准。')
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">每日签到 · 示例</Badge></div>
        <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪俱乐部 · 每日回响</p><h1 className="mt-2 text-[28px] font-semibold tracking-[-0.04em]">每一天，都有回响</h1><p className="mt-1 text-xs text-muted-foreground">完成当日消费签到，领取本月专属奖励</p></header>

        {scenario === 'guest' ? <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><CalendarCheck className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录查看签到日历</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">同步本月签到、连续记录与补签卡，继续每一次默契相伴。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section> : scenario === 'loading' ? <div className="space-y-3 px-4" aria-busy="true" aria-label="正在加载签到日历"><Skeleton className="h-28 w-full rounded-2xl" /><Skeleton className="h-32 w-full rounded-2xl" /><Skeleton className="h-[350px] w-full rounded-2xl" /></div> : scenario === 'error' ? <section className="px-4"><div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">签到日历暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会签到、补签或消耗补签卡。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section> : (
          <div className="space-y-4 px-4 pb-5">
            <section className="grid grid-cols-3 overflow-hidden rounded-2xl border bg-card py-4" aria-label="本月签到概览"><Summary value={checkedDays.length} unit="天" label="本月已签" /><Summary value={continuous} unit="天" label="连续签到" bordered /><Summary value={cards} unit="张" label="可用补签卡" bordered /></section>

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="spend-title"><div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><WalletCards className="size-4 text-primary" /><h2 id="spend-title" className="text-sm font-semibold">今日消费</h2></div><Badge variant={eligible ? 'outline' : 'secondary'} className={cn('text-[9px]', eligible && 'border-success/25 bg-success/[0.07] text-success')}>{eligible ? '已达标' : '未达标'}</Badge></div><p className="mt-3 text-[26px] font-semibold tracking-tight tabular-nums">{formatWaveAmount(spend)}<span className="ml-1 text-xs font-normal text-muted-foreground">/ {formatWaveAmount(threshold)} 浪花</span></p><Progress value={Math.min(100, spend / threshold * 100)} className="mt-3 h-2" aria-label="今日签到消费进度" /><p className="mt-2 text-[10px] leading-4 text-muted-foreground">当日消费满 200 浪花可获得 1 张补签卡，最多持有 3 张。</p></section>

            {issue && <section role="alert" className="rounded-2xl border border-warning/25 bg-warning/[0.06] p-4"><div className="flex gap-2"><AlertCircle className="mt-0.5 size-4 shrink-0 text-warning" /><p className="text-[11px] leading-5 text-muted-foreground">{issue}</p></div><Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" onClick={refreshStatus}><RefreshCw className="size-4" />刷新签到状态</Button></section>}
            {feedback && <div role="status" className="flex gap-2 rounded-xl border border-primary/20 bg-primary/[0.06] p-3 text-[11px] leading-5 text-primary"><Check className="mt-0.5 size-4 shrink-0" />{feedback}</div>}

            <section className="rounded-2xl border bg-card p-3" aria-labelledby="calendar-title"><div className="flex min-h-11 items-center justify-between gap-3 px-1"><h2 id="calendar-title" className="text-sm font-semibold">2026 年 11 月</h2><span className="text-[10px] text-muted-foreground">标有「补签」的日期可点击</span></div><div className="grid grid-cols-7 gap-1">{weekdays.map(day => <span key={day} className="py-1 text-center text-[10px] text-muted-foreground">{day}</span>)}</div><div className="mt-1 grid grid-cols-7 gap-1">{Array.from({ length: 30 }, (_, index) => index + 1).map(day => { const checked = checkedDays.includes(day); const makeup = makeupAvailable.includes(day); const today = day === 3; return <button key={day} type="button" className={cn('flex min-h-12 w-full flex-col items-center justify-center rounded-lg text-xs text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring', checked && 'bg-primary/10 text-primary', today && 'ring-1 ring-inset ring-primary', makeup && 'bg-warning/[0.08] text-warning')} disabled={!makeup || busy} onClick={() => openMakeup(day)} aria-label={`11 月 ${day} 日，${checked ? '已签到' : makeup ? '可补签' : today ? '今天' : '不可补签'}`}><span>{day}</span><span className="mt-0.5 min-h-3 text-[8px]">{checked ? '已签' : makeup ? '补签' : today ? '今天' : ''}</span></button>})}</div><Button type="button" className="mt-4 h-12 w-full" disabled={busy || todayChecked || !eligible || !!issue} onClick={checkin}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : todayChecked ? <Check className="size-4" /> : <CalendarCheck className="size-4" />}{busy ? '正在提交签到' : todayChecked ? '今日已签到' : eligible ? '立即签到' : `再消费 ${formatWaveAmount(threshold - spend)} 浪花可签到`}</Button></section>

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="rewards-title"><div className="flex items-center justify-between gap-3"><h2 id="rewards-title" className="text-sm font-semibold">本月签到礼物</h2><span className="text-[10px] text-muted-foreground">按累计签到天数达成</span></div>{scenario === 'empty-rewards' ? <div className="mt-3 rounded-xl border border-dashed p-5 text-center"><Gift className="mx-auto size-6 text-muted-foreground" /><p className="mt-2 text-xs font-medium">本期奖励待公布</p><p className="mt-1 text-[10px] text-muted-foreground">签到记录会继续保留</p></div> : <div className="phone-scrollbar -mx-4 mt-3 overflow-x-auto px-4"><div className="flex w-max gap-2 pb-1">{rewards.map(reward => { const reached = checkedDays.length >= reward.seq; return <article key={reward.seq} className={cn('w-32 rounded-xl border bg-muted/25 p-3', reached && 'border-primary/30 bg-primary/[0.07]')}><div className="flex items-center justify-between gap-2"><span className="text-[10px] text-muted-foreground">第 {reward.seq} 天</span>{reached && <Check className="size-3.5 text-primary" />}</div><h3 className="mt-2 text-xs font-semibold">{reward.name}</h3><p className="mt-1 text-sm font-semibold text-primary">{formatWaveAmount(reward.amount)} <span className="text-[9px] font-normal text-muted-foreground">浪花</span></p><p className="mt-2 text-[9px] text-muted-foreground">{reached ? '已达成' : reward.description}</p></article>})}</div></div>}</section>

            <section className="overflow-hidden rounded-2xl border border-primary/20 bg-[linear-gradient(140deg,rgba(30,58,138,0.28),rgba(2,6,23,0.5))] p-4"><div className="flex items-start gap-3"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-sky-300/10 text-sky-300"><Trophy className="size-5" /></div><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">本月全勤 · 海浪纪念礼</h2><Badge variant="outline" className="border-sky-200/20 text-[9px] text-sky-200">{scenario === 'full-attendance' ? '已获得' : '全勤奖励'}</Badge></div><p className="mt-2 text-[10px] leading-5 text-muted-foreground">完成本月全部签到后，额外获得 128 浪花与全勤纪念标识。</p></div></div></section>
            <button type="button" className="flex min-h-11 w-full items-center justify-center gap-1 text-[11px] text-primary focus-visible:ring-2 focus-visible:ring-ring" onClick={onWallet}><Sparkles className="size-3.5" />签到奖励可在钱包流水中查看<ChevronRight className="size-3.5" /></button>
            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><LockKeyhole className="mt-0.5 size-4 shrink-0 text-primary" /><p>签到与补签以服务器日历为准。结果未知时先刷新核对，不重复消耗补签卡。</p></div>
          </div>
        )}
      </main>

      <Dialog open={makeupDay !== null} onOpenChange={open => !open && !busy && setMakeupDay(null)}><DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-xl bg-primary/10 text-primary"><CalendarCheck className="size-5" /></div><DialogTitle className="text-left">补签 11 月 {makeupDay} 日</DialogTitle><DialogDescription className="text-left leading-6">将消耗 1 张补签卡，当前共有 {cards} 张。补签结果以服务器日历为准。</DialogDescription></DialogHeader><div className="rounded-xl bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">本次仅为原型演示，不会修改真实签到或钱包数据。</div><DialogFooter><div className="grid w-full grid-cols-2 gap-2"><DialogClose asChild><Button type="button" variant="outline" className="h-11" disabled={busy}>再想想</Button></DialogClose><Button type="button" className="h-11" disabled={busy} onClick={() => makeupDay && complete(makeupDay)}>{busy && <LoaderCircle className="size-4 animate-spin" />}确认补签</Button></div></DialogFooter></DialogContent></Dialog>
    </PhoneFrame>
  )
}

function Summary({ value, unit, label, bordered = false }: { value: number; unit: string; label: string; bordered?: boolean }) {
  return <div className={cn('text-center', bordered && 'border-l')}><p className="text-[26px] font-semibold leading-none tracking-tight text-primary tabular-nums">{value}<span className="ml-1 text-[10px] font-normal text-muted-foreground">{unit}</span></p><p className="mt-2 text-[10px] text-muted-foreground">{label}</p></div>
}
