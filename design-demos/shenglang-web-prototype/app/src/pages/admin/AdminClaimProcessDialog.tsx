import { useMemo, useState } from 'react'
import { Check, CircleAlert, Clock3, Eye, FileKey2, Info, LockKeyhole, PackageCheck, RotateCcw, ShieldCheck, Tag, Truck } from 'lucide-react'
import type { AdminClaimsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { adminClaimDetailLabels, adminClaimKindLabels, adminClaimSourceLabels, adminClaimStatusLabels, formatAdminClaimTerms, formatAdminClaimTime, type AdminClaim, type AdminClaimStatus } from '@/pages/admin/admin-claim-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminClaimProcessDialogProps {
  open: boolean
  item: AdminClaim
  scenario: AdminClaimsScenario
  onClose: (resultUnknown?: boolean) => void
  onProcess: (item: AdminClaim, status: AdminClaimStatus, note: string) => void
  onReview: (item: AdminClaim) => void
  onSuspend: (item: AdminClaim, reason: string) => void
}

const conflictMessages: Partial<Record<AdminClaimsScenario, string>> = {
  'details-conflict': '交付资料已更新（CLAIM_DETAILS_CHANGED）。当前处理说明已保留，请关闭后读取最新资料再提交。',
  'status-conflict': '交付状态已变化（CLAIM_STATUS_CHANGED）。当前处理说明已保留，请核对最新处理结果，避免重复流转。',
  'terms-conflict': '权益规格已变化（CLAIM_TERMS_CHANGED）。当前处理说明已保留，请重新核对时长与高亮类型。',
}

