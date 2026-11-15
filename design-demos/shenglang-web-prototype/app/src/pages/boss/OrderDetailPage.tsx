import { useState, type ReactNode } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  Clipboard,
  Clock3,
  Copy,
  Gamepad2,
  Gift,
  Headphones,
  Heart,
  MessageCircleMore,
  ReceiptText,
  ShieldCheck,
  Star,
  UserRound,
  UsersRound,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
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
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import { aftersaleOrder, bossOrders, paymentPendingOrder, type BossOrder } from '@/pages/boss/order-data'

export type OrderDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'pending' | 'grabbed' | 'in-service' | 'review' | 'refund' | 'gift' | 'payment-pending' | 'aftersale'

interface OrderDetailPageProps {
  scenario: OrderDetailScenario
  order: BossOrder
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onWallet: () => void
  onGift: () => void
  onSupport: (orderNo: string) => void
  onRetry: () => void
}

const applications = [
  { id: 1, name: '小澈', initial: '澈', level: '荣耀王者', ready: true, reason: '' },
  { id: 2, name: '阿屿', initial: '屿', level: '最强王者', ready: true, reason: '' },
  { id: 3, name: '桃乐丝', initial: '桃', level: '最强王者', ready: false, reason: '陪玩排期刚刚变化' },
]

