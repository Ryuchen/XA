import { useState, type ReactNode } from 'react'
import { AlertCircle, ArrowLeft, ArrowRight, CalendarClock, CalendarX2, Check, CheckCircle2, Clock3, Copy, Gamepad2, LoaderCircle, LogIn, ReceiptText, RefreshCw, ShieldCheck, UserRound, WalletCards, XCircle } from 'lucide-react'
import { BottomDock } from '@/components/prototype/BottomDock'
import { PhoneFrame } from '@/components/prototype/PhoneFrame'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { formatWaveAmount } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Reservation, ReservationStatus } from '@/pages/boss/reservation-data'

export type ReservationDetailScenario = 'default' | 'guest' | 'loading' | 'error' | 'pending' | 'quoting' | 'quote-ready' | 'quote-error' | 'insufficient' | 'convert-pending' | 'converted' | 'cancelled' | 'rejected' | 'expired'

interface ReservationDetailPageProps {
  scenario: ReservationDetailScenario
  reservation: Reservation
  onLogin: () => void
  onBack: () => void
  onHome: () => void
  onCompanions: () => void
  onOrders: () => void
  onOrder: () => void
  onWallet: () => void
  onRebook: () => void
  onRetry: () => void
}

const statusStyles: Record<ReservationStatus, string> = {
  PENDING: 'border-warning/25 bg-warning/10 text-warning',
  CONFIRMED: 'border-primary/25 bg-primary/10 text-primary',
  CONVERTED: 'border-success/25 bg-success/10 text-success',
  REJECTED: 'border-border bg-muted/45 text-muted-foreground',
  CANCELLED: 'border-border bg-muted/45 text-muted-foreground',
  EXPIRED: 'border-border bg-muted/45 text-muted-foreground',
}

