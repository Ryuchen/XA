import { useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  LoaderCircle,
  LogIn,
  RefreshCw,
  ShieldCheck,
  Ticket,
  TicketCheck,
  TicketX,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { FilterBar } from '@/components/prototype/FilterBar'
import { PageHeader } from '@/components/prototype/PageHeader'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export type CouponsScenario = 'default' | 'available' | 'guest' | 'loading' | 'error' | 'empty' | 'claiming' | 'claim-uncertain' | 'use-error' | 'used' | 'expired' | 'disabled'
type CouponStatus = 'UNUSED' | 'USED' | 'EXPIRED' | 'DISABLED'
type CouponTab = 'available' | 'mine'

interface Coupon {
  id: number
  name: string
  amount: string
  unit: string
  threshold: string
  validity: string
  cap?: string
  status: CouponStatus
  serviceCount?: number
  claimed?: boolean
  claimable?: boolean
  claimLabel?: string
}

interface CouponsPageProps {
  scenario: CouponsScenario
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onUse: (couponName: string) => void
  onRetry: () => void
}

const mineCoupons: Coupon[] = [
  { id: 1, name: '新老板体验礼', amount: '20', unit: '浪花', threshold: '满 68 浪花可用', validity: '2026-11-10 23:59 到期', status: 'UNUSED' },
  { id: 2, name: '周末畅玩折扣券', amount: '8.5', unit: '折', threshold: '全部服务 · 与 VIP 折扣取优，不叠加', validity: '2026-11-08 23:59 到期', cap: '每张最高优惠 30 浪花', status: 'UNUSED' },
  { id: 3, name: '王者双陪体验资格', amount: '套餐', unit: '资格券', threshold: '限指定套餐 · 2 款 · 限 1 份', validity: '领取当日有效', serviceCount: 2, status: 'UNUSED' },
]
const availableCoupons: Coupon[] = [
  { id: 11, name: '海浪见面礼', amount: '12', unit: '浪花', threshold: '无门槛', validity: '领取后 7 日内有效', status: 'UNUSED', claimable: true },
  { id: 12, name: '陪玩折扣周', amount: '9', unit: '折', threshold: '指定陪玩服务 · 限 2 份', validity: '2026-11-15 23:59 到期', cap: '每张最高优惠 20 浪花', status: 'UNUSED', claimed: true },
  { id: 13, name: '体验单免单资格', amount: '赠送', unit: '服务券', threshold: '68 元体验单 · 限 1 份', validity: '领取当日有效', status: 'UNUSED', claimLabel: '资格已使用' },
  { id: 14, name: '会员加赠券', amount: '30', unit: '浪花', threshold: '满 198 浪花可用', validity: '2026-11-30 23:59 到期', status: 'UNUSED', claimLabel: '已领完' },
]
const historyCoupons: Coupon[] = [
  { id: 21, name: '首单体验优惠', amount: '15', unit: '浪花', threshold: '满 68 浪花可用', validity: '2026-10-28 已使用', status: 'USED' },
  { id: 22, name: '周末畅玩折扣券', amount: '8.8', unit: '折', threshold: '全部服务 · 与 VIP 折扣取优', validity: '2026-10-31 已到期', status: 'EXPIRED' },
  { id: 23, name: '活动补偿券', amount: '10', unit: '浪花', threshold: '无门槛', validity: '2026-10-20 已停用', status: 'DISABLED' },
]
const filters: { value: CouponStatus; label: string }[] = [
  { value: 'UNUSED', label: '待使用' },
  { value: 'USED', label: '已使用' },
  { value: 'EXPIRED', label: '已过期' },
  { value: 'DISABLED', label: '已停用' },
]

export function CouponsPage({ scenario, onLogin, onBack, onHome, onCompanions, onOrders, onUse, onRetry }: CouponsPageProps) {
  const initialTab: CouponTab = ['available', 'claiming', 'claim-uncertain'].includes(scenario) ? 'available' : 'mine'
  const initialStatus: CouponStatus = scenario === 'used' ? 'USED' : scenario === 'expired' ? 'EXPIRED' : scenario === 'disabled' ? 'DISABLED' : 'UNUSED'
  const [tab, setTab] = useState<CouponTab>(initialTab)
  const [status, setStatus] = useState<CouponStatus>(initialStatus)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [claiming, setClaiming] = useState(scenario === 'claiming' ? 11 : 0)
  const [using, setUsing] = useState(0)
  const [claimed, setClaimed] = useState<number[]>([])
  const [notice, setNotice] = useState(scenario === 'claim-uncertain' ? '领取结果未确认，请先查看我的券，避免重复领取。' : scenario === 'use-error' ? '优惠券状态已变化，请重新查看。' : '')

  const claimedCoupons = availableCoupons.filter(coupon => claimed.includes(coupon.id)).map(coupon => ({ ...coupon, validity: '2026-11-11 23:59 到期' }))
  const items = scenario === 'empty' ? [] : tab === 'available' ? availableCoupons : ['USED', 'EXPIRED', 'DISABLED'].includes(status) ? historyCoupons.filter(item => item.status === status) : [...claimedCoupons, ...mineCoupons]
  const busy = claiming > 0 || using > 0

  function changeTab(next: CouponTab) {
    setTab(next)
    setExpandedId(null)
    setNotice('')
  }

  function viewMine() {
    setTab('mine')
    setStatus('UNUSED')
    setExpandedId(null)
    setNotice('')
  }

  function claim(coupon: Coupon) {
    if (busy || coupon.claimed || coupon.claimLabel || !coupon.claimable) return
    setClaiming(coupon.id)
    setNotice('')
    window.setTimeout(() => {
      setClaimed(current => current.includes(coupon.id) ? current : [...current, coupon.id])
      setClaiming(0)
      setNotice('领取成功，可在我的券中查看。')
    }, 500)
  }

  function redeemCoupon(coupon: Coupon) {
    if (busy || coupon.status !== 'UNUSED') return
    setUsing(coupon.id)
    setNotice('')
    window.setTimeout(() => {
      setUsing(0)
      if (scenario === 'use-error') {
        setNotice('优惠券状态已变化，请重新查看。')
        return
      }
      onUse(coupon.name)
    }, 400)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onBack()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className="pb-28 md:pb-12">
        <div className="flex items-center justify-between px-4 py-2 md:px-6">
          <Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-sm" onClick={onBack}><ArrowLeft className="size-4" />我的</Button>
          <Badge variant="outline" className="rounded-md text-xs">优惠券 · 示例</Badge>
        </div>

        <PageHeader className="pb-5" title="优惠券" description="先了解使用条件，再把合适的优惠带进结算。">
          <FilterBar className="mt-5" ariaLabel="优惠券视图" items={[{ value: 'available', label: '领券中心' }, { value: 'mine', label: '我的券' }]} value={tab} onChange={changeTab} columns={2} />
          {tab === 'mine' && <FilterBar className="mt-3" ariaLabel="按使用状态筛选" items={filters} value={status} onChange={value => { setStatus(value); setExpandedId(null); setNotice('') }} columns={4} />}
        </PageHeader>

        {scenario === 'guest' ? (
          <section className="px-4 pt-2 md:px-6"><div className="rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><Ticket className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后管理优惠券</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">领取记录与使用状态会同步到当前账号。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></div></section>
        ) : scenario === 'loading' ? (
          <div className="grid gap-3 px-4 md:px-6 xl:grid-cols-2" aria-busy="true" aria-label="正在加载优惠券"><Skeleton className="h-48 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-2 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">优惠券暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，加载失败不会改变领取或使用状态。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px]" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
        ) : (
          <div className="px-4 pb-6 md:px-6">
            {notice && <div role="status" className={cn('mb-4 rounded-[8px] border p-3 text-sm leading-6', notice.includes('成功') ? 'border-success/25 bg-success/5 text-success' : 'border-warning/25 bg-warning/5 text-warning')}><div className="flex items-start gap-2">{notice.includes('成功') ? <Check className="mt-1 size-4 shrink-0" /> : <AlertCircle className="mt-1 size-4 shrink-0" />}<p>{notice}</p></div>{scenario === 'claim-uncertain' && <Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px] text-warning" onClick={viewMine}>查看我的券<ArrowRight className="size-4" /></Button>}</div>}

            {items.length === 0 ? <section className="rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-muted/50 text-muted-foreground">{tab === 'available' ? <TicketX className="size-6" /> : <Ticket className="size-6" />}</div><h2 className="mt-4 text-xl font-semibold">{tab === 'available' ? '暂无新券可领取' : `暂无${filters.find(item => item.value === status)?.label}优惠券`}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{tab === 'available' ? '已领取的券可在「我的券」中查看。' : status === 'UNUSED' ? '去领券中心看看，或选择服务后领取适用优惠。' : '切换其他状态，查看完整优惠券记录。'}</p>{tab === 'mine' && status === 'UNUSED' && <Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={() => onUse('适用优惠')}>去选择服务<ArrowRight className="size-4" /></Button>}</section> : <section className="grid items-start gap-3 xl:grid-cols-2" aria-label={tab === 'available' ? '可领取优惠券' : '我的优惠券'}>{items.map(coupon => <CouponCard key={coupon.id} coupon={coupon} tab={tab} expanded={expandedId === coupon.id} claiming={claiming === coupon.id} using={using === coupon.id} anyBusy={busy} locallyClaimed={claimed.includes(coupon.id)} onToggle={() => setExpandedId(expandedId === coupon.id ? null : coupon.id)} onClaim={() => claim(coupon)} onUse={() => redeemCoupon(coupon)} />)}</section>}

            <div className="mt-4 flex gap-2 rounded-[8px] bg-muted/45 p-3 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" /><p>具体适用范围与优惠金额以结算页实时核验为准；优惠券与 VIP 折扣按规则取优，不重复叠加。</p></div>
          </div>
        )}
      </main>
    </PhoneFrame>
  )
}

function CouponCard({ coupon, tab, expanded, claiming, using, anyBusy, locallyClaimed, onToggle, onClaim, onUse }: { coupon: Coupon; tab: CouponTab; expanded: boolean; claiming: boolean; using: boolean; anyBusy: boolean; locallyClaimed: boolean; onToggle: () => void; onClaim: () => void; onUse: () => void }) {
  const inactive = coupon.status !== 'UNUSED'
  const statusLabel = coupon.status === 'USED' ? '已使用' : coupon.status === 'EXPIRED' ? '已过期' : coupon.status === 'DISABLED' ? '已停用' : ''
  const actionLabel = claiming ? '领取中' : locallyClaimed || coupon.claimed ? '已领取' : coupon.claimLabel || '立即领取'
  const detailsId = `coupon-details-${coupon.id}`

  return (
    <article className={cn('overflow-hidden rounded-[12px] border bg-card shadow-sm', inactive && 'bg-muted/20 shadow-none')}>
      <div className="flex">
        <div className={cn('flex w-[96px] shrink-0 flex-col items-center justify-center p-4 text-center text-primary', inactive && 'text-muted-foreground')}>
          <strong className={cn('break-words text-[32px] leading-none tracking-[-0.04em] tabular-nums', coupon.amount.length > 3 && 'text-xl')}>{coupon.amount}</strong>
          <span className="mt-2 text-xs">{coupon.unit}</span>
        </div>
        <div className="min-w-0 flex-1 border-l border-dashed p-4">
          <div className="flex items-start justify-between gap-2"><h2 className="break-words text-base font-semibold leading-6">{coupon.name}</h2>{inactive && <Badge variant="secondary" className="shrink-0 rounded-md text-xs">{statusLabel}</Badge>}</div>
          <p className="mt-2 text-sm font-medium leading-5">{coupon.threshold}</p>
          <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-muted-foreground"><Clock3 className="mt-0.5 size-3.5 shrink-0" />{coupon.validity}</p>
          {coupon.cap && <p className="mt-1 text-xs leading-5 text-muted-foreground">{coupon.cap} · 与 VIP 折扣取优</p>}
          <div className="mt-3 flex min-h-11 flex-wrap items-center justify-between gap-2">
            <button type="button" className="-ml-1 flex min-h-11 flex-1 items-center rounded-[4px] px-1 text-left text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={onToggle} aria-expanded={expanded} aria-controls={detailsId}>{expanded ? '收起说明' : '使用说明'}{expanded ? <ChevronUp className="ml-1 size-4" /> : <ChevronDown className="ml-1 size-4" />}</button>
            {tab === 'available' ? <Button type="button" variant={locallyClaimed || coupon.claimed ? 'secondary' : 'outline'} className="h-11 shrink-0 rounded-[8px] px-3 text-sm" disabled={anyBusy || locallyClaimed || coupon.claimed || !!coupon.claimLabel} onClick={onClaim}>{claiming ? <LoaderCircle className="size-4 animate-spin" /> : locallyClaimed || coupon.claimed ? <Check className="size-4" /> : <TicketCheck className="size-4" />}{actionLabel}</Button> : coupon.status === 'UNUSED' ? <Button type="button" variant="outline" className="h-11 shrink-0 rounded-[8px] px-3 text-sm" disabled={anyBusy} onClick={onUse}>{using && <LoaderCircle className="size-4 animate-spin" />}{using ? '正在核对' : '去选服务'}</Button> : null}
          </div>
        </div>
      </div>
      {expanded && <div id={detailsId} className="border-t bg-muted/20 px-4 py-4 text-sm leading-6 text-muted-foreground"><p className="font-medium text-foreground">使用说明</p><p className="mt-2">{coupon.threshold}；{coupon.validity}。</p><p className="mt-2">{coupon.serviceCount ? `仅限指定的 ${coupon.serviceCount} 款套餐，提交订单时会再次核验适用范围。` : '具体适用范围以结算页核验结果为准；如暂不可领取，请查看按钮状态或联系客服。'}</p><p className="mt-2">优惠券与 VIP 折扣按规则取优，不重复叠加。</p></div>}
    </article>
  )
}
