import { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Coins,
  Gift,
  Headphones,
  Inbox,
  Landmark,
  LockKeyhole,
  MessageCircleMore,
  ReceiptText,
  RefreshCw,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { FilterBar } from '@/components/prototype/FilterBar'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { useStore } from '@/components/store/store-context'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'

export type WalletScenario = 'default' | 'guest' | 'loading' | 'error' | 'empty' | 'refund' | 'more-error'
type TransactionType = 'TOPUP' | 'PAY' | 'REFUND' | 'INCOME'
type WalletFilter = '' | TransactionType

interface WalletPageProps {
  scenario: WalletScenario
  onLogin: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onSupport: () => void
  onRetry: () => void
}

interface Transaction {
  id: number
  type: TransactionType
  title: string
  amount: number
  balanceAfter: number
  remark: string
  time: string
}

const filters: { value: WalletFilter; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'TOPUP', label: '充值' },
  { value: 'PAY', label: '支付' },
  { value: 'REFUND', label: '退款' },
  { value: 'INCOME', label: '收入' },
]

const transactions: Transaction[] = [
  { id: 901, type: 'REFUND', title: '订单退款', amount: 49, balanceAfter: 326, remark: '订单 SL202611021019 · 接单时限已到', time: '2026-11-03 19:41' },
  { id: 898, type: 'PAY', title: '订单支付', amount: -41, balanceAfter: 277, remark: '王者娱乐双排 · SL202611031038', time: '2026-11-03 18:32' },
  { id: 887, type: 'TOPUP', title: '充值入账', amount: 200, balanceAfter: 318, remark: '客服核对充值 · TX202611031620', time: '2026-11-03 16:20' },
  { id: 872, type: 'INCOME', title: '推荐收益', amount: 18, balanceAfter: 118, remark: '好友完成首单 · 推荐奖励', time: '2026-11-02 22:10' },
  { id: 866, type: 'PAY', title: '订单支付', amount: -96, balanceAfter: 100, remark: '峡谷技术提升 · SL202611031031', time: '2026-11-02 21:06' },
  { id: 851, type: 'REFUND', title: '订单退款', amount: 49, balanceAfter: 196, remark: '订单 SL202611021019 · 自动取消退款', time: '2026-11-02 19:41' },
  { id: 843, type: 'PAY', title: '订单支付', amount: -68, balanceAfter: 147, remark: '峡谷默契双排 · SL202611031036', time: '2026-11-02 17:18' },
  { id: 820, type: 'TOPUP', title: '充值入账', amount: 215, balanceAfter: 215, remark: '客服核对充值 · TX202611011230', time: '2026-11-01 12:30' },
]

const transactionMeta: Record<TransactionType, { icon: LucideIcon; className: string }> = {
  TOPUP: { icon: ArrowDownLeft, className: 'bg-primary/10 text-primary' },
  PAY: { icon: ArrowUpRight, className: 'bg-muted text-muted-foreground' },
  REFUND: { icon: RefreshCw, className: 'bg-success/10 text-success' },
  INCOME: { icon: Gift, className: 'bg-sky-500/10 text-sky-500' },
}