export function OrderDetailPage({ scenario, order: selectedOrder, onLogin, onBack, onHome, onCompanions, onWallet, onGift, onSupport, onRetry }: OrderDetailPageProps) {
  const order = scenario === 'pending' ? bossOrders[0]
    : scenario === 'grabbed' ? bossOrders[1]
      : scenario === 'in-service' ? bossOrders[2]
        : scenario === 'review' ? bossOrders[3]
          : scenario === 'refund' ? bossOrders[4]
            : scenario === 'gift' ? bossOrders[5]
              : scenario === 'payment-pending' ? paymentPendingOrder
                : scenario === 'aftersale' ? aftersaleOrder : selectedOrder
  const [providerChoice, setProviderChoice] = useState<string | null>(null)
  const [assigned, setAssigned] = useState<string | null>(null)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [demoCancelled, setDemoCancelled] = useState(false)
  const [copied, setCopied] = useState(false)
  const [score, setScore] = useState(5)
  const [evaluationTarget, setEvaluationTarget] = useState('北夜')
  const [evaluation, setEvaluation] = useState('')
  const [anonymous, setAnonymous] = useState(false)
  const [evaluated, setEvaluated] = useState(false)
  const [aftersaleOpen, setAftersaleOpen] = useState(false)
  const [aftersaleReason, setAftersaleReason] = useState('')
  const [aftersaleError, setAftersaleError] = useState('')
  const [demoAftersale, setDemoAftersale] = useState(false)
  const [feature, setFeature] = useState<string | null>(null)

  const paymentUnconfirmed = !!order.paymentUnconfirmed
  const canSelect = order.status === 'PENDING' && !paymentUnconfirmed && !demoCancelled && !assigned
  const canCancel = order.status === 'PENDING' && !paymentUnconfirmed && !demoCancelled
  const canEvaluate = !!order.evaluate && !evaluated && !demoCancelled
  const canAfterSale = !order.gift && !paymentUnconfirmed && ['IN_SERVICE', 'COMPLETED'].includes(order.status) && !order.refund && !order.aftersale && !demoAftersale
  const canTip = order.status === 'COMPLETED' && !order.gift && !paymentUnconfirmed
  const displayStatus = demoCancelled ? '已取消' : order.statusText
  const displayProgress = demoCancelled ? '订单已取消（原型演示）' : order.progress
  const summaryDark = order.status !== 'CANCELLED' && !paymentUnconfirmed && !demoCancelled
  const original = order.gift ? order.amount : order.amount + 8
  const timeline = getTimeline(order, demoCancelled)

  async function copyOrder() {
    try {
      await navigator.clipboard.writeText(order.orderNo)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setFeature('手动复制订单编号')
    }
  }

  function submitAftersale() {
    if (Array.from(aftersaleReason.trim()).length < 2) {
      setAftersaleError('请填写至少 2 字问题说明')
      return
    }
    setAftersaleError('')
    setAftersaleOpen(false)
    setDemoAftersale(true)
  }

  function handleDockNavigate(key: string) {
    if (key === 'home') return onHome()
    if (key === 'companions') return onCompanions()
    if (key === 'orders') return onBack()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="orders" onNavigate={handleDockNavigate} />}>
      <main className="pb-44 md:pb-32">
        <header className="px-4 pb-6 pt-2 md:px-6 md:pt-4">
          <div className="flex items-center justify-between gap-3">
            <Button type="button" variant="ghost" className="h-11 rounded-[8px] px-2 text-sm" onClick={onBack}><ArrowLeft className="size-4" />订单列表</Button>
            <Badge variant="outline" className="rounded-md text-xs">示例订单</Badge>
          </div>
          <h1 className="mt-4 text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">订单详情</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">核对服务进度、费用明细与可执行操作。</p>
        </header>

        {scenario === 'guest' ? (
          <section className="px-4 md:px-6"><Card className="rounded-[12px] py-0 text-center"><CardContent className="p-6 md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><ReceiptText className="size-6" /></div><h2 className="mt-4 text-xl font-semibold">登录后查看订单详情</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">登录后查看服务进度、费用与订单操作。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}>微信快捷登录</Button></CardContent></Card></section>
        ) : scenario === 'loading' ? (
          <div className="space-y-4 px-4 md:grid md:grid-cols-2 md:space-y-0 md:px-6" aria-busy="true" aria-label="正在加载订单详情"><Skeleton className="h-56 w-full rounded-[12px] md:col-span-2" /><Skeleton className="h-64 w-full rounded-[12px]" /><Skeleton className="h-64 w-full rounded-[12px]" /></div>
        ) : scenario === 'error' ? (
          <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h2 className="mt-4 text-xl font-semibold">订单详情暂时没有加载成功</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试，或返回订单列表重新进入。</p><Button type="button" className="mt-6 h-11 w-full rounded-[8px] md:max-w-64" onClick={onRetry}>重新加载</Button><Button type="button" variant="ghost" className="mt-3 h-11 w-full rounded-[8px] md:max-w-64" onClick={onBack}>返回订单列表</Button></div></section>
        ) : (
          <div className="space-y-6 px-4 pb-6 md:grid md:grid-cols-[minmax(0,1fr)_320px] md:items-start md:gap-6 md:space-y-0 md:px-6 xl:grid-cols-[minmax(0,1fr)_360px]">
            <section className={cn('overflow-hidden rounded-[12px] border shadow-sm md:col-span-2', summaryDark ? 'border-slate-700 text-white' : 'bg-card', paymentUnconfirmed && 'border-warning/25 bg-warning/5')} style={summaryDark ? { backgroundColor: '#0F172A' } : undefined} aria-label="订单金额与状态摘要">
              <div className="p-5 md:p-6">
                <div className="md:flex md:items-start md:justify-between md:gap-8">
                  <div className="min-w-0">
                    <div className="flex items-center justify-between gap-3 md:justify-start">
                      <p className={cn('text-sm', summaryDark ? 'text-slate-300' : 'text-muted-foreground')}>{order.amountLabel || '本单实付'}</p>
                      <Badge variant="outline" className={cn('rounded-md', summaryDark ? 'border-white/20 bg-white/10 text-white hover:bg-white/10' : statusClass(order, demoCancelled))}>{displayStatus}</Badge>
                    </div>
                    <p className="mt-4 text-[34px] font-semibold leading-none tracking-[-0.03em] tabular-nums">{formatWaveAmount(order.amount)}<span className={cn('ml-2 text-sm font-normal', summaryDark ? 'text-slate-300' : 'text-muted-foreground')}>浪花</span></p>
                    <p className={cn('mt-4 text-sm leading-6', summaryDark ? 'text-slate-200' : 'text-muted-foreground')}>{displayProgress}</p>
                    {order.deadline && !demoCancelled && <p className={cn('mt-2 text-xs leading-5', summaryDark ? 'text-sky-200' : 'text-warning')}>{order.deadline}</p>}
                    {order.refund && <p className="mt-2 text-xs leading-5 text-success">{order.refund}</p>}
                    {assigned && <p role="status" className="mt-3 rounded-[8px] bg-white/10 p-3 text-xs leading-5 text-white">演示已选择 {assigned}，未修改真实订单</p>}
                    {demoCancelled && <p role="status" className="mt-3 text-xs text-success">演示订单已取消，未执行真实退款</p>}
                  </div>
                  <div className={cn('mt-5 border-t pt-4 md:mt-0 md:w-[320px] md:shrink-0 md:border-l md:border-t-0 md:pl-6 md:pt-0', summaryDark ? 'border-white/15' : 'border-border')}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0"><p className={cn('text-xs', summaryDark ? 'text-slate-300' : 'text-muted-foreground')}>订单编号</p><p className={cn('mt-1 truncate font-mono text-xs', summaryDark ? 'text-slate-100' : '')}>{order.orderNo}</p></div>
                      <Button type="button" variant="ghost" className={cn('h-11 shrink-0 rounded-[8px] px-3 text-xs', summaryDark ? 'bg-white/10 text-white hover:bg-white/15 hover:text-white' : 'bg-primary/10 text-primary')} onClick={copyOrder} aria-label={`复制订单编号 ${order.orderNo}`}>
                        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}{copied ? '已复制' : '复制'}
                      </Button>
                    </div>
                    <div className={cn('mt-3 flex items-center justify-between gap-3 text-xs', summaryDark ? 'text-slate-300' : 'text-muted-foreground')}><span>下单时间</span><span className="tabular-nums">{order.created}</span></div>
                  </div>
                </div>
              </div>
            </section>

            <div className="space-y-6">
              {canSelect && (
                <DetailSection title="挑选接单陪玩" icon={UsersRound} tone="primary">
                  <p className="text-sm leading-6 text-muted-foreground">按等级从高到低排列。确认后，所选陪玩按本单实付总价提供服务。</p>
                  <div className="mt-4 space-y-3">
                    {applications.map(item => (
                      <div key={item.id} className="rounded-[8px] border bg-muted/25 p-3 md:flex md:items-center md:gap-4 md:p-4">
                        <div className="flex min-w-0 flex-1 items-center gap-3"><div className="grid size-11 shrink-0 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{item.initial}</div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium">{item.name}</p><Badge variant="outline" className={cn('rounded-md text-xs', item.ready ? 'border-success/25 bg-success/10 text-success' : 'text-muted-foreground')}>{item.ready ? '可选择' : '暂不可选择'}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{item.level} · 一人服务</p></div></div>
                        {!item.ready && <p className="mt-3 text-xs leading-5 text-muted-foreground md:max-w-40">{item.reason}，请刷新或选择其他陪玩。</p>}
                        <Button type="button" variant={item.ready ? 'outline' : 'secondary'} className="mt-3 h-11 w-full shrink-0 rounded-[8px] md:mt-0 md:w-28" disabled={!item.ready} onClick={() => setProviderChoice(item.name)} aria-label={`选择${item.name}作为本次陪玩`}>选择 {item.name}</Button>
                      </div>
                    ))}
                  </div>
                </DetailSection>
              )}

              <DetailSection title={order.gift ? '赠礼记录' : '服务进度'} icon={Clock3} action={<Badge variant="secondary" className="rounded-md">{displayStatus}</Badge>}>
                <div>{timeline.map((item, index) => <div key={`${item.title}-${index}`} className="relative border-l pb-6 pl-5 last:border-transparent last:pb-0"><span className={cn('absolute -left-[5px] top-1.5 size-2.5 rounded-full bg-primary ring-4 ring-primary/10', index < timeline.length - 1 && 'bg-muted-foreground/45 ring-0')} /><p className={cn('text-sm leading-5', index === timeline.length - 1 && 'font-semibold text-primary')}>{item.title}</p><p className="mt-1 text-xs tabular-nums text-muted-foreground">{item.time}</p></div>)}</div>
                {order.evaluate && <Note label="双陪服务凭证">入队截图 · 2 / 2 已提交<br />小澈 · 已提交 / 北夜 · 已提交</Note>}
              </DetailSection>

              {canEvaluate && (
                <DetailSection title="评价本次服务" icon={Star} action={<Badge variant="secondary" className="rounded-md">{score} 分</Badge>}>
                  <p className="text-sm font-medium">选择评价对象</p>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <Button type="button" variant="outline" className="h-11 rounded-[8px]" disabled>小澈 · 已评价</Button>
                    <Button type="button" variant={evaluationTarget === '北夜' ? 'default' : 'outline'} className="h-11 rounded-[8px]" onClick={() => setEvaluationTarget('北夜')}>北夜</Button>
                  </div>
                  <div className="mt-4 flex items-center justify-between"><Label htmlFor="evaluation-score" className="text-sm">满意度</Label><span className="text-sm tabular-nums text-primary">{score} / 5</span></div>
                  <Slider id="evaluation-score" aria-label="服务满意度" min={1} max={5} step={1} value={[score]} onValueChange={value => setScore(value[0])} className="mt-2 h-11" />
                  <Label htmlFor="order-evaluation" className="text-sm">服务评价</Label>
                  <Textarea id="order-evaluation" value={evaluation} onChange={event => setEvaluation(event.target.value)} maxLength={500} placeholder="分享你的真实体验（选填）" className="mt-2 min-h-24 rounded-[8px] text-sm leading-6" />
                  <label htmlFor="order-anonymous" className="mt-2 flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm">匿名评价<Switch id="order-anonymous" checked={anonymous} onCheckedChange={setAnonymous} /></label>
                  <Button type="button" className="mt-3 h-11 w-full rounded-[8px]" onClick={() => setEvaluated(true)}>提交评价</Button>
                </DetailSection>
              )}

              {(order.evaluate || evaluated) && <DetailSection title="我的评价" icon={MessageCircleMore}><div className="space-y-4"><ReviewEntry name="小澈" score={5} content="沟通很舒服，开局前确认了位置和节奏。" reply="谢谢认可，下次继续一起开心上分。" />{evaluated && <ReviewEntry name={evaluationTarget} score={score} content={evaluation.trim() || '用户未填写文字评价'} anonymous={anonymous} />}</div></DetailSection>}
            </div>

            <aside className="space-y-6" aria-label="订单资料与售后">
              <DetailSection title="服务与费用" icon={ReceiptText}>
                <p className="text-base font-semibold leading-6">{order.service}</p>
                <p className="mt-1 text-xs text-muted-foreground">{order.game}</p>
                <div className="mt-3">
                  {order.gift && <DetailRow label="赠礼来源" value="直接赠送" />}
                  <DetailRow label="服务原价" value={`${formatWaveAmount(original)} 浪花`} />
                  {!order.gift && <><DetailRow label="会员优惠" value="-5 浪花" accent /><DetailRow label="活动优惠" value="-3 浪花" accent /></>}
                  <DetailRow label="购买数量" value={`${order.quantity} ${order.unit}`} />
                  <DetailRow label={order.amountLabel || '实付金额'} value={`${formatWaveAmount(order.amount)} 浪花`} />
                </div>
                {order.gift && <Note label="赠礼备注">感谢这一局的认真陪伴。</Note>}
              </DetailSection>

              <DetailSection title={order.gift ? '赠礼对象' : '陪玩安排'} icon={Headphones}>
                <DetailRow label={order.gift ? '赠礼对象' : '陪玩'} value={assigned || order.provider} />
                {!order.gift && <DetailRow label="陪玩选择" value="不限" />}
              </DetailSection>

              {!order.gift && <DetailSection title="游戏信息" icon={Gamepad2}><DetailRow label="游戏大区" value="微信区" /><DetailRow label="游戏昵称" value="海浪收藏家" /><DetailRow label="游戏 UID" value="83921044" /><Note label="服务备注">希望轻松沟通，开局前确认位置。</Note></DetailSection>}

              {canTip && <DetailSection title="把欣赏送给 TA" icon={Heart}><p className="text-sm leading-6 text-muted-foreground">感谢这一局的陪伴，挑选一份心意。</p><Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={onGift}><Gift className="size-4" />挑选礼物</Button></DetailSection>}

              {order.refund && <DetailSection title="退款已入账" icon={WalletCards}><p className="text-sm leading-6 text-muted-foreground">已退回钱包：49 浪花<br />订单剩余金额：0 浪花</p><Note label="退款 #901 · 49 浪花">2026-11-02 19:41<br />接单时限已到，订单自动取消</Note></DetailSection>}

              {(canAfterSale || order.aftersale || demoAftersale) && <DetailSection title="售后服务" icon={ShieldCheck} action={(order.aftersale || demoAftersale) ? <Badge variant="outline" className="rounded-md border-warning/25 bg-warning/10 text-warning">处理中</Badge> : undefined}>
                {(order.aftersale || demoAftersale) && <><p className="text-sm leading-6 text-muted-foreground">售后处理中，实销暂不确认；处理结束后仍需满足确认条件。</p><Note label="申请说明">{order.aftersale ? '服务时间与约定不一致，请协助核对。' : aftersaleReason.trim()}</Note></>}
                {canAfterSale && <Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={() => setAftersaleOpen(true)}>提交售后问题</Button>}
              </DetailSection>}
            </aside>
          </div>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && <div className="absolute inset-x-0 bottom-[84px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:bottom-0 md:px-6"><div className="mx-auto flex max-w-[1120px] items-center justify-between gap-4"><div className="hidden md:block"><p className="text-xs text-muted-foreground">当前状态</p><p className="mt-1 text-sm font-medium">{displayStatus}</p></div><div className={cn('grid flex-1 gap-3 md:flex md:flex-initial', (canCancel || order.refund) ? 'grid-cols-2' : 'grid-cols-1')}>
        {canCancel && <Button type="button" variant="outline" className="h-12 rounded-[8px] text-destructive md:w-32" onClick={() => setCancelOpen(true)}>取消订单</Button>}
        {order.refund && <Button type="button" variant="outline" className="h-12 rounded-[8px] md:w-32" onClick={onWallet}>退款流水</Button>}
        <Button type="button" className="h-12 rounded-[8px] md:w-44" onClick={() => onSupport(order.orderNo)}><MessageCircleMore className="size-4" />联系订单客服</Button>
      </div></div></div>}

      <Dialog open={providerChoice !== null} onOpenChange={open => !open && setProviderChoice(null)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><UserRound className="size-5" /></div><DialogTitle className="text-left text-xl">选择本次陪玩</DialogTitle><DialogDescription className="text-left text-sm leading-6">{providerChoice} · 荣耀王者，一人服务。按本单已付总金额 {formatWaveAmount(order.amount)} 浪花提供服务。</DialogDescription></DialogHeader><div className="rounded-[8px] bg-muted/55 p-3 text-sm leading-6 text-muted-foreground">这是原型演示，确认后只更新当前页面状态，不会修改真实订单。</div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]">再想想</Button></DialogClose><Button type="button" className="h-11 rounded-[8px]" onClick={() => { setAssigned(providerChoice); setProviderChoice(null) }}>确认选择</Button></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">确认取消订单？</DialogTitle><DialogDescription className="text-left text-sm leading-6">订单仍待接单时可以取消。{order.amount === 0 ? '本单实付为 0，无需退回余额。' : `已支付的 ${formatWaveAmount(order.amount)} 浪花将退回余额。`}已核销的券取消后不返还。</DialogDescription></DialogHeader><div><Label htmlFor="cancel-reason" className="text-sm">取消原因（选填）</Label><Textarea id="cancel-reason" value={cancelReason} onChange={event => setCancelReason(event.target.value)} maxLength={255} placeholder="可填写取消原因" className="mt-2 min-h-20 rounded-[8px] text-sm leading-6" /></div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]">保留订单</Button></DialogClose><Button type="button" variant="destructive" className="h-11 rounded-[8px]" onClick={() => { setDemoCancelled(true); setCancelOpen(false) }}>确认取消</Button></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={aftersaleOpen} onOpenChange={setAftersaleOpen}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><DialogTitle className="text-left text-xl">提交订单售后</DialogTitle><DialogDescription className="text-left text-sm leading-6">请说明遇到的问题，提交后可在订单中查看处理进度。</DialogDescription></DialogHeader><div><Label htmlFor="aftersale-reason" className="text-sm">问题说明</Label><Textarea id="aftersale-reason" value={aftersaleReason} onChange={event => setAftersaleReason(event.target.value)} maxLength={500} placeholder="请填写至少 2 字问题说明" className="mt-2 min-h-24 rounded-[8px] text-sm leading-6" />{aftersaleError && <p role="alert" className="mt-2 text-xs text-destructive">{aftersaleError}</p>}</div><p className="text-xs leading-5 text-muted-foreground">原型仅展示提交反馈，不会创建真实售后申请。</p><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]">取消</Button></DialogClose><Button type="button" className="h-11 rounded-[8px]" onClick={submitAftersale}>提交问题</Button></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={feature !== null} onOpenChange={open => !open && setFeature(null)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><Clipboard className="size-5" /></div><DialogTitle className="text-left text-xl">手动复制订单编号</DialogTitle><DialogDescription className="select-text text-left font-mono text-sm leading-6">{order.orderNo}</DialogDescription></DialogHeader><DialogFooter><DialogClose asChild><Button type="button" className="h-11 w-full rounded-[8px]">返回订单详情</Button></DialogClose></DialogFooter></DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}

