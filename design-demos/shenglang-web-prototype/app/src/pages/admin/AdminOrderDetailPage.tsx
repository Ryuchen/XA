import { useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Banknote,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Copy,
  Gamepad2,
  Info,
  RotateCcw,
  ShieldCheck,
  UserRound,
  UsersRound,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminOrderDetailScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { adminOrderStatusLabel, adminPaymentStatusLabel, type AdminOrder, type AdminOrderStatus, type AdminPaymentStatus } from '@/pages/admin/admin-order-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminOrderDetailPageProps {
  order: AdminOrder
  scenario: AdminOrderDetailScenario
  onScenarioChange: (scenario: AdminOrderDetailScenario) => void
  onBack: () => void
}

function DetailRow({ label, children, note }: { label: string; children: React.ReactNode; note?: string }) {
  return <div className="grid gap-1 border-b py-3 last:border-0 sm:grid-cols-[128px_minmax(0,1fr)] sm:gap-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="min-w-0 text-sm leading-6">{children}{note && <span className="mt-0.5 block text-[11px] text-muted-foreground">{note}</span>}</dd></div>
}

export function AdminOrderDetailPage({ order, scenario, onScenarioChange, onBack }: AdminOrderDetailPageProps) {
  const initialRefunded = order.refundedAmount || (scenario === 'refunded' ? Math.round(order.amount * 0.25) : 0)
  const [status, setStatus] = useState<AdminOrderStatus>(scenario === 'pending' ? 'PENDING' : scenario === 'in-service' ? 'IN_SERVICE' : order.status)
  const [payment, setPayment] = useState<AdminPaymentStatus>(scenario === 'refunded' && order.paymentStatus === 'PAID' ? 'PARTIALLY_REFUNDED' : order.paymentStatus)
  const [providers, setProviders] = useState(order.providerNames)
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignSearch, setAssignSearch] = useState('')
  const [assignProvider, setAssignProvider] = useState('')
  const [assignChecked, setAssignChecked] = useState(false)
  const [action, setAction] = useState<'start' | 'complete' | 'cancel' | null>(null)
  const [cancelReason, setCancelReason] = useState('')
  const [refundOpen, setRefundOpen] = useState(false)
  const [refundPreview, setRefundPreview] = useState(false)
  const [refundAmount, setRefundAmount] = useState(String(Math.max(order.amount - initialRefunded, 0)))
  const [refundReason, setRefundReason] = useState('')
  const [refundError, setRefundError] = useState('')
  const [localRefunded, setLocalRefunded] = useState(initialRefunded)
  const [aftersaleResolveOpen, setAftersaleResolveOpen] = useState(false)
  const [resolution, setResolution] = useState('')
  const [aftersaleResolved, setAftersaleResolved] = useState(false)
  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState('')

  const locked = scenario === 'refresh-error'
  const hasAftersale = !aftersaleResolved && (scenario === 'aftersale' || !!order.aftersale)
  const ticket = order.aftersale || { id: 209, reason: '老板反馈服务体验与约定不一致，请客服核对履约记录。', createdAt: '2026-11-04 16:36' }
  const refunded = localRefunded
  const remaining = Math.max(order.amount - refunded, 0)
  const candidates = ['小鹿', '奈奈', '阿澈', '可可'].filter(name => name.includes(assignSearch.trim()))

  function confirmAction() {
    if (action === 'start') { setStatus('IN_SERVICE'); setFeedback('订单已在原型中开始服务，状态流水已追加一条本地记录。') }
    if (action === 'complete') { setStatus('COMPLETED'); setFeedback('订单已在原型中标记完成，未触发真实结算。') }
    if (action === 'cancel') {
      setStatus('CANCELLED')
      if (payment === 'PAID') { setPayment('REFUNDED'); setLocalRefunded(order.amount) }
      setFeedback('订单已在原型中取消，未执行真实退款。')
    }
    setAction(null)
  }

  function previewRefund() {
    const amount = Number(refundAmount)
    if (!Number.isInteger(amount) || amount <= 0 || amount > remaining) { setRefundError(`退款金额须为正整数且不超过剩余可退 ${formatWaveAmount(remaining)} 浪花`); setRefundPreview(false); return }
    if (refundReason.trim().length < 2) { setRefundError('请填写至少 2 个字的退款原因或处理说明'); setRefundPreview(false); return }
    setRefundError(''); setRefundPreview(true)
  }

  function confirmRefund() {
    const amount = Number(refundAmount)
    const total = localRefunded + amount
    setLocalRefunded(total)
    setPayment(total >= order.amount ? 'REFUNDED' : 'PARTIALLY_REFUNDED')
    setRefundOpen(false); setRefundPreview(false); setAftersaleResolved(true)
    setFeedback(`退款复核原型已完成：本次 ${formatWaveAmount(amount)} 浪花，未操作真实钱包或收益。`)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="订单详情加载中"><Skeleton className="h-36 rounded-xl" /><div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]"><Skeleton className="h-[720px] rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">订单详情加载失败</CardTitle><CardDescription className="mt-2">请重新加载，或返回订单列表核对订单状态。</CardDescription><div className="mt-6 flex gap-2"><Button variant="outline" onClick={onBack}><ArrowLeft className="size-4" />返回列表</Button><Button onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></div></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="flex flex-wrap items-center gap-3">
      <Button variant="ghost" onClick={onBack}><ArrowLeft className="size-4" />返回订单列表</Button>
      <div className="ml-auto flex flex-wrap gap-2">{status === 'PENDING' && payment === 'PAID' && <Button disabled={locked} onClick={() => setAssignOpen(true)}><UsersRound className="size-4" />指派陪玩</Button>}{status === 'GRABBED' && <Button disabled={locked} onClick={() => setAction('start')}>开始服务<ArrowRight className="size-4" /></Button>}{status === 'IN_SERVICE' && <Button disabled={locked} onClick={() => setAction('complete')}><Check className="size-4" />完成订单</Button>}</div>
    </section>

    {locked && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>订单更新失败，当前显示上次结果</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请重新加载后再处理订单，派单与资金操作已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}

    <Card className="overflow-hidden border-[#10285b] bg-[#10285b] text-white">
      <CardContent className="grid gap-6 py-6 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <div><div className="flex flex-wrap items-center gap-2"><Badge className="border-white/20 bg-white/10 text-white hover:bg-white/10">{adminOrderStatusLabel[status]}</Badge><Badge className="border-white/20 bg-white/10 text-white hover:bg-white/10">{adminPaymentStatusLabel[payment]}</Badge>{hasAftersale && <Badge className="border-amber-300/30 bg-amber-300/10 text-amber-100 hover:bg-amber-300/10">售后处理中</Badge>}</div><div className="mt-4 flex items-center gap-2"><h1 className="font-mono text-xl font-semibold tracking-tight sm:text-2xl">{order.orderNo}</h1><Button variant="ghost" size="icon" className="size-9 text-blue-100 hover:bg-white/10 hover:text-white" onClick={() => setCopied(true)} aria-label="复制订单号">{copied ? <Check className="size-4" /> : <Copy className="size-4" />}</Button></div><p className="mt-2 text-sm text-blue-100/75">{order.serviceName} · {order.customerName}</p></div>
        <div className="md:text-right"><p className="text-xs text-blue-100/70">服务成交金额</p><p className="mt-1 text-3xl font-semibold tabular-nums">{formatWaveAmount(order.amount)}<small className="ml-2 text-sm font-normal text-blue-100/75">浪花</small></p><p className="mt-1 text-xs text-blue-100/70">已计优惠，未扣退款</p></div>
      </CardContent>
    </Card>

    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_360px]">
      <div className="flex min-w-0 flex-col gap-4">
        {order.teamSize === 2 && (status === 'GRABBED' || status === 'IN_SERVICE') && <Alert><Info className="size-4" /><AlertTitle>双陪履约需要分别核对</AlertTitle><AlertDescription>请与双方核对入队、结单凭证和服务进度；两位陪玩分别提交截图后，再由客服审核。</AlertDescription></Alert>}
        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><UserRound className="size-4 text-primary" />订单信息</CardTitle><CardDescription>身份、接单路径与服务快照</CardDescription></CardHeader><CardContent><dl>
          <DetailRow label="老板">{order.customerName}<span className="ml-2 text-xs text-muted-foreground">{order.customerUsername}</span></DetailRow>
          <DetailRow label="下单手机号">{order.customerPhone}</DetailRow>
          <DetailRow label="陪玩选择">{order.gender} · {order.teamSize === 2 ? '2 人（双陪）' : '1 人（单陪）'}</DetailRow>
          <DetailRow label="陪玩" note="编号与账号以读取时资料为准，不是下单时快照">{providers.length ? providers.join(' / ') : '等待安排'}</DetailRow>
          <DetailRow label="接单路径">{order.route === 'STAFF' ? '客服派单' : '公开接单'}{status === 'PENDING' && order.route === 'STAFF' && <Badge variant="outline" className="ml-2">客服待派</Badge>}</DetailRow>
          {order.deadline && status === 'PENDING' && <DetailRow label="接单截止时间">{order.deadline}<span className="ml-2 text-xs text-warning-foreground">到期后不能派单</span></DetailRow>}
          <DetailRow label="服务">{order.serviceName}</DetailRow>
          <DetailRow label="玩法规格">标准陪玩 · 微信区 · 普通安全保障</DetailRow>
          <DetailRow label="负责客服">{order.support}</DetailRow>
        </dl></CardContent></Card>

        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Banknote className="size-4 text-primary" />金额与收益</CardTitle><CardDescription>成交金额、预计收益与结算状态分别展示</CardDescription></CardHeader><CardContent><dl>
          <DetailRow label="单价 / 局数">{formatWaveAmount(order.unitPrice)} 浪花 × {order.rounds} 局</DetailRow>
          {order.discount > 0 && <DetailRow label="优惠">原价 {formatWaveAmount(order.originalAmount)} 浪花 · 优惠 -{formatWaveAmount(order.discount)} 浪花</DetailRow>}
          <DetailRow label="服务成交金额"><strong>{formatWaveAmount(order.amount)} 浪花</strong></DetailRow>
          <DetailRow label="平台抽成率">{order.commissionRate}%</DetailRow>
          <DetailRow label={payment.includes('REFUND') ? '原预计陪玩收益' : order.settlementStatus === 'SETTLED' ? '陪玩实得' : '预计陪玩收益'}>{formatWaveAmount(order.providerIncome)} 浪花{order.teamSize === 2 && <span className="ml-2 text-xs text-muted-foreground">双陪合计</span>}</DetailRow>
          <DetailRow label="店铺留存快照">{formatWaveAmount(order.shopIncome)} 浪花</DetailRow>
          <DetailRow label="推荐人 / 分佣">{order.inviter} · {formatWaveAmount(order.inviterCommission)} 浪花</DetailRow>
          <DetailRow label="收益状态">{order.settlementStatus === 'SETTLED' ? '已结算' : order.settlementStatus === 'REVIEW' ? '收益待核对' : '待结算'}</DetailRow>
        </dl><p className="mt-3 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">订单净额和店铺留存均不等于平台利润；退款后需以实际追回与冲正流水为准。</p></CardContent></Card>

        {(refunded > 0 || scenario === 'refunded') && <Card><CardHeader><CardTitle className="text-base">实际退款与追回</CardTitle><CardDescription>按实际退款记录展示，不使用预计金额替代</CardDescription></CardHeader><CardContent><dl><DetailRow label="原实付">{formatWaveAmount(order.amount)} 浪花</DetailRow><DetailRow label="累计退回老板"><strong className="text-primary">{formatWaveAmount(refunded)} 浪花</strong></DetailRow><DetailRow label="剩余可退">{formatWaveAmount(remaining)} 浪花</DetailRow><DetailRow label="退款笔数">{refunded > 0 ? 1 : 0} 笔</DetailRow><DetailRow label="累计追回收益">{formatWaveAmount(order.recoveredAmount || (refunded > 0 ? Math.round(refunded * 0.8) : 0))} 浪花</DetailRow></dl><div className="mt-4 rounded-xl border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">退款 #R{order.id} · {formatWaveAmount(refunded)} 浪花</strong><Badge variant="secondary">已记录</Badge></div><p className="mt-2 text-xs text-muted-foreground">2026-11-04 16:48 · {order.remark}</p><div className="mt-3 border-t pt-3 text-xs leading-6 text-muted-foreground"><p>原结算金额保留为历史记录，实际追回以冲正流水为准。</p><p>消费与权益：实销确认已按退款结果冲回，历史记录保留备查。</p></div></div></CardContent></Card>}

        {(hasAftersale || aftersaleResolved) && <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4 text-primary" />售后 #{ticket.id}</CardTitle><CardDescription>{aftersaleResolved ? '已处理' : '处理中'} · 申请于 {ticket.createdAt}</CardDescription></CardHeader><CardContent><div className="rounded-xl border bg-muted/30 p-4"><p className="text-xs text-muted-foreground">申请说明</p><p className="mt-2 text-sm leading-6">{ticket.reason}</p>{aftersaleResolved && <p className="mt-3 border-t pt-3 text-sm leading-6">处理说明：已核对订单与履约记录，原型流程处理完成。</p>}</div>{hasAftersale && <div className="mt-4 flex flex-wrap gap-2"><Button variant="outline" disabled={locked} onClick={() => setAftersaleResolveOpen(true)}>处理完成</Button>{payment !== 'REFUNDED' && <Button variant="destructive" disabled={locked} onClick={() => { setRefundPreview(false); setRefundOpen(true) }}>退款结案</Button>}<p className="w-full text-xs text-muted-foreground">处理完成只结束售后，不会自动退款。</p></div>}</CardContent></Card>}

        <Card><CardHeader><CardTitle className="text-base">状态流水</CardTitle><CardDescription>订单操作与状态变化记录</CardDescription></CardHeader><CardContent><ol className="space-y-0">{[
          ...(status === 'COMPLETED' ? [{ title: '完成订单', note: '服务履约已完成 · 运营管理员', time: '2026-11-04 16:48' }] : []),
          ...(status === 'IN_SERVICE' || status === 'COMPLETED' ? [{ title: '开始服务', note: '已接单 → 服务中 · 运营管理员', time: '2026-11-04 16:26' }] : []),
          ...(providers.length ? [{ title: '指派陪玩', note: `${providers.join(' / ')} · ${order.support}`, time: '2026-11-04 16:20' }] : []),
          { title: '创建订单', note: `${order.customerName} · ${order.route === 'STAFF' ? '客服派单' : '公开接单'}`, time: order.createdAt },
        ].map((log, index) => <li key={`${log.title}-${index}`} className="relative border-l pb-5 pl-5 last:pb-0"><span className="absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-background bg-primary" /><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-sm font-medium">{log.title}</p><p className="mt-1 text-xs text-muted-foreground">{log.note}</p></div><time className="text-[11px] text-muted-foreground">{log.time}</time></div></li>)}</ol></CardContent></Card>
      </div>

      <aside className="flex min-w-0 flex-col gap-4 xl:sticky xl:top-[168px]">
        <Card><CardHeader><CardTitle className="text-base">当前可执行操作</CardTitle><CardDescription>提交前重新核对订单与支付状态</CardDescription></CardHeader><CardContent className="space-y-2">
          {status === 'PENDING' && payment === 'PAID' && <Button className="h-11 w-full" disabled={locked} onClick={() => setAssignOpen(true)}><UsersRound className="size-4" />指派陪玩</Button>}
          {status === 'PENDING' && payment === 'UNPAID' && <Alert><Clock3 className="size-4" /><AlertDescription>订单未支付，支付完成后才可派单。</AlertDescription></Alert>}
          {status === 'GRABBED' && <Button className="h-11 w-full" disabled={locked} onClick={() => setAction('start')}>开始服务<ArrowRight className="size-4" /></Button>}
          {status === 'IN_SERVICE' && <Button className="h-11 w-full" disabled={locked} onClick={() => setAction('complete')}><Check className="size-4" />完成订单</Button>}
          {(status === 'GRABBED' || status === 'IN_SERVICE' || status === 'COMPLETED') && (payment === 'PAID' || payment === 'PARTIALLY_REFUNDED') && <Button variant="outline" className="h-11 w-full" disabled={locked || remaining <= 0} onClick={() => { setRefundAmount(String(remaining)); setRefundPreview(false); setRefundOpen(true) }}><WalletCards className="size-4" />订单退款</Button>}
          {status === 'PENDING' && <Button variant="outline" className="h-11 w-full" disabled={locked} onClick={() => setAction('cancel')}>取消订单</Button>}
          {(status === 'COMPLETED' || status === 'CANCELLED') && <div className="rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground"><CheckCircle2 className="mb-2 size-4 text-success" />{status === 'COMPLETED' ? '订单履约已完成，可继续查看结算与售后记录。' : '订单已取消，资金结果以支付状态和退款记录为准。'}</div>}
        </CardContent></Card>

        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Gamepad2 className="size-4 text-primary" />游戏资料</CardTitle></CardHeader><CardContent><dl><DetailRow label="区服">{order.gameRegion}</DetailRow><DetailRow label="游戏昵称">{order.gameNickname}</DetailRow><DetailRow label="游戏 ID"><span className="font-mono text-xs">{order.gameUid}</span></DetailRow></dl></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-base">备注与时间</CardTitle></CardHeader><CardContent><dl><DetailRow label="订单备注">{order.remark}</DetailRow><DetailRow label="下单时间">{order.createdAt}</DetailRow><DetailRow label="售后截止">2026-11-11 23:59</DetailRow><DetailRow label="实销确认">{order.settlementStatus === 'SETTLED' ? '2026-11-04 16:40' : '暂无确认记录'}</DetailRow></dl></CardContent></Card>
        <p className="px-2 text-center text-[11px] leading-5 text-muted-foreground">订单详情与操作均为高保真示例，不连接生产订单、资金或人员。</p>
      </aside>
    </div>

    <Dialog open={assignOpen} onOpenChange={setAssignOpen}><DialogContent className="sm:max-w-xl"><DialogHeader><DialogTitle>指派待接单订单</DialogTitle><DialogDescription>{order.orderNo} · 提交时重新核验资格、档期和名额。</DialogDescription></DialogHeader><div className="rounded-xl border bg-muted/30 p-4 text-sm"><p>{order.serviceName}</p><p className="mt-1 text-xs text-muted-foreground">{order.customerName} · {order.teamSize === 2 ? '双陪 2 人' : '单陪 1 人'} · {order.gender}</p></div><div className="space-y-2"><Label htmlFor="assign-search">搜索陪玩</Label><Input id="assign-search" value={assignSearch} onChange={event => setAssignSearch(event.target.value)} placeholder="输入昵称搜索" className="h-11" /></div><div className="grid gap-2 sm:grid-cols-2">{candidates.map(name => <button key={name} type="button" aria-pressed={assignProvider === name} onClick={() => setAssignProvider(name)} className={cn('flex min-h-16 items-center gap-3 rounded-xl border p-3 text-left hover:border-primary/40', assignProvider === name && 'border-primary bg-accent')}><span className="grid size-9 place-items-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{name.slice(0, 1)}</span><span><strong className="block text-sm">{name}</strong><span className="mt-0.5 block text-[11px] text-muted-foreground">资格可核验 · 当前可接</span></span>{assignProvider === name && <CheckCircle2 className="ml-auto size-4 text-primary" />}</button>)}</div><label className="flex min-h-11 items-start gap-2 text-xs leading-5"><Checkbox checked={assignChecked} onCheckedChange={value => setAssignChecked(value === true)} className="mt-0.5" />我已核对订单、陪玩人数与接单截止时间</label><DialogFooter><Button variant="outline" onClick={() => setAssignOpen(false)}>取消</Button><Button disabled={!assignProvider || !assignChecked} onClick={() => { setProviders([assignProvider]); setStatus('GRABBED'); setAssignOpen(false); setFeedback(`已在原型中指派 ${assignProvider}，未通知真实陪玩。`) }}>确认派单</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!action} onOpenChange={open => { if (!open) setAction(null) }}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>{action === 'start' ? '确认开始服务？' : action === 'complete' ? '确认完成订单？' : '确认取消订单？'}</DialogTitle><DialogDescription>{order.orderNo} · {action === 'cancel' ? '余额支付订单需核对退款结果，未支付订单需核对渠道状态。' : '请核对当前履约状态和双方进度。'}</DialogDescription></DialogHeader>{action === 'cancel' && <div className="space-y-2"><Label htmlFor="cancel-reason">取消原因（可选）</Label><Textarea id="cancel-reason" value={cancelReason} onChange={event => setCancelReason(event.target.value)} maxLength={255} placeholder="最多 255 字" /></div>}<DialogFooter><Button variant="outline" onClick={() => setAction(null)}>返回</Button><Button variant={action === 'cancel' ? 'destructive' : 'default'} onClick={confirmAction}>{action === 'start' ? '确认开始' : action === 'complete' ? '确认完成' : '确认取消订单'}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={refundOpen} onOpenChange={setRefundOpen}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{hasAftersale ? '售后退款 · 资金复核' : '订单退款 · 资金复核'}</DialogTitle><DialogDescription>先核对金额与收益追回，再确认退款。</DialogDescription></DialogHeader><div className="grid grid-cols-3 gap-2 rounded-xl border bg-muted/30 p-4 text-center">{[['原实付', order.amount], ['累计已退', refunded], ['剩余可退', remaining]].map(([label, value]) => <div key={label}><p className="text-[11px] text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold tabular-nums">{formatWaveAmount(value)}<small className="ml-1 text-[10px] font-normal">浪花</small></p></div>)}</div><div className="grid gap-4 md:grid-cols-2"><div className="space-y-2"><Label htmlFor="refund-amount">本次退款金额（浪花）</Label><Input id="refund-amount" inputMode="numeric" value={refundAmount} disabled={refundPreview} onChange={event => { setRefundAmount(event.target.value); setRefundPreview(false) }} className="h-11" /><Button variant="link" className="h-auto p-0 text-xs" disabled={refundPreview} onClick={() => { setRefundAmount(String(remaining)); setRefundPreview(false) }}>填入全部余款</Button></div><div className="space-y-2"><Label htmlFor="refund-reason">退款原因 / 处理说明</Label><Textarea id="refund-reason" value={refundReason} disabled={refundPreview} onChange={event => { setRefundReason(event.target.value); setRefundPreview(false) }} placeholder="至少 2 个字" /></div></div>{refundError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{refundError}</AlertDescription></Alert>}{refundPreview && <div className="space-y-3 rounded-xl border bg-card p-4"><div className="flex items-center gap-2 text-sm font-semibold"><CheckCircle2 className="size-4 text-success" />服务端试算版式 · 示例结果</div><dl><DetailRow label="本次退回老板">{formatWaveAmount(refundAmount)} 浪花</DetailRow><DetailRow label="本次需追回收益">{formatWaveAmount(Number(refundAmount) * 0.8)} 浪花</DetailRow><DetailRow label="退款后余款">{formatWaveAmount(remaining - Number(refundAmount))} 浪花</DetailRow><DetailRow label="退款范围">{Number(refundAmount) >= remaining ? '退还全部剩余款项' : '部分退款，订单保留'}</DetailRow></dl><p className="text-xs leading-5 text-muted-foreground">金额及来源在真实业务中由服务端试算；确认时会重新核对。此处仅演示复核流程。</p></div>}<DialogFooter><Button variant="outline" onClick={() => { if (refundPreview) setRefundPreview(false); else setRefundOpen(false) }}>{refundPreview ? '返回修改' : '取消'}</Button>{!refundPreview ? <Button onClick={previewRefund}>核对退款试算</Button> : <Button variant="destructive" onClick={confirmRefund}>确认本次退款</Button>}</DialogFooter></DialogContent></Dialog>

    <Dialog open={aftersaleResolveOpen} onOpenChange={setAftersaleResolveOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>处理售后</DialogTitle><DialogDescription>确认处理只结束售后，不会自动退款。</DialogDescription></DialogHeader><div className="space-y-2"><Label htmlFor="aftersale-resolution">处理说明</Label><Textarea id="aftersale-resolution" value={resolution} onChange={event => setResolution(event.target.value)} placeholder="填写处理说明（2–1000 字）" /></div><DialogFooter><Button variant="outline" onClick={() => setAftersaleResolveOpen(false)}>返回</Button><Button disabled={resolution.trim().length < 2} onClick={() => { setAftersaleResolved(true); setAftersaleResolveOpen(false); setFeedback('售后已在原型中处理完成，未自动退款。') }}>确认处理</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