export function WalletPage({ scenario, onLogin, onHome, onCompanions, onOrders, onSupport, onRetry }: WalletPageProps) {
  const { store } = useStore()
  const [filter, setFilter] = useState<WalletFilter>(scenario === 'refund' ? 'REFUND' : '')
  const [expanded, setExpanded] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState(false)
  const [rechargeOpen, setRechargeOpen] = useState(false)
  const [feature, setFeature] = useState<string | null>(null)

  const storeTransactions = useMemo(() => transactions.map(item => ({ ...item, remark: item.remark.replace(/SL|TX/g, store.slug.toUpperCase()) })), [store.slug])
  const filtered = useMemo(() => storeTransactions.filter(item => !filter || item.type === filter), [filter, storeTransactions])
  const visibleTransactions = scenario === 'empty' ? [] : filter || expanded ? filtered : filtered.slice(0, 5)
  const hasMore = !filter && !expanded && !['empty', 'guest', 'loading', 'error'].includes(scenario)
  const balance = scenario === 'empty' ? '0' : store.boss.balance
  const frozen = scenario === 'empty' ? '0' : store.boss.frozen
  const numericBalance = Math.max(0, Number(balance) || 0)
  const giftBalance = Math.min(77, numericBalance)
  const compensationBalance = Math.min(49, Math.max(0, numericBalance - giftBalance))
  const balanceParts = [
    { label: '预存', amount: Math.max(0, numericBalance - giftBalance - compensationBalance) },
    { label: '赠送', amount: giftBalance },
    { label: '赔款', amount: compensationBalance },
  ]

  function changeFilter(next: WalletFilter) {
    setFilter(next)
    setExpanded(false)
    setMoreError(false)
  }

  function loadMore() {
    if (loadingMore) return
    if (scenario === 'more-error' && !moreError) {
      setMoreError(true)
      return
    }
    setLoadingMore(true)
    window.setTimeout(() => {
      setExpanded(true)
      setMoreError(false)
      setLoadingMore(false)
    }, 450)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onOrders()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-32 md:pb-12">
        <PageHeader title={`${store.shortName} · 我的钱包`} description="只查看本店可用余额、冻结金额与每一笔资金变动。" />

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6"><Card className="rounded-[12px] py-0 text-center"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><WalletCards className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后查看钱包</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看可用余额、冻结金额与每一笔资金变动。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button></CardContent></Card></section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 md:grid md:grid-cols-2 md:space-y-0 md:px-6" aria-busy="true" aria-label="正在加载钱包"><Skeleton className="h-52 w-full rounded-[12px] md:col-span-2" /><Skeleton className="h-96 w-full rounded-[12px]" /><Skeleton className="h-44 w-full rounded-[12px]" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">钱包暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会改变你的余额与资金记录。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px] md:max-w-64" onClick={onRetry}>重新加载</Button></div></section>
        ) : (
          <div className="flex flex-col space-y-6 px-4 pb-6 md:grid md:grid-cols-[minmax(0,1fr)_320px] md:items-start md:gap-6 md:space-y-0 md:px-6 xl:grid-cols-[minmax(0,1fr)_360px]">
            <section className="order-1 overflow-hidden rounded-[12px] border border-slate-700 text-white shadow-sm md:col-span-2 md:row-start-1" style={{ backgroundColor: '#0F172A' }} aria-label="钱包余额摘要">
              <div className="p-5 md:flex md:items-center md:justify-between md:gap-8 md:p-6">
                <div><div className="flex items-center gap-3"><p className="text-sm text-slate-300">可用余额</p><Badge variant="outline" className="rounded-md border-white/20 bg-white/10 text-xs text-white hover:bg-white/10">示例钱包</Badge></div><p className="mt-4 text-[36px] font-semibold leading-none tracking-[-0.03em] tabular-nums">{formatWaveAmount(balance)}<span className="ml-2 text-sm font-normal text-slate-300">浪花</span></p></div>
                <div className="mt-5 border-t border-white/15 pt-4 md:mt-0 md:min-w-64 md:border-l md:border-t-0 md:pl-6 md:pt-0"><div className="flex items-center justify-between gap-4"><span className="flex items-center gap-2 text-sm text-slate-300"><LockKeyhole className="size-4 text-sky-300" />冻结金额</span><p className="text-xl font-semibold tabular-nums">{formatWaveAmount(frozen)}<span className="ml-1 text-xs font-normal text-slate-300">浪花</span></p></div><p className="mt-3 text-xs leading-5 text-slate-300">可用与冻结金额分别展示，资金变动以最新流水为准。</p></div>
              </div>
              <dl className="grid grid-cols-3 divide-x divide-white/15 border-t border-white/15" aria-label="可用余额构成">
                {balanceParts.map(part => (
                  <div key={part.label} className="px-2 py-4 text-center md:px-4">
                    <dt className="text-xs text-slate-300">{part.label}</dt>
                    <dd className="mt-1 whitespace-nowrap text-lg font-semibold tabular-nums">{formatWaveAmount(part.amount)}<span className="ml-1 text-[10px] font-normal text-slate-300">浪花</span></dd>
                  </div>
                ))}
              </dl>
            </section>

            <section className="order-3 overflow-hidden rounded-[12px] border bg-card shadow-sm md:order-none md:col-start-1 md:row-start-2" aria-labelledby="wallet-transactions-title">
              <div className="border-b p-4 md:p-5"><div className="flex items-end justify-between gap-3"><div><h2 id="wallet-transactions-title" className="text-base font-semibold">资金流水</h2><p className="mt-1 text-xs text-muted-foreground">变动金额、业务备注与变动后余额</p></div><span className="shrink-0 text-xs tabular-nums text-muted-foreground">{scenario === 'empty' ? 0 : filtered.length} 笔示例</span></div>
                <FilterBar className="mt-4" ariaLabel="资金流水筛选" items={filters} value={filter} onChange={changeFilter} columns={5} />
              </div>
              {visibleTransactions.length ? (
                <div aria-label="资金流水列表">{visibleTransactions.map(item => <TransactionRow key={item.id} item={item} />)}</div>
              ) : (
                <div className="px-5 py-10 text-center"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/60 text-muted-foreground"><Inbox className="size-6" /></div><h3 className="mt-4 text-base font-semibold">暂无资金流水</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">{filter ? '当前分类暂时没有记录，可切换其他分类查看。' : '充值、支付、退款与收入记录会在这里展示。'}</p>{filter && <Button type="button" variant="outline" className="mt-4 h-11 rounded-[8px]" onClick={() => changeFilter('')}>查看全部流水</Button>}</div>
              )}
              {(moreError || hasMore || visibleTransactions.length > 0) && <div className="border-t p-4 md:p-5">{moreError && <div role="alert" className="mb-3 rounded-[8px] border border-destructive/20 bg-destructive/5 p-3 text-center text-xs text-destructive">后续流水暂时没有加载成功，请重试。</div>}{hasMore && <Button type="button" variant="outline" className="h-11 w-full rounded-[8px]" onClick={loadMore} disabled={loadingMore}>{loadingMore ? <RefreshCw className="size-4 animate-spin" /> : <ChevronRight className="size-4 rotate-90" />}{loadingMore ? '正在加载' : moreError ? '加载失败，点击重试' : '加载更多'}</Button>}{!hasMore && visibleTransactions.length > 0 && <p className="text-center text-xs text-muted-foreground">已展示当前筛选下的全部示例流水</p>}</div>}
            </section>

            <aside className="order-2 space-y-6 md:order-none md:col-start-2 md:row-start-2" aria-label="钱包操作与说明">
              <Card className="rounded-[12px] py-0 shadow-sm"><CardContent className="p-4 md:p-5"><div className="flex items-center gap-3"><div className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary"><Landmark className="size-5" /></div><div><h2 className="text-base font-semibold">钱包充值</h2><p className="mt-1 text-xs text-muted-foreground">由客服核对付款结果</p></div></div><p className="mt-4 text-sm leading-6 text-muted-foreground">联系客服核对充值金额与付款结果，实际入账以余额和流水为准。</p><Button type="button" className="mt-4 h-11 w-full rounded-[8px]" onClick={() => setRechargeOpen(true)}><MessageCircleMore className="size-4" />联系客服充值</Button></CardContent></Card>
              <button type="button" className="flex min-h-12 w-full items-center justify-center gap-2 rounded-[8px] text-sm text-muted-foreground transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setFeature('钱包与流水说明')}><CircleHelp className="size-4" />钱包与流水说明<ChevronRight className="size-4" /></button>
            </aside>
          </div>
        )}
      </main>

      <Dialog open={rechargeOpen} onOpenChange={setRechargeOpen}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><Headphones className="size-5" /></div><DialogTitle className="text-left text-xl">联系客服充值</DialogTitle><DialogDescription className="text-left text-sm leading-6">客服将核对充值金额与付款结果。请勿重复付款，实际入账以钱包余额和充值流水为准。</DialogDescription></DialogHeader><div className="rounded-[8px] bg-muted/55 p-3 text-sm leading-6 text-muted-foreground">这是原型演示，不会打开真实客服会话，也不会发起收款或充值。</div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]">返回钱包</Button></DialogClose><Button type="button" className="h-11 rounded-[8px]" onClick={() => { setRechargeOpen(false); onSupport() }}>联系充值客服</Button></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={feature !== null} onOpenChange={open => !open && setFeature(null)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><Coins className="size-5" /></div><DialogTitle className="text-left text-xl">钱包与流水说明</DialogTitle><DialogDescription className="text-left text-sm leading-6">可用余额与冻结金额分开显示；每笔记录包含变动金额、业务备注、发生时间和变动后余额。充值由客服核对，结果未明确时请先刷新钱包，避免重复操作。</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回钱包</Button></DialogClose></DialogFooter></DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}