function DetailSection({ title, icon: Icon, action, tone, children }: { title: string; icon: LucideIcon; action?: ReactNode; tone?: 'primary'; children: ReactNode }) {
  return <section className={cn('rounded-[12px] border bg-card p-4 shadow-sm md:p-5', tone === 'primary' && 'border-primary/20 bg-primary/[0.025]')}><div className="mb-4 flex items-center justify-between gap-3 border-b pb-3"><div className="flex items-center gap-2"><Icon className="size-4 text-primary" /><h2 className="text-base font-semibold">{title}</h2></div>{action}</div>{children}</section>
}

function DetailRow({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return <div className="flex min-h-11 items-center justify-between gap-3 border-b py-2 text-sm last:border-0"><span className="text-muted-foreground">{label}</span><span className={cn('max-w-[70%] text-right tabular-nums', accent && 'text-success')}>{value}</span></div>
}

function Note({ label, children }: { label: string; children: ReactNode }) {
  return <div className="mt-4 rounded-[8px] bg-muted/50 p-3 text-xs leading-5"><p className="text-muted-foreground">{label}</p><div className="mt-1">{children}</div></div>
}

function ReviewEntry({ name, score, content, reply, anonymous = false }: { name: string; score: number; content: string; reply?: string; anonymous?: boolean }) {
  return <div className="border-b pb-4 last:border-0 last:pb-0"><div className="flex items-center justify-between"><p className="text-sm font-medium">{name}{anonymous ? ' · 匿名' : ''}</p><Badge variant="secondary" className="rounded-md">{score} 分</Badge></div><p className="mt-2 text-sm leading-6">{content}</p>{reply && <p className="mt-3 rounded-[8px] bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"><span className="text-foreground">陪玩回复：</span>{reply}</p>}</div>
}

function getTimeline(order: BossOrder, demoCancelled: boolean) {
  if (order.paymentUnconfirmed) return [{ title: '订单已创建', time: order.created }]
  if (order.gift) return [{ title: '礼物已送达', time: order.created }]
  const items = [{ title: '下单成功', time: order.created }]
  if (['GRABBED', 'IN_SERVICE', 'COMPLETED'].includes(order.status)) items.push({ title: '陪玩已接单', time: '2026-11-03 17:26' })
  if (['IN_SERVICE', 'COMPLETED'].includes(order.status)) items.push({ title: '服务已开始', time: '2026-11-03 16:00' })
  if (order.status === 'COMPLETED') items.push({ title: '服务已完成', time: '2026-11-02 22:06' })
  if (order.status === 'CANCELLED') items.push({ title: '订单已取消', time: '2026-11-02 19:40' }, { title: '退款已入账', time: '2026-11-02 19:41' })
  if (demoCancelled) items.push({ title: '订单已取消（演示）', time: '刚刚' })
  return items
}

function statusClass(order: BossOrder, demoCancelled: boolean) {
  if (demoCancelled || order.status === 'CANCELLED') return 'border-border bg-muted/50 text-muted-foreground'
  if (order.paymentUnconfirmed || order.status === 'PENDING') return 'border-warning/25 bg-warning/10 text-warning'
  if (order.status === 'COMPLETED') return 'border-success/25 bg-success/10 text-success'
  return 'border-primary/25 bg-primary/10 text-primary'
}
