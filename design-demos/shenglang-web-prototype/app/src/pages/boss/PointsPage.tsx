import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  CircleDollarSign,
  Copy,
  Gift,
  Headphones,
  History,
  LoaderCircle,
  LogIn,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Ticket,
  Trophy,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type PointsScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'insufficient' | 'soldout' | 'pending' | 'redeeming' | 'claims' | 'ledger' | 'more-error' | 'kook-bound'
type PointsTab = 'shop' | 'claims' | 'ledger'
interface Reward { id: number; name: string; kind: string; category: string; cost: number; stock: number | null; description: string; icon: 'gift' | 'ticket' | 'balance' | 'tag' }

interface PointsPageProps {
  scenario: PointsScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onLottery: () => void
  onClaim: () => void
  onRules: () => void
  onRetry: () => void
}

const rewards: Reward[] = [
  { id: 1, name: '68 元体验单资格', kind: '专属权益', category: 'SERVICE', cost: 680, stock: 12, description: '兑换后补充游戏 UID，由俱乐部安排交付。', icon: 'ticket' },
  { id: 2, name: '20 浪花余额', kind: '余额兑换', category: 'BALANCE', cost: 200, stock: null, description: '兑换成功后 20 浪花自动到账。', icon: 'balance' },
  { id: 3, name: '限定高亮 TAG', kind: '虚拟物品', category: 'VIRTUAL', cost: 1280, stock: 3, description: '发放后有效 30 天，TAG 内容需客服复核。', icon: 'tag' },
  { id: 4, name: '声浪定制耳机', kind: '实物礼物', category: 'PHYSICAL', cost: 2880, stock: 0, description: '兑换后填写收件人、电话与完整收货地址。', icon: 'gift' },
]
const claims = [
  { id: 101, name: '声浪定制耳机', status: '待补充资料', source: '积分兑换', time: '2026-11-02 18:20', action: '请补充收件资料' },
  { id: 102, name: '限定高亮 TAG', status: '待审核', source: '幸运转盘', time: '2026-11-01 21:08', action: '标签待审核' },
  { id: 103, name: '20 浪花余额', status: '已完成', source: '会员赠礼', time: '2026-10-28 12:30', action: '' },
]
const ledger = [
  { id: 1, remark: '有效报单确认实销', time: '2026-11-03 12:20', amount: 128 },
  { id: 2, remark: '生日当日消费积分翻倍', time: '2026-11-02 20:16', amount: 68 },
  { id: 3, remark: '兑换 20 浪花余额', time: '2026-11-01 14:08', amount: -200 },
  { id: 4, remark: 'KOOK 每日签到', time: '2026-11-01 09:30', amount: 5 },
]
const filters = [{ value: '', label: '全部' }, { value: 'PHYSICAL', label: '实物礼物' }, { value: 'VIRTUAL', label: '虚拟物品' }, { value: 'SERVICE', label: '专属权益' }, { value: 'BALANCE', label: '余额兑换' }]