export function ReservationDetailPage({ scenario, reservation, onLogin, onBack, onHome, onCompanions, onOrders, onOrder, onWallet, onRebook, onRetry }: ReservationDetailPageProps) {
  const [quoteState, setQuoteState] = useState<'none' | 'loading' | 'ready' | 'error'>(scenario === 'quoting' ? 'loading' : ['quote-ready', 'insufficient'].includes(scenario) ? 'ready' : scenario === 'quote-error' || scenario === 'convert-pending' ? 'error' : 'none')
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [cancelBusy, setCancelBusy] = useState(false)
  const [demoCancelled, setDemoCancelled] = useState(false)
  const [convertOpen, setConvertOpen] = useState(false)
  const [convertBusy, setConvertBusy] = useState(false)
  const [demoConverted, setDemoConverted] = useState(false)
  const [copied, setCopied] = useState(false)
  const [pendingRecovered, setPendingRecovered] = useState(false)
  const status: ReservationStatus = demoConverted ? 'CONVERTED' : demoCancelled ? 'CANCELLED' : scenario === 'converted' ? 'CONVERTED' : scenario === 'cancelled' ? 'CANCELLED' : scenario === 'rejected' ? 'REJECTED' : scenario === 'expired' ? 'EXPIRED' : scenario === 'pending' ? 'PENDING' : 'CONFIRMED'
  const statusText = { PENDING: '待陪玩确认', CONFIRMED: '已确认', CONVERTED: '已成单', REJECTED: '已拒绝', CANCELLED: '已取消', EXPIRED: '已过期' }[status]
  const canConvert = status === 'CONFIRMED'
  const canCancel = ['CONFIRMED', 'PENDING'].includes(status)
  const quoteAmount = scenario === 'quote-ready' || scenario === 'insufficient' ? 148 : reservation.estimatedAmount
  const balance = scenario === 'insufficient' ? 96 : 326
  const sufficient = balance >= quoteAmount
  const quoteVisible = canConvert && quoteState === 'ready'
  const quoteError = scenario === 'convert-pending' && !pendingRecovered ? '成单结果待确认，请刷新预约查看。不要重复提交，避免重复成单。' : quoteState === 'error' ? '商品信息已变化，请重新获取报价。' : ''
  const orderNo = reservation.orderNo || 'SL202611031128'
  const busy = cancelBusy || convertBusy || quoteState === 'loading'

  async function copyReservation() {
    try { await navigator.clipboard.writeText(reservation.reservationNo); setCopied(true); window.setTimeout(() => setCopied(false), 1600) } catch { setCopied(false) }
  }
  function requestQuote() {
    if (!canConvert || quoteState === 'loading') return
    setQuoteState('loading')
    window.setTimeout(() => setQuoteState('ready'), 500)
  }
  function confirmCancel() {
    if (cancelBusy) return
    setCancelBusy(true)
    window.setTimeout(() => { setDemoCancelled(true); setQuoteState('none'); setCancelBusy(false); setCancelOpen(false) }, 450)
  }
  function confirmConvert() {
    if (convertBusy || !quoteVisible || !sufficient) return
    setConvertBusy(true)
    window.setTimeout(() => { setDemoConverted(true); setQuoteState('none'); setConvertBusy(false); setConvertOpen(false) }, 500)
  }
  function handleDockNavigate(key: string) {
    if (key === 'home') onHome()
    else if (key === 'companions') onCompanions()
    else if (key === 'orders') onOrders()
    else if (key === 'mine') onBack()
  }

  return (
    <PhoneFrame bottomNavigation={<BottomDock active="mine" onNavigate={handleDockNavigate} />}>
      <main className={cn('pb-32 md:pb-12', quoteVisible && sufficient && 'pb-44 md:pb-12')}>
        <div className="flex items-center justify-between px-4 py-2 md:px-6"><Button type="button" variant="ghost" className="h-11 rounded-[8px] px-3 text-sm" onClick={onBack}><ArrowLeft className="size-4" />我的预约</Button><Badge variant="outline" className="rounded-md text-xs">预约详情 · 示例</Badge></div>

        {scenario === 'guest' ? <GuestState onLogin={onLogin} /> : scenario === 'loading' ? (
          <div className="grid gap-6 px-4 pt-2 md:grid-cols-[minmax(0,1fr)_360px] md:px-6" aria-busy="true" aria-label="正在加载预约详情"><div className="space-y-6"><Skeleton className="h-52 rounded-[12px]" /><Skeleton className="h-56 rounded-[12px]" /><Skeleton className="h-48 rounded-[12px]" /></div><Skeleton className="h-80 rounded-[12px]" /></div>
        ) : scenario === 'error' ? <ErrorState onRetry={onRetry} /> : (
          <>
            <header className="px-4 pt-2 md:px-6">
              <div className="rounded-[12px] border border-primary/15 bg-primary/[0.055] p-5">
                <div className="flex flex-wrap items-center justify-between gap-3"><Badge variant="outline" className={cn('rounded-md text-xs', statusStyles[status])}>{status === 'CONVERTED' ? <CheckCircle2 className="size-3.5" /> : ['REJECTED', 'CANCELLED', 'EXPIRED'].includes(status) ? <XCircle className="size-3.5" /> : <Clock3 className="size-3.5" />}{statusText}</Badge><span className="text-xs text-muted-foreground">预约不扣款</span></div>
                <h1 className="mt-4 text-[26px] font-semibold leading-[1.3] tracking-[-0.03em]">{reservation.service}</h1>
                <div className="mt-3 flex items-center gap-2"><Avatar className="size-8 border"><AvatarFallback className="bg-primary/10 text-xs text-primary">{reservation.providerInitial}</AvatarFallback></Avatar><span className="text-sm">{reservation.provider}</span><span className="text-muted-foreground">·</span><span className="text-sm text-muted-foreground tabular-nums">{reservation.rounds} 份服务</span></div>
                <p className="mt-4 text-sm leading-6 text-muted-foreground">{status === 'CONFIRMED' ? '陪玩已确认时段，核对实时报价后即可成单。' : status === 'PENDING' ? '正在等待陪玩确认，当前不会扣款。' : status === 'CONVERTED' ? '预约已转为订单，后续服务进度在订单详情查看。' : status === 'EXPIRED' ? '这个时段已结束，原预约不能再转成订单。' : '本次预约已结束，未生成订单。'}</p>
                <div className="mt-5 flex items-center justify-between gap-3 border-t border-primary/10 pt-4"><div className="min-w-0"><p className="text-xs text-muted-foreground">预约编号</p><p className="mt-1 break-all font-mono text-xs">{reservation.reservationNo}</p></div><Button type="button" variant="ghost" className="h-11 shrink-0 rounded-[8px] px-3 text-xs" onClick={copyReservation} aria-label={`复制预约编号 ${reservation.reservationNo}`}>{copied ? <Check className="size-4" /> : <Copy className="size-4" />}{copied ? '已复制' : '复制'}</Button></div>
              </div>
            </header>

            <div className="grid items-start gap-6 px-4 py-6 md:grid-cols-[minmax(0,1fr)_360px] md:px-6">
              <div className="space-y-6">
                <DetailSection title="预约安排" icon={CalendarClock} action={<Badge variant="outline" className="rounded-md text-xs text-muted-foreground">北京时间</Badge>}>
                  <div className="grid grid-cols-2 gap-3 rounded-[8px] bg-muted/45 p-4"><div><p className="text-xs text-muted-foreground">开始时间</p><p className="mt-2 text-sm font-semibold leading-5 tabular-nums">{reservation.startText}</p></div><div className="border-l pl-4"><p className="text-xs text-muted-foreground">预计结束</p><p className="mt-2 text-sm font-semibold leading-5 tabular-nums">{reservation.endText || '11 月 4 日 22:00'}</p></div></div>
                  <div className="mt-4 flex items-end justify-between gap-3 border-b pb-4"><span className="text-sm text-muted-foreground">预约时预估</span><p className="flex items-baseline gap-1"><strong className="text-[28px] leading-none tabular-nums">{formatWaveAmount(reservation.estimatedAmount)}</strong><span className="text-xs text-muted-foreground">浪花</span></p></div>
                  <p className="mt-3 text-xs leading-5 text-muted-foreground">预估仅供参考，陪玩确认后将以实时报价成单。</p>
                </DetailSection>

                <DetailSection title="游戏与备注" icon={Gamepad2}>
                  <DetailRow label="游戏区服" value={reservation.gameRegion || '微信区'} /><DetailRow label="游戏昵称" value={reservation.gameNickname || '海浪收藏家'} /><DetailRow label="游戏 ID" value={reservation.gameUid || '83921044'} />
                  <div className="mt-4 rounded-[8px] bg-muted/45 p-3"><p className="text-xs text-muted-foreground">服务备注</p><p className="mt-2 text-sm leading-6">{reservation.remark || '希望轻松沟通，开局前确认位置。'}</p></div>
                </DetailSection>

                {['REJECTED', 'CANCELLED'].includes(status) && <DetailSection title="处理说明" icon={XCircle}><p className="text-sm leading-6 text-muted-foreground">{status === 'REJECTED' ? reservation.rejectReason || '陪玩当前排期有变，无法承接这个时段。你可以重新选择其他服务时间。' : demoCancelled ? cancelReason.trim() || '你取消了本次预约，未产生扣款。' : reservation.cancelReason || '行程安排变化，已取消本次预约。'}</p></DetailSection>}
                {status === 'EXPIRED' && <DetailSection title="重新安排服务" icon={RefreshCw}><p className="text-sm leading-6 text-muted-foreground">可沿用原服务和陪玩重新预约，新的时段仍需等待确认。</p><Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={onRebook}><CalendarClock className="size-4" />沿用原服务重新预约</Button></DetailSection>}
              </div>

              <aside className="space-y-6 md:sticky md:top-6">
                {status === 'CONVERTED' && <DetailSection title="已生成订单" icon={ReceiptText} action={<Badge variant="outline" className="rounded-md border-success/25 bg-success/10 text-xs text-success">已成单</Badge>}><p className="text-sm leading-6 text-muted-foreground">订单已立即指派给 {reservation.provider}，费用与服务进度以订单详情为准。</p><div className="mt-4 rounded-[8px] bg-muted/45 p-3 font-mono text-xs">{orderNo}</div><Button type="button" className="mt-3 h-11 w-full rounded-[8px]" onClick={onOrder}>查看订单<ArrowRight className="size-4" /></Button></DetailSection>}

                {canConvert && <DetailSection title="实时报价" icon={WalletCards} action={quoteVisible ? <span className="flex items-center gap-1 text-xs text-success"><CheckCircle2 className="size-3.5" />已更新</span> : undefined}>
                  {quoteState === 'loading' ? <div className="space-y-3 py-2" aria-busy="true" aria-label="正在获取实时报价"><Skeleton className="h-14 rounded-[8px]" /><Skeleton className="h-20 rounded-[8px]" /><Skeleton className="h-8 w-2/3 rounded-[8px]" /><p className="flex items-center justify-center gap-2 text-xs text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在核对服务与钱包余额</p></div> : quoteVisible ? <>
                    <div className="rounded-[8px] bg-muted/45 p-3"><p className="text-sm font-semibold">{reservation.service}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">王者荣耀 · 标准陪练 · 指定 {reservation.provider}</p></div>
                    <div className="mt-4 flex items-end justify-between gap-3 border-y py-4"><span className="text-sm text-muted-foreground">当前应付</span><p className="flex items-baseline gap-1"><strong className="text-[32px] leading-none text-primary tabular-nums">{formatWaveAmount(quoteAmount)}</strong><span className="text-xs text-muted-foreground">浪花</span></p></div>
                    <div className="mt-3 flex items-center justify-between text-sm"><span className="text-muted-foreground">钱包余额</span><span className={cn('font-medium tabular-nums', !sufficient && 'text-warning')}>{formatWaveAmount(balance)} 浪花</span></div>
                    {quoteAmount !== reservation.estimatedAmount && <p className="mt-4 rounded-[8px] bg-warning/10 p-3 text-xs leading-5 text-warning">报价比预约预估多 {formatWaveAmount(quoteAmount - reservation.estimatedAmount)} 浪花，请确认当前金额后再成单。</p>}
                    {!sufficient && <div className="mt-4 rounded-[8px] border border-warning/25 bg-warning/5 p-3"><p className="text-xs leading-5 text-warning">余额不足，还差 {formatWaveAmount(quoteAmount - balance)} 浪花。返回后需重新获取报价。</p><Button type="button" variant="outline" className="mt-3 h-11 w-full rounded-[8px]" onClick={onWallet}><WalletCards className="size-4" />查看钱包并处理余额</Button></div>}
                    {sufficient && <Button type="button" className="mt-4 hidden h-12 w-full rounded-[8px] md:flex" onClick={() => setConvertOpen(true)} disabled={convertBusy}>核对并确认成单<ArrowRight className="size-4" /></Button>}
                    <Button type="button" variant="ghost" className="mt-2 h-11 w-full rounded-[8px] text-xs" onClick={requestQuote}><RefreshCw className="size-4" />刷新报价</Button>
                  </> : <>
                    <div className="rounded-[8px] border border-dashed bg-muted/20 p-4 text-center"><ShieldCheck className="mx-auto size-6 text-primary" /><p className="mt-3 text-xs leading-5 text-muted-foreground">获取实时价格后再决定是否成单，此步骤不会扣款。</p></div>
                    {quoteError && <div role="alert" className="mt-3 rounded-[8px] bg-destructive/10 p-3 text-xs leading-5 text-destructive">{quoteError}</div>}
                    {scenario === 'convert-pending' && !pendingRecovered ? <Button type="button" variant="outline" className="mt-4 h-11 w-full rounded-[8px]" onClick={() => { setPendingRecovered(true); setQuoteState('none') }}><RefreshCw className="size-4" />刷新预约核对结果</Button> : <Button type="button" className="mt-4 h-11 w-full rounded-[8px]" onClick={requestQuote}><RefreshCw className="size-4" />{quoteState === 'error' ? '重新获取报价' : '获取实时报价'}</Button>}
                    {pendingRecovered && <p role="status" className="mt-3 rounded-[8px] bg-primary/10 p-3 text-xs leading-5 text-primary">预约仍可成单，旧报价已失效。请重新获取报价。</p>}
                  </>}
                </DetailSection>}

                {canCancel && <Button type="button" variant="ghost" className="h-12 w-full rounded-[8px] text-muted-foreground hover:text-destructive" onClick={() => setCancelOpen(true)} disabled={busy}><CalendarX2 className="size-4" />取消预约</Button>}
                <p className="px-3 text-center text-xs leading-5 text-muted-foreground">交互原型 · 不获取真实报价，不扣款或创建订单</p>
              </aside>
            </div>
          </>
        )}
      </main>

      {!['guest', 'loading', 'error'].includes(scenario) && quoteVisible && sufficient && <div className="absolute inset-x-0 bottom-[76px] z-30 border-t bg-background/95 px-4 py-3 backdrop-blur-xl md:hidden"><div className="flex items-center gap-4"><div className="shrink-0"><p className="text-xs text-muted-foreground">应付 · 浪花</p><p className="mt-1 text-xl font-semibold text-primary tabular-nums">{formatWaveAmount(quoteAmount)}</p></div><Button type="button" className="h-12 flex-1 rounded-[8px]" onClick={() => setConvertOpen(true)} disabled={convertBusy}>核对并确认成单<ArrowRight className="size-4" /></Button></div></div>}

      <Dialog open={cancelOpen} onOpenChange={open => !cancelBusy && setCancelOpen(open)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-destructive/10 text-destructive"><CalendarX2 className="size-5" /></div><DialogTitle className="text-left text-xl">确认取消预约？</DialogTitle><DialogDescription className="text-left text-sm leading-6">将取消 {reservation.startText} 的 {reservation.service}。预约尚未成单，不会涉及扣款或退款。</DialogDescription></DialogHeader><div><Label htmlFor="reservation-cancel-reason" className="text-sm">取消原因（选填）</Label><Textarea id="reservation-cancel-reason" value={cancelReason} onChange={event => setCancelReason(event.target.value)} maxLength={255} placeholder="可填写取消原因" className="mt-2 min-h-20 rounded-[8px] text-sm" disabled={cancelBusy} /></div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]" disabled={cancelBusy}>保留预约</Button></DialogClose><Button type="button" variant="destructive" className="h-11 rounded-[8px]" onClick={confirmCancel} disabled={cancelBusy}>{cancelBusy ? <LoaderCircle className="size-4 animate-spin" /> : null}{cancelBusy ? '正在取消' : '确认取消'}</Button></DialogFooter></DialogContent>
      </Dialog>

      <Dialog open={convertOpen} onOpenChange={open => !convertBusy && setConvertOpen(open)}>
        <DialogContent className="prototype-dialog rounded-[12px] p-6" showCloseButton={false}><DialogHeader><div className="mb-2 grid size-11 place-items-center rounded-[8px] bg-primary/10 text-primary"><ReceiptText className="size-5" /></div><DialogTitle className="text-left text-xl">确认转为订单</DialogTitle><DialogDescription className="text-left text-sm leading-6">{reservation.service} · 指定 {reservation.provider}。{quoteAmount === 0 ? '本单应付为 0，无需扣款' : `将支付 ${formatWaveAmount(quoteAmount)} 浪花`}，订单会立即指派给该陪玩。</DialogDescription></DialogHeader><div className="rounded-[8px] border bg-muted/35 p-4"><DetailRow label="预约时间" value={reservation.startText} /><DetailRow label="服务数量" value={`${reservation.rounds} 份`} /><DetailRow label="当前应付" value={`${formatWaveAmount(quoteAmount)} 浪花`} /><p className="mt-3 text-xs leading-5 text-muted-foreground">成单时将再次核对金额与服务快照；如有变化，需要重新报价。</p></div><DialogFooter className="grid grid-cols-2 gap-3"><DialogClose asChild><Button type="button" variant="outline" className="h-11 rounded-[8px]" disabled={convertBusy}>再核对一下</Button></DialogClose><Button type="button" className="h-11 rounded-[8px]" onClick={confirmConvert} disabled={convertBusy}>{convertBusy ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}{convertBusy ? '正在成单' : '确认成单'}</Button></DialogFooter></DialogContent>
      </Dialog>
    </PhoneFrame>
  )
}

function DetailSection({ title, icon: Icon, action, children }: { title: string; icon: typeof UserRound; action?: ReactNode; children: ReactNode }) {
  return <section className="rounded-[12px] border bg-card p-4 shadow-sm md:p-5"><div className="mb-4 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Icon className="size-4 text-primary" /><h2 className="text-base font-semibold">{title}</h2></div>{action}</div>{children}</section>
}
function DetailRow({ label, value }: { label: string; value: string }) {
  return <div className="flex min-h-11 items-center justify-between gap-3 border-b py-2 text-sm last:border-0"><span className="shrink-0 text-muted-foreground">{label}</span><span className="break-all text-right">{value}</span></div>
}
function GuestState({ onLogin }: { onLogin: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div className="mx-auto max-w-xl rounded-[12px] border bg-card p-6 text-center shadow-sm md:p-8"><div className="mx-auto grid size-12 place-items-center rounded-[8px] bg-primary/10 text-primary"><CalendarClock className="size-6" /></div><h1 className="mt-4 text-xl font-semibold">登录后查看预约详情</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">确认进度、报价与成单记录均与当前账号关联。</p><Button type="button" className="mt-6 h-12 w-full rounded-[8px]" onClick={onLogin}><LogIn className="size-4" />微信快捷登录</Button></div></section>
}
function ErrorState({ onRetry }: { onRetry: () => void }) {
  return <section className="px-4 pt-4 md:px-6"><div role="alert" className="rounded-[12px] border border-destructive/25 bg-destructive/5 p-6 text-center md:p-8"><AlertCircle className="mx-auto size-8 text-destructive" /><h1 className="mt-4 text-xl font-semibold">预约详情暂时没有加载成功</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">请检查网络后重试。加载失败不会取消预约或生成订单。</p><Button type="button" className="mt-6 h-11 rounded-[8px]" onClick={onRetry}><RefreshCw className="size-4" />重新加载</Button></div></section>
}
