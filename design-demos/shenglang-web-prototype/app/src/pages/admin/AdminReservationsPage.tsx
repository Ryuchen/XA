import { useMemo, useState, type FormEvent } from 'react'
import {
  ArrowRight,
  CalendarCheck2,
  CalendarClock,
  Check,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  Clock3,
  Coins,
  Headphones,
  Info,
  Link2,
  LockKeyhole,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  UserRound,
  X,
  XCircle,
} from 'lucide-react'
import type { AdminReservationsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { adminReservationStatusLabel, initialAdminReservations, type AdminReservation, type AdminReservationStatus } from '@/pages/admin/admin-reservation-data'
import { formatWaveAmount } from '@/lib/format'

interface AdminReservationsPageProps {
  scenario: AdminReservationsScenario
  onScenarioChange: (scenario: AdminReservationsScenario) => void
  onOpenOrder: (orderNo: string) => void
}

type DetailMode = 'detail' | 'action' | 'quote' | 'review'
type ReservationAction = 'confirm' | 'reject' | 'cancel'

function coins(amount: number) {
  return formatWaveAmount(amount / 10)
}

function statusClass(status: AdminReservationStatus) {
  if (status === 'PENDING') return 'border-warning/30 bg-warning/10 text-warning-foreground'
  if (status === 'CONFIRMED') return 'border-primary/30 bg-primary/10 text-primary'
  if (status === 'CONVERTED') return 'border-success/30 bg-success/10 text-success'
  return 'bg-muted/60 text-muted-foreground'
}

export function AdminReservationsPage({ scenario, onScenarioChange, onOpenOrder }: AdminReservationsPageProps) {
  const [records, setRecords] = useState<AdminReservation[]>(initialAdminReservations)
  const [status, setStatus] = useState<'ALL' | AdminReservationStatus>(scenario === 'pending' ? 'PENDING' : scenario === 'confirmed' ? 'CONFIRMED' : scenario === 'converted' ? 'CONVERTED' : 'ALL')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detailMode, setDetailMode] = useState<DetailMode>('detail')
  const [action, setAction] = useState<ReservationAction>('confirm')
  const [reason, setReason] = useState('')
  const [quoteRecovered, setQuoteRecovered] = useState(false)
  const [conversionRecovered, setConversionRecovered] = useState(false)
  const [createOpen, setCreateOpen] = useState(scenario === 'create' || scenario === 'create-review')
  const [createUncertain, setCreateUncertain] = useState(scenario === 'create-review')
  const [createError, setCreateError] = useState('')
  const [feedback, setFeedback] = useState('')

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return records
      .filter(row => status === 'ALL' || row.status === status)
      .sort((a, b) => status === 'PENDING' || status === 'CONFIRMED' ? a.startTime.localeCompare(b.startTime) : b.startTime.localeCompare(a.startTime))
  }, [records, scenario, status])
  const selected = selectedId ? records.find(row => row.id === selectedId) || null : null
  const canDispatch = scenario !== 'readonly'
  const canCancel = scenario !== 'readonly'
  const quoteLoading = scenario === 'quote-loading' && !quoteRecovered
  const quoteFailed = scenario === 'quote-error' && !quoteRecovered
  const insufficient = scenario === 'insufficient'
  const walletBalance = insufficient && selected ? Math.max(selected.quote.amount - 100, 0) : selected?.quote.walletBalance || 0

  function openDetail(id: number) {
    setSelectedId(id)
    setDetailMode('detail')
    setReason('')
    setQuoteRecovered(false)
    setConversionRecovered(false)
  }

  function closeDetail() {
    setSelectedId(null)
    setDetailMode('detail')
    setReason('')
  }

  function openAction(next: ReservationAction) {
    setAction(next)
    setReason('')
    setDetailMode('action')
  }

  function submitAction() {
    if (!selected) return
    const nextStatus: AdminReservationStatus = action === 'confirm' ? 'CONFIRMED' : action === 'reject' ? 'REJECTED' : 'CANCELLED'
    setRecords(items => items.map(row => row.id === selected.id ? { ...row, status: nextStatus, rejectReason: action === 'reject' ? reason.trim() : row.rejectReason, cancelReason: action === 'cancel' ? reason.trim() : row.cancelReason } : row))
    setDetailMode('detail')
    setFeedback(action === 'confirm' ? '预约已在原型中确认，可继续核对实时报价。' : action === 'reject' ? '预约已在原型中拒绝，档期已释放。' : '预约已在原型中取消，未产生扣款。')
  }

  function openQuote() {
    setQuoteRecovered(false)
    setDetailMode('quote')
  }

  function convert() {
    if (!selected || insufficient) return
    if (scenario === 'convert-review' && !conversionRecovered) {
      setDetailMode('review')
      return
    }
    setRecords(items => items.map(row => row.id === selected.id ? { ...row, status: 'CONVERTED', orderNo: 'SL202611040032' } : row))
    setDetailMode('detail')
    setFeedback('预约已在原型中转单，未创建真实订单或产生扣款。')
  }

  function recoverConversion() {
    if (!selected) return
    setRecords(items => items.map(row => row.id === selected.id ? { ...row, status: 'CONVERTED', orderNo: 'SL202611040032' } : row))
    setConversionRecovered(true)
    setDetailMode('detail')
    setFeedback('已重新读取示例预约并核对到关联订单，请勿重复转单。')
  }

  function openCreate() {
    setCreateError('')
    setCreateUncertain(scenario === 'create-review')
    setCreateOpen(true)
  }

  function createReservation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const start = String(data.get('startTime') || '')
    const end = String(data.get('endTime') || '')
    const rounds = Number(data.get('gameRounds'))
    const timePattern = /^\d{4}-\d{2}-\d{2}T\d{2}:(00|15|30|45)$/
    if (!timePattern.test(start) || !timePattern.test(end)) { setCreateError('预约起止时间须按 15 分钟刻度选择。'); return }
    const startAt = new Date(`${start}:00+08:00`).getTime()
    const endAt = new Date(`${end}:00+08:00`).getTime()
    const duration = Math.round((endAt - startAt) / 60000)
    if (!Number.isFinite(duration) || duration < 30 || duration > 720) { setCreateError('单次预约时长须为 30 分钟至 12 小时，且结束晚于开始。'); return }
    if (!Number.isInteger(rounds) || rounds < 1 || rounds > 999) { setCreateError('购买数量须为 1 至 999 的整数。'); return }
    const serviceId = Number(data.get('serviceId'))
    const service = serviceId === 12 ? { name: '英雄联盟 · 峡谷同行', price: 680 } : { name: '英雄联盟 · 双排', price: 760 }
    const providerName = String(data.get('providerAccountId')) === '202' ? '北夜' : '小鹿'
    const record: AdminReservation = {
      ...initialAdminReservations[1],
      id: 309,
      reservationNo: 'RSV202611040019',
      status: 'PENDING',
      customerAccountId: 101,
      customerName: '海边听风',
      providerAccountId: Number(data.get('providerAccountId')),
      providerName,
      serviceId,
      serviceName: service.name,
      startTime: start.replace('T', ' '),
      endTime: end.replace('T', ' '),
      durationMinutes: duration,
      gameRounds: rounds,
      estimatedAmount: service.price * rounds,
      gameRegion: String(data.get('gameRegion') || ''),
      gameNickname: String(data.get('gameNickname') || ''),
      gameUid: String(data.get('gameUid') || ''),
      remark: String(data.get('remark') || ''),
      createdAt: '2026-11-04 17:18',
      cancelReason: '',
      rejectReason: '',
      orderNo: undefined,
    }
    setRecords(items => [record, ...items.filter(item => item.id !== record.id)])
    setStatus('ALL')
    setCreateOpen(false)
    setCreateError('')
    setFeedback('预约已在原型中创建并等待确认；未占用真实档期或发送通知。')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="预约列表加载中"><Skeleton className="h-32 rounded-xl" /><div className="grid gap-4 md:grid-cols-4"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /></div><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">预约列表加载失败</CardTitle><CardDescription className="mt-2">请检查网络或订单查看权限后重试，当前没有展示推测预约。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">预约与转单</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">档期与履约 · 示例</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">先锁定陪玩档期，再核对双方安排与实时报价。预约创建和确认均不扣款，只有成功转单才进入正式订单。</p></div>
      {canDispatch && <Button variant="secondary" onClick={openCreate}><Plus className="size-4" />代老板预约</Button>}
    </div></section>

    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      { label: '全部预约', value: records.length, unit: '笔', note: '预约与订单独立编号', icon: CalendarClock },
      { label: '等待确认', value: records.filter(row => row.status === 'PENDING').length, unit: '笔', note: '占用档期，但不占用资金', icon: Clock3 },
      { label: '已确认待转单', value: records.filter(row => row.status === 'CONFIRMED').length, unit: '笔', note: '转单前必须重新报价', icon: CalendarCheck2 },
      { label: '已转正式订单', value: records.filter(row => row.status === 'CONVERTED').length, unit: '笔', note: '一笔预约最多转一笔订单', icon: ClipboardCheck },
    ].map(item => <div key={item.label} className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">{item.label}</dt><item.icon className="size-4 text-primary" /></div><dd className="mt-3 text-2xl font-semibold tabular-nums">{item.value}<span className="ml-1 text-xs font-normal text-muted-foreground">{item.unit}</span></dd><p className="mt-1 text-[11px] text-muted-foreground">{item.note}</p></div>)}</dl>

    <Alert><Info className="size-4" /><AlertTitle>创建时估价不等于最终扣款</AlertTitle><AlertDescription>转单报价会重新核对当前服务、老板优惠、活动、优惠券与钱包余额。已确认预约转单后立即进入已接单订单；过期、拒绝、取消和已转单均为终态。</AlertDescription></Alert>
    {scenario === 'readonly' && <Alert><LockKeyhole className="size-4" /><AlertTitle>当前为预约只读权限</AlertTitle><AlertDescription>可查看预约与关联订单；代约、确认、拒绝、取消和转单需要独立的派单或取消权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>预约更新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">现有筛选与详情入口已保留。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新刷新</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><CalendarClock className="size-4 text-primary" />预约列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 笔 · 全部时间为北京时间{status === 'PENDING' || status === 'CONFIRMED' ? ' · 按开始时间从近到远' : ''}</CardDescription></div><Select value={status} onValueChange={value => setStatus(value as 'ALL' | AdminReservationStatus)}><SelectTrigger className="h-10 w-full sm:w-44" aria-label="预约状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem>{Object.entries(adminReservationStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div></CardHeader>
      <CardContent>{rows.length > 0 ? <div className="grid items-start gap-3 md:grid-cols-2 2xl:grid-cols-3">{rows.map(row => <article key={row.id} className="group min-w-0 rounded-xl border bg-card p-4 shadow-sm transition-colors hover:border-primary/30">
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><Badge variant="outline" className={statusClass(row.status)}>{adminReservationStatusLabel[row.status]}</Badge><h2 className="mt-3 truncate text-sm font-semibold">{row.serviceName}</h2><p className="mt-1 font-mono text-[10px] text-muted-foreground">{row.reservationNo}</p></div><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/8 text-primary"><CalendarClock className="size-4" /></span></div>
        <dl className="mt-4 grid gap-3 border-t pt-4 text-xs"><div className="grid grid-cols-[48px_minmax(0,1fr)] gap-2"><dt className="text-muted-foreground">开始</dt><dd className="font-medium tabular-nums">{row.startTime}</dd></div><div className="grid grid-cols-[48px_minmax(0,1fr)] gap-2"><dt className="text-muted-foreground">结束</dt><dd className="tabular-nums">{row.endTime}</dd></div><div className="grid grid-cols-2 gap-3 border-t pt-3"><div className="flex items-center gap-2"><UserRound className="size-3.5 text-muted-foreground" /><span className="truncate">{row.customerName}</span></div><div className="flex items-center gap-2"><Headphones className="size-3.5 text-muted-foreground" /><span className="truncate">{row.providerName}</span></div></div></dl>
        <div className="mt-4 flex items-end justify-between gap-3 border-t pt-3"><div><p className="text-[10px] text-muted-foreground">创建时估价 · {row.gameRounds} 局</p><p className="mt-1 text-sm font-semibold tabular-nums">{coins(row.estimatedAmount)} 浪花</p></div><div className="flex gap-1.5"><Button variant="outline" size="sm" onClick={() => openDetail(row.id)}>查看详情</Button>{row.orderNo && <Button variant="ghost" size="sm" onClick={() => onOpenOrder(row.orderNo!)}>查看订单</Button>}</div></div>
      </article>)}</div> : <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><CalendarClock className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">当前状态暂无预约</h2><p className="mt-2 text-sm text-muted-foreground">切换到全部状态查看其他预约记录。</p><Button variant="outline" className="mt-5" onClick={() => { setStatus('ALL'); if (scenario === 'empty') onScenarioChange('default') }}>查看全部预约</Button></div>}</CardContent>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/20 px-4 py-3 text-xs text-muted-foreground"><span>所有预约、报价与状态操作均为本地示例。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!selected} onOpenChange={open => { if (!open) closeDetail() }}><DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{detailMode === 'quote' ? '实时报价并转单' : detailMode === 'review' ? '转单结果待核对' : detailMode === 'action' ? `${action === 'confirm' ? '确认' : action === 'reject' ? '拒绝' : '取消'}预约` : '预约详情'}</DialogTitle><DialogDescription>{selected?.reservationNo} · {selected?.customerName} / {selected?.providerName} · 示例</DialogDescription></DialogHeader>
      {selected && detailMode === 'detail' && <>
        <section className="rounded-xl border bg-primary/5 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-sm font-semibold">{selected.serviceName}</p><p className="mt-1 text-xs text-muted-foreground">{selected.platform === 'PC' ? '端游' : '手游'} · {selected.securityLevel === 'ANY' ? '不限难度' : selected.securityLevel === 'CONFIDENTIAL' ? '机密' : '绝密'} · {selected.gameRounds} 局</p></div><Badge variant="outline" className={statusClass(selected.status)}>{adminReservationStatusLabel[selected.status]}</Badge></div><div className="mt-4 grid gap-2 border-t pt-3 text-xs sm:grid-cols-2"><p className="flex items-center gap-2"><Clock3 className="size-3.5 text-primary" />{selected.startTime} 开始</p><p className="flex items-center gap-2"><Clock3 className="size-3.5 text-primary" />{selected.endTime} 结束</p></div></section>
        {selected.status === 'CONFIRMED' && <Alert><Coins className="size-4" /><AlertTitle>转单前重新计价</AlertTitle><AlertDescription>创建时估价 {coins(selected.estimatedAmount)} 浪花仅供对比；实际扣款以本次实时报价为准。</AlertDescription></Alert>}
        {selected.status === 'EXPIRED' && <Alert><CircleAlert className="size-4" /><AlertTitle>预约时段已结束</AlertTitle><AlertDescription>原预约不能转单；如需服务，请重新选择档期。</AlertDescription></Alert>}
        <dl className="grid gap-x-6 rounded-xl border p-4 sm:grid-cols-2">{[
          ['老板', selected.customerName], ['陪玩', selected.providerName], ['预约时长', `${selected.durationMinutes} 分钟`], ['创建时估价', `${coins(selected.estimatedAmount)} 浪花`], ['游戏区', selected.gameRegion || '未填写'], ['游戏昵称', selected.gameNickname || '未填写'], ['游戏 ID', selected.gameUid || '未填写'], ['创建时间', selected.createdAt], ['备注', selected.remark || '未填写'], ...(selected.rejectReason ? [['拒绝原因', selected.rejectReason]] : []), ...(selected.cancelReason ? [['取消原因', selected.cancelReason]] : []), ...(selected.orderNo ? [['关联订单', selected.orderNo]] : []),
        ].map(([label, value]) => <div key={label} className="border-b py-3 last:border-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{value}</dd></div>)}</dl>
        <div className="flex flex-wrap gap-2">{selected.status === 'PENDING' && canDispatch && <><Button variant="outline" onClick={() => openAction('confirm')}><Check className="size-4" />确认预约</Button><Button variant="outline" onClick={() => openAction('reject')}><XCircle className="size-4" />拒绝预约</Button></>}{(selected.status === 'PENDING' || selected.status === 'CONFIRMED') && canCancel && <Button variant="outline" onClick={() => openAction('cancel')}>取消预约</Button>}{selected.status === 'CONFIRMED' && canDispatch && <Button onClick={openQuote}>报价并转单<ArrowRight className="size-4" /></Button>}{selected.orderNo && <Button variant="outline" onClick={() => onOpenOrder(selected.orderNo!)}><Link2 className="size-4" />查看关联订单</Button>}</div>
      </>}
      {selected && detailMode === 'action' && <><Alert><ShieldCheck className="size-4" /><AlertTitle>请核对状态变更</AlertTitle><AlertDescription>{action === 'confirm' ? '确认后预约进入已确认状态，仍不会扣款。' : action === 'reject' ? '拒绝后预约进入终态并释放档期。' : '取消后预约进入终态并释放档期，不产生资金流水。'}</AlertDescription></Alert>{action !== 'confirm' && <div className="space-y-2"><Label htmlFor="reservation-action-reason">原因（选填）</Label><Textarea id="reservation-action-reason" rows={3} maxLength={255} value={reason} onChange={event => setReason(event.target.value)} placeholder="填写原因，将保留在示例记录中" /></div>}<DialogFooter><Button variant="outline" onClick={() => setDetailMode('detail')}>返回详情</Button><Button onClick={submitAction}>确认{action === 'confirm' ? '预约' : action === 'reject' ? '拒绝' : '取消'}</Button></DialogFooter></>}
      {selected && detailMode === 'quote' && <>
        {quoteLoading && <><div className="space-y-3" role="status"><p className="text-sm text-muted-foreground">正在按当前规则重新报价…</p><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /></div><DialogFooter><Button variant="outline" onClick={() => setQuoteRecovered(true)}>完成示例读取</Button></DialogFooter></>}
        {quoteFailed && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>实时报价读取失败</AlertTitle><AlertDescription>当前未展示推测价格，也不能确认转单。<Button variant="link" className="ml-2 h-auto p-0" onClick={() => setQuoteRecovered(true)}>重新报价</Button></AlertDescription></Alert>}
        {!quoteLoading && !quoteFailed && <><section className="rounded-xl border bg-primary/5 p-4"><p className="text-xs text-muted-foreground">本次实际应付</p><p className="mt-2 text-3xl font-semibold tabular-nums text-primary">{coins(selected.quote.amount)} <span className="text-sm font-normal">浪花</span></p><p className="mt-2 text-xs text-muted-foreground">创建时估价 {coins(selected.estimatedAmount)} 浪花 · 当前钱包余额 {coins(walletBalance)} 浪花</p></section><dl className="grid grid-cols-2 gap-3 rounded-xl border p-4 text-xs sm:grid-cols-4">{[['服务原价', coins(selected.quote.originalAmount)], ['老板优惠', `−${coins(selected.quote.bossDiscount)}`], ['活动优惠', `−${coins(selected.quote.promoDiscount)}`], ['优惠券', `−${coins(selected.quote.couponDiscount)}`]].map(([label, value]) => <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-2 text-sm font-semibold tabular-nums">{value} 浪花</dd></div>)}</dl>{insufficient ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>老板余额不足或钱包不可用</AlertTitle><AlertDescription>请先核对钱包，当前不能转单，不会创建订单或扣款。</AlertDescription></Alert> : <Alert><ShieldCheck className="size-4" /><AlertTitle>确认后立即进入已接单订单</AlertTitle><AlertDescription>将从老板余额扣除 {coins(selected.quote.amount)} 浪花。请与双方确认服务安排；原型只演示状态变化，不产生真实扣款。</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => setDetailMode('detail')}>返回详情</Button><Button disabled={insufficient} onClick={convert}>确认转单</Button></DialogFooter></>}
      </>}
      {selected && detailMode === 'review' && <><Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>转单结果未确认</AlertTitle><AlertDescription>请勿重复提交。先重新读取预约详情并核对关联订单，再决定后续操作。</AlertDescription></Alert><section className="rounded-xl border p-4 text-sm leading-6"><p>预约 {selected.reservationNo}</p><p className="mt-2 text-muted-foreground">确认金额 {coins(selected.quote.amount)} 浪花 · 当前示例已锁定重复转单。</p></section><DialogFooter><Button onClick={recoverConversion}><RefreshCw className="size-4" />重新读取并核对</Button></DialogFooter></>}
      {detailMode === 'detail' && <DialogFooter><Button variant="outline" onClick={closeDetail}>关闭</Button></DialogFooter>}
    </DialogContent></Dialog>

    <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>代老板预约</DialogTitle><DialogDescription>选择双方、服务与北京时间；创建时不扣款，仅占用示例档期。</DialogDescription></DialogHeader>
      {createUncertain ? <><Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>预约结果未确认</AlertTitle><AlertDescription>请先在预约列表核对，避免重复创建。不要依据旧表单重复提交。</AlertDescription></Alert><p className="rounded-xl border bg-muted/30 p-4 text-sm leading-6">海边听风 · 小鹿 · 英雄联盟双排 · 2026-11-05 20:00（北京时间）</p><DialogFooter><Button variant="outline" onClick={() => setCreateUncertain(false)}>已核对，继续新建</Button><Button onClick={() => { setCreateOpen(false); setCreateUncertain(false); setStatus('ALL') }}>查看预约列表</Button></DialogFooter></> : <form id="admin-reservation-create" className="grid gap-4" onSubmit={createReservation}>
        <div className="grid gap-4 sm:grid-cols-3"><div className="space-y-2"><Label>老板</Label><Select name="customerAccountId" defaultValue="101"><SelectTrigger className="w-full" aria-label="选择老板"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="101">海边听风 · B1001</SelectItem></SelectContent></Select></div><div className="space-y-2"><Label>服务</Label><Select name="serviceId" defaultValue="11"><SelectTrigger className="w-full" aria-label="选择服务"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="11">英雄联盟 · 双排</SelectItem><SelectItem value="12">英雄联盟 · 峡谷同行</SelectItem></SelectContent></Select></div><div className="space-y-2"><Label>陪玩</Label><Select name="providerAccountId" defaultValue="201"><SelectTrigger className="w-full" aria-label="选择陪玩"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="201">小鹿 · P2001</SelectItem><SelectItem value="202">北夜 · P2002</SelectItem></SelectContent></Select></div></div>
        <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="create-start-time">开始时间</Label><Input id="create-start-time" name="startTime" type="datetime-local" step="900" defaultValue="2026-11-05T20:00" /></div><div className="space-y-2"><Label htmlFor="create-end-time">结束时间</Label><Input id="create-end-time" name="endTime" type="datetime-local" step="900" defaultValue="2026-11-05T22:00" /></div></div>
        <p className="text-[11px] text-muted-foreground">北京时间 · 15 分钟刻度 · 30 分钟至 12 小时 · 最多提前 30 天。双陪套餐需另行安排，不在此表单创建。</p>
        <div className="grid gap-4 sm:grid-cols-3"><div className="space-y-2"><Label htmlFor="create-rounds">购买数量</Label><Input id="create-rounds" name="gameRounds" type="number" min="1" max="999" step="1" defaultValue="2" /></div><div className="space-y-2"><Label htmlFor="create-region">游戏区</Label><Input id="create-region" name="gameRegion" maxLength={50} defaultValue="艾欧尼亚" /></div><div className="space-y-2"><Label htmlFor="create-nickname">游戏昵称</Label><Input id="create-nickname" name="gameNickname" maxLength={50} defaultValue="听风入海" /></div></div>
        <div className="space-y-2"><Label htmlFor="create-uid">游戏 ID</Label><Input id="create-uid" name="gameUid" maxLength={50} defaultValue="LOL-829361" /></div><div className="space-y-2"><Label htmlFor="create-remark">备注</Label><Textarea id="create-remark" name="remark" rows={2} maxLength={255} defaultValue="希望温柔耐心，尽量连麦。" /></div>
        {createError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{createError}</AlertDescription></Alert>}<Alert><Info className="size-4" /><AlertDescription>创建只生成待确认预约，不扣款。所有选项、档期与通知均为本地示例。</AlertDescription></Alert><DialogFooter><Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>取消</Button><Button type="submit">创建预约</Button></DialogFooter>
      </form>}
    </DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