export function PointsPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onLottery, onClaim, onRules, onRetry }: PointsPageProps) {
  const initialTab: PointsTab = scenario === 'claims' ? 'claims' : scenario === 'ledger' || scenario === 'more-error' ? 'ledger' : 'shop'
  const [tab, setTab] = useState<PointsTab>(initialTab)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState<Reward | null>(null)
  const [redeeming, setRedeeming] = useState(scenario === 'redeeming')
  const [pending, setPending] = useState(['pending', 'redeeming'].includes(scenario))
  const [notice, setNotice] = useState(scenario === 'more-error' ? '更多积分明细加载失败，请重试。' : '')
  const [bindingCode, setBindingCode] = useState('')
  const [moreLoaded, setMoreLoaded] = useState(false)
  const points = scenario === 'insufficient' ? 80 : 1280
  const visibleRewards = scenario === 'empty' ? [] : rewards.filter(reward => !filter || reward.category === filter || (filter === 'SERVICE' && reward.category === 'VIRTUAL'))

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
    if (key === 'mine') return onBack()
  }

  function confirmRedemption() {
    if (!selected || redeeming || pending) return
    setRedeeming(true)
    setSelected(null)
    window.setTimeout(() => {
      setRedeeming(false)
      setPending(false)
      onClaim()
    }, 600)
  }

  function retryRedemption() {
    if (redeeming) return
    setRedeeming(true)
    window.setTimeout(() => {
      setRedeeming(false)
      setPending(false)
      onClaim()
    }, 600)
  }

  function createBindingCode() {
    if (redeeming) return
    setRedeeming(true)
    window.setTimeout(() => { setRedeeming(false); setBindingCode('SL-826410'); setNotice('绑定码已生成并模拟复制，10 分钟内有效。') }, 400)
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28">
        <div className="flex items-center justify-between px-4 py-2"><Button type="button" variant="ghost" className="h-11 rounded-full px-2 text-xs" onClick={onBack}><ArrowLeft className="size-4" />我的</Button><Badge variant="outline" className="border-primary/20 bg-primary/5 text-[10px] text-primary">积分账户 · 示例</Badge></div>
        <header className="px-4 pb-4 pt-2"><p className="text-[11px] font-medium text-primary">声浪俱乐部</p><h1 className="mt-1 text-[28px] font-semibold tracking-[-0.04em]">我的积分</h1><p className="mt-1 text-xs text-muted-foreground">积分、兑换与权益记录</p></header>

        {scenario === 'guest' ? (
          <section className="px-4"><div className="rounded-3xl border bg-card p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Trophy className="size-7" /></div><h2 className="mt-4 text-xl font-semibold">登录查看我的积分</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">同步积分明细、兑换好礼与每一次幸运收获。</p><Button type="button" className="mt-6 h-12 w-full" onClick={onLogin}><LogIn className="size-4" />登录后查看</Button></div></section>
        ) : (
          <div className="space-y-4 px-4 pb-5">
            <section className="relative overflow-hidden rounded-3xl border border-primary/25 bg-[linear-gradient(140deg,rgba(30,58,138,0.4),rgba(2,6,23,0.96)_62%)] p-5" aria-label="积分账户">
              <div className="pointer-events-none absolute -right-14 -top-20 size-52 rounded-full border border-sky-200/10" /><Trophy className="pointer-events-none absolute -right-3 top-7 size-28 text-sky-200/[0.055]" strokeWidth={1} />
              <div className="relative"><p className="text-[11px] text-sky-100/60">可用积分</p><p className="mt-2 text-[38px] font-semibold leading-none tracking-[-0.045em] tabular-nums text-white">{points.toLocaleString()}<span className="ml-2 text-xs font-normal tracking-normal text-sky-100/60">积分</span></p><div className="mt-5 flex gap-2"><Button type="button" className="h-11 flex-1 bg-sky-300 text-xs text-slate-950 hover:bg-sky-200" onClick={onLottery}><Sparkles className="size-4" />幸运转盘<ArrowRight className="size-3.5" /></Button><Button type="button" variant="outline" className="h-11 border-sky-100/15 bg-white/[0.05] text-xs text-sky-50 hover:bg-white/10 hover:text-white" onClick={onRules}>积分规则</Button></div></div>
            </section>

            {pending && <section role="status" className="rounded-2xl border border-warning/25 bg-warning/[0.06] p-4"><div className="flex gap-3"><AlertCircle className="mt-0.5 size-5 shrink-0 text-warning" /><div><h2 className="text-sm font-semibold">兑换结果待确认</h2><p className="mt-1 text-[11px] leading-5 text-muted-foreground">「68 元体验单资格」结果未确认，请重试原申请，不会重复扣除积分。</p></div></div><Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" disabled={redeeming} onClick={retryRedemption}>{redeeming && <LoaderCircle className="size-4 animate-spin" />}{redeeming ? '正在核对原申请' : '重试原申请'}</Button></section>}
            {notice && <div role="status" className="rounded-xl border border-primary/20 bg-primary/[0.06] p-3 text-[11px] leading-5 text-primary">{notice}</div>}

            <section className="rounded-2xl border bg-card p-4" aria-labelledby="kook-title"><div className="flex items-start gap-3"><div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><Headphones className="size-5" /></div><div className="min-w-0 flex-1"><h2 id="kook-title" className="text-sm font-semibold">KOOK 积分</h2><p className="mt-1 text-[10px] leading-4 text-muted-foreground">绑定本人账号后，在线与签到自动计分</p></div><Badge variant="secondary" className="text-[9px]">{scenario === 'kook-bound' ? '已连接' : '待绑定'}</Badge></div>{scenario === 'kook-bound' ? <div className="mt-3 rounded-xl bg-muted/35 p-3 text-[11px] leading-5 text-muted-foreground"><p className="text-foreground">海浪收藏家 · 在线获得 36 积分</p><p>今日已签到，每日签到可获得 5 积分。</p></div> : <><Button type="button" variant="outline" className="mt-3 h-11 w-full text-xs" disabled={redeeming} onClick={createBindingCode}>{redeeming ? <LoaderCircle className="size-4 animate-spin" /> : <Copy className="size-4" />}获取并复制绑定码</Button>{bindingCode && <p className="mt-3 rounded-xl bg-primary/[0.06] p-3 text-[11px] leading-5 text-primary">请在 KOOK 签到大厅发送「绑定 {bindingCode}」，10 分钟内有效。</p>}</>}</section>

            <div className="grid grid-cols-3 rounded-xl bg-muted/45 p-1" role="tablist" aria-label="积分记录视图">{([['shop', '积分好礼'], ['claims', '权益记录'], ['ledger', '积分明细']] as [PointsTab, string][]).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={tab === value} className={cn('h-11 rounded-lg text-[11px] font-medium text-muted-foreground', tab === value && 'bg-card text-primary shadow-sm')} onClick={() => { setTab(value); setNotice('') }}>{label}</button>)}</div>

            {scenario === 'loading' ? <div className="space-y-3" aria-busy="true" aria-label="正在加载积分信息"><Skeleton className="h-44 w-full rounded-2xl" /><Skeleton className="h-44 w-full rounded-2xl" /><Skeleton className="h-44 w-full rounded-2xl" /></div> : scenario === 'error' ? <div role="alert" className="rounded-3xl border border-destructive/25 bg-destructive/5 p-7 text-center"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">积分信息暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会扣除积分或改变兑换记录。</p><Button type="button" className="mt-6 h-11 w-full" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div> : tab === 'shop' ? (
              <section aria-label="积分好礼"><div className="phone-scrollbar -mx-4 overflow-x-auto px-4"><div className="flex w-max gap-2 pb-1" role="group" aria-label="好礼分类">{filters.map(item => <button key={item.value} type="button" aria-pressed={filter === item.value} className={cn('h-11 rounded-full border px-4 text-xs', filter === item.value ? 'border-primary bg-primary text-primary-foreground' : 'bg-card text-muted-foreground')} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div></div>{visibleRewards.length ? <div className="mt-3 grid grid-cols-2 gap-3">{visibleRewards.map(reward => { const soldOut = reward.stock === 0 || scenario === 'soldout'; const disabled = redeeming || pending || soldOut; return <article key={reward.id} className="flex min-w-0 flex-col rounded-2xl border bg-card p-3"><div className="grid h-20 place-items-center rounded-xl bg-[linear-gradient(145deg,rgba(37,99,235,0.2),rgba(56,189,248,0.06))] text-primary"><RewardIcon icon={reward.icon} /></div><p className="mt-3 text-[10px] text-muted-foreground">{reward.kind}</p><h2 className="mt-1 min-h-10 text-sm font-semibold leading-5">{reward.name}</h2><p className="mt-2 text-lg font-semibold tabular-nums text-primary">{reward.cost}<span className="ml-1 text-[10px] font-normal text-muted-foreground">积分</span></p><p className="mt-1 min-h-4 text-[10px] text-muted-foreground">{reward.stock !== null ? `剩余 ${reward.stock} 份` : '不限份数'}</p><Button type="button" variant={soldOut ? 'secondary' : 'outline'} className="mt-3 h-11 w-full px-2 text-xs" disabled={disabled} onClick={() => points < reward.cost ? setNotice('积分还不够，继续累积吧。') : setSelected(reward)}>{soldOut ? '已兑完' : points < reward.cost ? '积分不足' : '兑换'}</Button></article>})}</div> : <EmptyPoints icon={<Gift className="size-7" />} title="暂无积分好礼" copy="礼物上架后会展示在这里，请稍后再来。" />}</section>
            ) : tab === 'claims' ? (
              <section className="space-y-3" aria-label="权益记录">{claims.map(claim => <button key={claim.id} type="button" className="block w-full rounded-2xl border bg-card p-4 text-left focus-visible:ring-2 focus-visible:ring-ring" onClick={onClaim}><span className="flex items-start justify-between gap-3"><span><span className="block text-sm font-semibold">{claim.name}</span><span className="mt-1 block text-[10px] text-muted-foreground">{claim.source} · {claim.time}</span></span><Badge variant="secondary" className="shrink-0 text-[9px]">{claim.status}</Badge></span>{claim.action && <span className="mt-3 block rounded-lg bg-warning/[0.07] p-2 text-[10px] text-warning">{claim.action}</span>}<span className="mt-2 flex min-h-11 items-center justify-end text-[11px] text-primary">查看交付详情<ChevronRight className="size-3.5" /></span></button>)}<div className="py-2 text-center text-[10px] text-muted-foreground">已显示全部权益记录</div></section>
            ) : (
              <section className="rounded-2xl border bg-card p-4" aria-label="积分明细">{ledger.map(row => <div key={row.id} className="flex min-h-[60px] items-center justify-between gap-3 border-b py-3 last:border-0"><div><p className="text-xs font-medium">{row.remark}</p><p className="mt-1 text-[10px] text-muted-foreground">{row.time}</p></div><strong className={cn('text-sm tabular-nums', row.amount > 0 ? 'text-primary' : 'text-foreground')}>{row.amount > 0 ? '+' : ''}{row.amount}</strong></div>)}{moreLoaded && <div className="flex min-h-[60px] items-center justify-between gap-3 border-t py-3"><div><p className="text-xs font-medium">俱乐部活动加赠</p><p className="mt-1 text-[10px] text-muted-foreground">2026-10-30 18:00</p></div><strong className="text-sm tabular-nums text-primary">+20</strong></div>}<Button type="button" variant="ghost" className="mt-2 h-11 w-full text-xs" disabled={moreLoaded} onClick={() => scenario === 'more-error' ? setNotice('更多积分明细加载失败，请重试。') : setMoreLoaded(true)}>{moreLoaded ? <Check className="size-4" /> : <History className="size-4" />}{moreLoaded ? '已显示全部明细' : '加载更多'}</Button></section>
            )}
            <div className="flex gap-2 rounded-xl bg-primary/[0.045] p-3 text-[10px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>积分与兑换结果以账户记录为准；网络异常时请重试原申请，不要重复创建兑换。</p></div>
          </div>
        )}
      </main>

      <Dialog open={selected !== null} onOpenChange={open => !open && setSelected(null)}><DialogContent className="prototype-dialog rounded-3xl p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-xl bg-primary/10 text-primary"><PackageCheck className="size-5" /></div><DialogTitle className="text-left">确认兑换</DialogTitle><DialogDescription className="text-left leading-6">使用 {selected?.cost} 积分兑换「{selected?.name}」。</DialogDescription></DialogHeader><div className="rounded-xl bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">{selected?.description}</div><DialogFooter><div className="grid w-full grid-cols-2 gap-2"><DialogClose asChild><Button type="button" variant="outline" className="h-11">再想想</Button></DialogClose><Button type="button" className="h-11" disabled={redeeming} onClick={confirmRedemption}>{redeeming && <LoaderCircle className="size-4 animate-spin" />}确认兑换</Button></div></DialogFooter></DialogContent></Dialog>
    </PhoneFrame>
  )
}

function RewardIcon({ icon }: { icon: Reward['icon'] }) {
  if (icon === 'balance') return <CircleDollarSign className="size-8" strokeWidth={1.4} />
  if (icon === 'ticket') return <Ticket className="size-8" strokeWidth={1.4} />
  if (icon === 'tag') return <Sparkles className="size-8" strokeWidth={1.4} />
  return <Gift className="size-8" strokeWidth={1.4} />
}

function EmptyPoints({ icon, title, copy }: { icon: React.ReactNode; title: string; copy: string }) {
  return <div className="mt-3 rounded-3xl border border-dashed p-7 text-center"><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-muted/40 text-muted-foreground">{icon}</div><h2 className="mt-4 text-lg font-semibold">{title}</h2><p className="mt-2 text-xs leading-5 text-muted-foreground">{copy}</p></div>
}