function TransactionRow({ item }: { item: Transaction }) {
  const meta = transactionMeta[item.type]
  const Icon = meta.icon
  const positive = item.amount > 0
  return (
    <article className="border-b p-4 last:border-0 md:p-5" aria-label={`${item.title}，${positive ? '入账' : '支出'} ${formatWaveAmount(Math.abs(item.amount))} 浪花，变动后余额 ${formatWaveAmount(item.balanceAfter)} 浪花`}>
      <div className="flex items-start gap-3">
        <div className={cn('grid size-10 shrink-0 place-items-center rounded-[8px]', meta.className)}><Icon className="size-4" /></div>
        <div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h3 className="text-sm font-semibold">{item.title}</h3><p className="mt-1 text-xs tabular-nums text-muted-foreground">{item.time}</p></div><p className={cn('shrink-0 text-lg font-semibold tabular-nums', positive ? 'text-success' : 'text-foreground')}>{positive ? '+' : '-'}{formatWaveAmount(Math.abs(item.amount))}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花</span></p></div>
          <p className="mt-3 break-all rounded-[8px] bg-muted/45 px-3 py-2 text-xs leading-5 text-muted-foreground">{item.remark}</p>
          <div className="mt-3 flex items-center justify-between gap-3 text-xs"><span className="flex items-center gap-1.5 text-muted-foreground"><ReceiptText className="size-3.5" />变动后余额</span><span className="font-medium tabular-nums">{formatWaveAmount(item.balanceAfter)} 浪花</span></div>
        </div>
      </div>
    </article>
  )
}