export function AdminClaimProcessDialog({ open, item, scenario, onClose, onProcess, onReview, onSuspend }: AdminClaimProcessDialogProps) {
  const initialStatus: AdminClaimStatus = item.status === 'PENDING'
    ? item.kindSnapshot === 'TAG' ? (item.tagRevisionRequired ? 'PENDING' : 'COMPLETED') : 'FULFILLING'
    : 'COMPLETED'
  const [nextStatus, setNextStatus] = useState<AdminClaimStatus>(initialStatus)
  const [note, setNote] = useState('')
  const [tagReviewed, setTagReviewed] = useState(false)
  const [error, setError] = useState('')
  const [resultUnknown, setResultUnknown] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [suspendOpen, setSuspendOpen] = useState(false)
  const [suspendReason, setSuspendReason] = useState('')
  const terminal = item.status === 'COMPLETED' || item.status === 'CANCELLED'
  const readonly = scenario === 'readonly'
  const dirty = !terminal && !readonly && (!!note.trim() || nextStatus !== initialStatus)
  const details = Object.entries(item.details).filter(([, value]) => value)
  const availableStatuses = useMemo(() => {
    if (terminal) return []
    const values: AdminClaimStatus[] = item.status === 'PENDING' ? ['FULFILLING', 'COMPLETED'] : ['PENDING', 'COMPLETED']
    if (item.status === 'PENDING' && item.kindSnapshot === 'TAG') values.unshift('PENDING')
    if (item.source === 'REDEMPTION') values.push('CANCELLED')
    return values
  }, [item, terminal])

  function statusLabel(status: AdminClaimStatus) {
    if (status === 'PENDING') return item.kindSnapshot === 'TAG' ? '退回修改标签' : '退回补充资料'
    if (status === 'CANCELLED') return '取消并退回积分'
    return adminClaimStatusLabels[status]
  }

  function requestClose() {
    if (dirty && !resultUnknown) setDiscardOpen(true)
    else onClose(resultUnknown)
  }

  function submit() {
    setError('')
    if (!note.trim()) { setError('请填写交付说明、凭证或退回原因。'); return }
    if (note.trim().length > 255) { setError('处理说明最多 255 字。'); return }
    if (item.kindSnapshot === 'TAG' && item.status === 'PENDING' && nextStatus === 'PENDING' && note.trim() === item.deliveryNote.trim()) {
      setError('再次退回 TAG 时，请填写新的修改要求，不能沿用上次说明。'); return
    }
    if (item.kindSnapshot === 'TAG' && nextStatus === 'COMPLETED' && !tagReviewed) {
      setError('请先确认已审核 TAG 内容符合要求。'); return
    }
    if (scenario === 'save-review') { setResultUnknown(true); return }
    if (scenario === 'save-error') { setError('处理未完成：请求被拒绝，请核对内容与 engagement:fulfill 权限。当前输入已保留，可修正后重试。'); return }
    const conflict = conflictMessages[scenario]
    if (conflict) { setError(conflict); return }
    onProcess(item, nextStatus, note.trim())
  }

  function requestSubmit() {
    if (nextStatus === 'CANCELLED') setCancelOpen(true)
    else submit()
  }

  function review() {
    if (!tagReviewed) return
    onReview(item)
  }

  function suspend() {
    const reason = suspendReason.trim()
    if (reason.length < 2) { setError('暂停原因至少填写 2 个字。'); return }
    onSuspend(item, reason)
  }

  return <>
    <Dialog open={open} onOpenChange={value => { if (!value) requestClose() }}>
      <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-[1120px]" onInteractOutside={event => event.preventDefault()}>
        <DialogHeader>
          <div className="flex flex-wrap items-center gap-2"><DialogTitle>权益交付 #{item.id}</DialogTitle><Badge variant="outline">{adminClaimStatusLabels[item.status]}</Badge></div>
          <DialogDescription>{item.nameSnapshot} · {adminClaimSourceLabels[item.source]} · {adminClaimKindLabels[item.kindSnapshot]}</DialogDescription>
        </DialogHeader>

        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <section className="rounded-xl border bg-muted/15 p-4">
              <div className="flex items-center gap-2"><FileKey2 className="size-4 text-primary" /><h3 className="text-sm font-semibold">历史权益快照</h3><Badge variant="secondary" className="text-[10px]">不随商品配置改写</Badge></div>
              <dl className="mt-4 grid gap-x-5 gap-y-4 sm:grid-cols-2">
                <div><dt className="text-[11px] text-muted-foreground">权益名称</dt><dd className="mt-1 text-sm font-medium">{item.nameSnapshot}</dd></div>
                <div><dt className="text-[11px] text-muted-foreground">申请人</dt><dd className="mt-1 text-sm font-medium">{item.accountType === 'BOSS' ? '老板' : '陪玩'} · {item.accountName}<span className="ml-2 font-mono text-[11px] font-normal text-muted-foreground">#{item.account}</span></dd></div>
                <div><dt className="text-[11px] text-muted-foreground">来源</dt><dd className="mt-1 text-sm">{adminClaimSourceLabels[item.source]}{item.sourceOrder ? ` · 订单 #${item.sourceOrder}` : ''}</dd></div>
                <div><dt className="text-[11px] text-muted-foreground">创建时间</dt><dd className="mt-1 text-sm tabular-nums">{formatAdminClaimTime(item.createdAt)}</dd></div>
                <div><dt className="text-[11px] text-muted-foreground">权益时长</dt><dd className="mt-1 text-sm">{formatAdminClaimTerms(item)}</dd></div>
                <div><dt className="text-[11px] text-muted-foreground">展示类型</dt><dd className="mt-1 text-sm">{item.kindSnapshot === 'TAG' ? item.highlighted ? '高亮 TAG' : '普通 TAG' : adminClaimKindLabels[item.kindSnapshot]}</dd></div>
                {item.pointsSpent > 0 && <div><dt className="text-[11px] text-muted-foreground">兑换积分</dt><dd className="mt-1 text-sm font-medium tabular-nums">{item.pointsSpent} 积分{item.pointsReturned && <span className="ml-2 text-success">已退回</span>}</dd></div>}
                {item.balanceAmount > 0 && <div><dt className="text-[11px] text-muted-foreground">到账余额</dt><dd className="mt-1 text-sm font-medium">{formatWaveAmount(item.balanceAmount / 10)} 浪花</dd></div>}
                {item.expiresAt && <div><dt className="text-[11px] text-muted-foreground">有效期至</dt><dd className="mt-1 text-sm tabular-nums">{formatAdminClaimTime(item.expiresAt)}</dd></div>}
                {item.completedAt && <div><dt className="text-[11px] text-muted-foreground">完成时间</dt><dd className="mt-1 text-sm tabular-nums">{formatAdminClaimTime(item.completedAt)}</dd></div>}
              </dl>
            </section>

            <section className="rounded-xl border p-4">
              <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><LockKeyhole className="size-4 text-primary" /><h3 className="text-sm font-semibold">私密交付资料</h3></div><Badge variant="outline" className="text-[10px]"><Eye className="size-3" />权限内可见</Badge></div>
              {details.length ? <dl className="mt-4 grid gap-3 sm:grid-cols-2">{details.map(([key, value]) => <div key={key} className={cn('rounded-lg bg-muted/30 px-3 py-2.5', ['address', 'note'].includes(key) && 'sm:col-span-2')}><dt className="text-[11px] text-muted-foreground">{adminClaimDetailLabels[key] || key}</dt><dd className="mt-1 break-words text-sm leading-5">{value}</dd></div>)}</dl> : <p className="mt-4 rounded-lg border border-dashed p-4 text-center text-xs text-muted-foreground">无需补充交付资料</p>}
              <p className="mt-3 flex items-start gap-2 text-[11px] leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-3.5 shrink-0" />资料仅用于本次权益交付，不进入公开播报或动态。</p>
            </section>

            <section className="rounded-xl border p-4">
              <div className="flex items-center gap-2"><Clock3 className="size-4 text-primary" /><h3 className="text-sm font-semibold">处理轨迹</h3><span className="text-[10px] text-muted-foreground">本地示例</span></div>
              <div className="mt-4 space-y-0">{item.timeline.map((event, index) => <div key={event.id} className="relative grid grid-cols-[18px_minmax(0,1fr)] gap-3 pb-5 last:pb-0"><div className="flex flex-col items-center"><span className={cn('mt-1 size-2 rounded-full ring-4 ring-background', index === 0 ? 'bg-primary' : 'bg-muted-foreground/40')} />{index < item.timeline.length - 1 && <span className="h-full w-px bg-border" />}</div><div><div className="flex flex-wrap items-center gap-2"><p className="text-xs font-semibold">{adminClaimStatusLabels[event.status]}</p><span className="text-[10px] text-muted-foreground">{event.operator} · {formatAdminClaimTime(event.at)}</span></div><p className="mt-1.5 text-xs leading-5 text-muted-foreground">{event.note}</p></div></div>)}</div>
            </section>
          </div>

          <aside className="min-w-0 space-y-4 xl:sticky xl:top-0">
            {readonly && <section className="rounded-xl border bg-muted/15 p-4"><div className="flex items-center gap-2"><LockKeyhole className="size-4 text-primary" /><h3 className="text-sm font-semibold">仅查看交付记录</h3></div><p className="mt-2 text-xs leading-5 text-muted-foreground">当前可核对历史快照和私密资料，但不能变更状态、取消兑换或管理 TAG。</p></section>}
            {!terminal && !readonly && <section className="rounded-xl border p-4">
              <div className="flex items-center gap-2"><PackageCheck className="size-4 text-primary" /><h3 className="text-sm font-semibold">处理本次交付</h3></div>
              <p className="mt-2 text-[11px] leading-5 text-muted-foreground">提交时校验当前资料、状态和权益规格，防止覆盖他人的处理。</p>
              <fieldset disabled={resultUnknown} className="mt-4 space-y-4">
                <div className="space-y-2"><Label htmlFor="claim-next-status">处理结果</Label><Select value={nextStatus} onValueChange={value => { setNextStatus(value as AdminClaimStatus); setError('') }}><SelectTrigger id="claim-next-status" className="w-full"><SelectValue /></SelectTrigger><SelectContent>{availableStatuses.map(status => <SelectItem key={status} value={status} disabled={item.kindSnapshot === 'TAG' && item.tagRevisionRequired && ['FULFILLING', 'COMPLETED'].includes(status)}>{statusLabel(status)}</SelectItem>)}</SelectContent></Select></div>
                {item.kindSnapshot === 'TAG' && item.tagRevisionRequired && <Alert><RotateCcw className="size-4" /><AlertDescription>正在等待用户修改 TAG，仅可补充新的退回要求；资料更新前不能交付或完成。</AlertDescription></Alert>}
                {nextStatus === 'CANCELLED' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>取消后不可在本记录上继续交付</AlertTitle><AlertDescription>仅积分兑换支持此操作：退回 {item.pointsSpent} 积分，并恢复对应商品库存。</AlertDescription></Alert>}
                {item.kindSnapshot === 'TAG' && nextStatus === 'COMPLETED' && <label className="flex cursor-pointer items-start gap-3 rounded-lg border p-3"><Checkbox checked={tagReviewed} onCheckedChange={value => setTagReviewed(value === true)} aria-label="确认已审核 TAG 内容" /><span className="text-xs leading-5">已审核 TAG「{item.details.tag || '未填写'}」内容符合俱乐部要求及相关规定</span></label>}
                <div className="space-y-2"><div className="flex items-center justify-between gap-2"><Label htmlFor="claim-process-note">交付凭证 / 处理说明</Label><span className="text-[10px] text-muted-foreground">{note.length} / 255</span></div><Textarea id="claim-process-note" rows={5} maxLength={255} value={note} onChange={event => { setNote(event.target.value); setError('') }} placeholder={nextStatus === 'CANCELLED' ? '填写取消原因与积分退回说明' : nextStatus === 'PENDING' ? '说明需要补充或修改的资料' : '填写快递单号、交付记录编号或服务安排'} /><p className="text-[11px] leading-5 text-muted-foreground">必填。老板可在权益详情查看处理进度与说明。</p></div>
              </fieldset>
            </section>}

            {terminal && <section className="rounded-xl border bg-muted/15 p-4"><div className="flex items-center gap-2"><Check className="size-4 text-success" /><h3 className="text-sm font-semibold">交付记录已归档</h3></div><p className="mt-2 text-xs leading-5 text-muted-foreground">{item.status === 'CANCELLED' ? '本记录已取消，不能恢复为待处理或交付中。' : '本记录已完成，历史快照和处理说明保持只读。'}</p>{item.deliveryNote && <div className="mt-3 rounded-lg border bg-card p-3"><p className="text-[11px] text-muted-foreground">最新处理说明</p><p className="mt-1.5 text-xs leading-5">{item.deliveryNote}</p></div>}</section>}

            {!readonly && item.kindSnapshot === 'TAG' && item.status === 'COMPLETED' && <section className="rounded-xl border p-4">
              <div className="flex items-center gap-2"><Tag className="size-4 text-primary" /><h3 className="text-sm font-semibold">TAG 展示管理</h3></div>
              {item.tagExpired ? <Alert className="mt-3"><Info className="size-4" /><AlertDescription>标签已到期，不再展示，也无法继续审核或暂停。</AlertDescription></Alert> : item.tagReviewed === false ? <div className="mt-3 space-y-3"><Alert><CircleAlert className="size-4" /><AlertDescription>{item.tagSuspensionReason ? `标签已暂停展示：${item.tagSuspensionReason}` : '历史标签尚无有效审核记录，暂不展示；请核对后复核。'}</AlertDescription></Alert><label className="flex cursor-pointer items-start gap-3 rounded-lg border p-3"><Checkbox checked={tagReviewed} onCheckedChange={value => setTagReviewed(value === true)} aria-label="确认复核历史 TAG" /><span className="text-xs leading-5">已复核当前 TAG 内容，确认恢复或继续展示</span></label><Button className="w-full" disabled={!tagReviewed} onClick={review}><ShieldCheck className="size-4" />确认审核</Button></div> : <div className="mt-3 space-y-3"><Alert className="border-success/30 bg-success/10"><ShieldCheck className="size-4 text-success" /><AlertDescription>TAG 已审核并处于可展示状态。暂停后记录保留，并进入待复核。</AlertDescription></Alert>{suspendOpen ? <div className="space-y-2"><Label htmlFor="claim-suspend-reason">暂停原因</Label><Textarea id="claim-suspend-reason" rows={3} maxLength={255} value={suspendReason} onChange={event => { setSuspendReason(event.target.value); setError('') }} placeholder="至少 2 个字，说明暂停展示的原因" /><div className="grid grid-cols-2 gap-2"><Button variant="outline" onClick={() => setSuspendOpen(false)}>返回</Button><Button variant="destructive" disabled={suspendReason.trim().length < 2} onClick={suspend}>确认暂停</Button></div></div> : <Button variant="outline" className="w-full" onClick={() => setSuspendOpen(true)}>暂停展示</Button>}</div>}
            </section>}

            {(error || resultUnknown) && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>{resultUnknown ? '处理结果待核对' : '本次操作未提交'}</AlertTitle><AlertDescription>{resultUnknown ? '请求结果未确认，当前输入已保留。请关闭后查询本记录，核实前不会重复提交。' : error}</AlertDescription></Alert>}
            <Alert className="border-primary/20 bg-primary/5"><Truck className="size-4 text-primary" /><AlertDescription>演示操作仅更新本地示例，不发送通知、不退积分、不改变真实库存或标签。</AlertDescription></Alert>
          </aside>
        </div>
        <DialogFooter><Button variant="outline" onClick={requestClose}>{resultUnknown ? '关闭并核对' : dirty ? '取消' : '关闭'}</Button>{!terminal && !readonly && <Button disabled={resultUnknown || !note.trim() || (item.kindSnapshot === 'TAG' && nextStatus === 'COMPLETED' && !tagReviewed)} onClick={requestSubmit}>{nextStatus === 'CANCELLED' ? '核对取消影响' : '提交处理结果'}</Button>}</DialogFooter>
      </DialogContent>
    </Dialog>

    <AlertDialog open={discardOpen} onOpenChange={setDiscardOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>放弃未保存的处理内容？</AlertDialogTitle><AlertDialogDescription>处理结果与说明尚未提交，放弃后将恢复记录 #{item.id} 当前状态。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续处理</AlertDialogCancel><AlertDialogAction onClick={() => { setDiscardOpen(false); onClose() }}>放弃修改</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={cancelOpen} onOpenChange={setCancelOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>确认取消积分兑换 #{item.id}？</AlertDialogTitle><AlertDialogDescription>将向 {item.accountName} 退回 {item.pointsSpent} 积分，并恢复「{item.nameSnapshot}」商品库存。本记录随后进入已取消终态。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>返回核对</AlertDialogCancel><AlertDialogAction onClick={() => { setCancelOpen(false); submit() }}>确认取消并退回</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </>
}
