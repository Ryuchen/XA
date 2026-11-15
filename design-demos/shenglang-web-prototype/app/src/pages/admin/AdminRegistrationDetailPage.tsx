import { useState } from 'react'
import {
  ArrowLeft,
  BadgeCheck,
  CheckCircle2,
  CircleAlert,
  CreditCard,
  Eye,
  FileImage,
  Info,
  LockKeyhole,
  ReceiptText,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
  UserRound,
  X,
  ZoomIn,
} from 'lucide-react'
import type { AdminRegistrationDetailScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import {
  adminDepositModeLabel,
  adminRegistrationLevels,
  adminRegistrationStatusLabel,
  type AdminRegistration,
  type AdminRegistrationStatus,
} from '@/pages/admin/admin-registration-data'
import { cn } from '@/lib/utils'

interface AdminRegistrationDetailPageProps {
  registration: AdminRegistration
  scenario: AdminRegistrationDetailScenario
  onScenarioChange: (scenario: AdminRegistrationDetailScenario) => void
  onReviewed: (registration: AdminRegistration) => void
  onBack: () => void
}

type DocumentKey = 'front' | 'back' | 'proof'

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="grid gap-1 border-b py-3 last:border-0 sm:grid-cols-[132px_minmax(0,1fr)] sm:gap-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="min-w-0 break-words text-sm leading-6">{children}</dd></div>
}

function DocumentArtwork({ type, registration, large = false }: { type: DocumentKey; registration: AdminRegistration; large?: boolean }) {
  if (type === 'proof') return <div className={cn('relative flex w-full flex-col overflow-hidden rounded-lg border bg-[#f5f2e9] p-4 text-[#24334c] shadow-sm', large ? 'min-h-[360px] max-w-md' : 'h-40')}>
    <div className="flex items-center justify-between border-b border-[#24334c]/15 pb-3"><ReceiptText className={large ? 'size-8' : 'size-5'} /><span className="text-xs font-semibold tracking-widest">押金交付凭证</span></div>
    <div className="mt-4 space-y-3">{[`申请编号 #${registration.id}`, '交付方式　现金缴纳', `凭证金额 ¥ ${registration.depositRequired}.00`, `交付时间 ${registration.submittedAt.slice(0, 10)}`].map(text => <p key={text} className={cn('border-b border-dashed border-[#24334c]/15 pb-2 font-mono', large ? 'text-sm' : 'text-[9px]')}>{text}</p>)}</div>
    <span className="absolute bottom-4 right-4 rotate-[-10deg] rounded border-2 border-[#b45757]/50 px-2 py-1 text-xs font-semibold text-[#b45757]/60">示例凭证</span>
  </div>
  return <div className={cn('relative w-full overflow-hidden rounded-lg border bg-gradient-to-br from-[#dbe8eb] to-[#b8d0d6] p-4 text-[#254452] shadow-sm', large ? 'min-h-[300px]' : 'h-40')}>
    <div className="flex items-center justify-between"><span className={cn('font-semibold tracking-wider', large ? 'text-base' : 'text-[10px]')}>{type === 'front' ? '居民身份证 · 人像面' : '居民身份证 · 国徽面'}</span><CreditCard className={large ? 'size-8' : 'size-5'} /></div>
    {type === 'front' ? <div className="mt-4 flex gap-4"><div className={cn('grid shrink-0 place-items-center rounded bg-white/45', large ? 'h-36 w-28' : 'h-20 w-16')}><UserRound className={large ? 'size-16' : 'size-9'} /></div><div className="flex-1 space-y-2">{[86, 70, 92, 62, 80].map((width, index) => <div key={index} className={cn('rounded bg-[#254452]/15', large ? 'h-3' : 'h-1.5')} style={{ width: `${width}%` }} />)}</div></div> : <div className="grid place-items-center py-4"><ShieldCheck className={large ? 'size-24 text-[#8b5550]/55' : 'size-14 text-[#8b5550]/55'} /><div className={cn('mt-3 w-4/5 rounded bg-[#254452]/15', large ? 'h-3' : 'h-1.5')} /></div>}
    <span className="absolute bottom-2 right-3 text-[9px] font-medium tracking-widest text-[#254452]/55">示例材料 · 非真实证件</span>
  </div>
}

export function AdminRegistrationDetailPage({ registration, scenario, onScenarioChange, onReviewed, onBack }: AdminRegistrationDetailPageProps) {
  const initialStatus: AdminRegistrationStatus = scenario === 'approved' ? 'APPROVED' : scenario === 'rejected' ? 'REJECTED' : registration.status
  const [status, setStatus] = useState<AdminRegistrationStatus>(initialStatus)
  const [levelId, setLevelId] = useState(registration.levelName ? String(adminRegistrationLevels.find(item => item.name === registration.levelName)?.id || '') : '')
  const [levelName, setLevelName] = useState(registration.levelName)
  const [depositAmount, setDepositAmount] = useState(String(registration.depositRequired))
  const [depositPaid, setDepositPaid] = useState(registration.depositPaid)
  const [identityConfirmed, setIdentityConfirmed] = useState(false)
  const [depositConfirmed, setDepositConfirmed] = useState(false)
  const [reviewNote, setReviewNote] = useState(registration.reviewNote)
  const [reviewError, setReviewError] = useState('')
  const [action, setAction] = useState<'approve' | 'reject' | null>(null)
  const [preview, setPreview] = useState<DocumentKey | null>(null)
  const [reviewChecked, setReviewChecked] = useState(false)
  const [reviewResolved, setReviewResolved] = useState(false)
  const [conflictResolved, setConflictResolved] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [events, setEvents] = useState([
    ...(registration.reviewedAt ? [{ title: registration.status === 'APPROVED' ? '审核通过并开通账号' : '退回补充资料', note: `${registration.reviewerName} · ${registration.reviewNote}`, time: registration.reviewedAt }] : []),
    { title: registration.revision > 1 ? '重新提交申请资料' : '提交入驻申请', note: `${adminDepositModeLabel[registration.depositMode]} · 资料版本 v${registration.revision}`, time: registration.submittedAt },
  ])

  const reviewPending = scenario === 'review' && !reviewResolved
  const conflictPending = scenario === 'conflict' && !conflictResolved
  const readOnly = scenario === 'readonly'
  const locked = readOnly || scenario === 'refresh-error' || reviewPending || conflictPending
  const isPending = status === 'PENDING'
  const cash = registration.depositMode === 'CASH'
  const parsedDeposit = Number(depositAmount)
  const depositHasCentPrecision = Math.abs(parsedDeposit * 100 - Math.round(parsedDeposit * 100)) < 1e-7
  const canApprove = !readOnly && !!levelId && identityConfirmed && (!cash || (depositConfirmed && Number.isFinite(parsedDeposit) && depositHasCentPrecision && parsedDeposit >= registration.depositRequired && parsedDeposit <= 100000))
  const documents: { key: DocumentKey; label: string; available: boolean }[] = [
    { key: 'front', label: '身份证正面（人像面）', available: registration.idFrontAvailable },
    { key: 'back', label: '身份证反面（国徽面）', available: registration.idBackAvailable },
    ...(cash ? [{ key: 'proof' as const, label: '押金交付凭证', available: registration.depositProofAvailable }] : []),
  ]

  function requestReject() {
    if (!reviewNote.trim()) { setReviewError('请填写需要补充的资料和原因'); return }
    setReviewError('')
    setAction('reject')
  }

  function confirmReview() {
    if (!action) return
    const reviewedAt = '2026-11-04 17:36'
    if (action === 'approve') {
      const selectedLevel = adminRegistrationLevels.find(item => String(item.id) === levelId)
      if (!selectedLevel || !canApprove) return
      setStatus('APPROVED')
      setLevelName(selectedLevel.name)
      setDepositPaid(cash ? parsedDeposit : 0)
      setEvents(items => [{ title: '审核通过并开通账号', note: `审核管理员 · 开通${selectedLevel.name}${cash ? ` · 登记押金 ${parsedDeposit} 元` : ' · 单抵不登记现金'}`, time: reviewedAt }, ...items])
      onReviewed({ ...registration, status: 'APPROVED', levelName: selectedLevel.name, depositPaid: cash ? parsedDeposit : 0, reviewNote: reviewNote.trim(), reviewerName: '审核管理员 · 岚', reviewedAt })
      setFeedback('账号已在原型中通过审核；可使用入驻绑定的微信快捷登录，首次登录可选择开启二次验证，未创建真实账号。')
    } else {
      setStatus('REJECTED')
      setEvents(items => [{ title: '退回补充资料', note: `审核管理员 · ${reviewNote.trim()}`, time: reviewedAt }, ...items])
      onReviewed({ ...registration, status: 'REJECTED', levelName: '', depositPaid: 0, reviewNote: reviewNote.trim(), reviewerName: '审核管理员 · 岚', reviewedAt })
      setFeedback('申请已在原型中退回，申请人可按审核意见补充资料后重新提交。')
    }
    setAction(null)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="注册申请详情加载中"><Skeleton className="h-52 rounded-xl" /><div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_380px]"><Skeleton className="h-[720px] rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">申请资料加载失败</CardTitle><CardDescription className="mt-2">请重新加载，或返回列表核对申请状态。</CardDescription><div className="mt-6 flex gap-2"><Button variant="outline" onClick={onBack}><ArrowLeft className="size-4" />返回列表</Button><Button onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></div></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="flex flex-wrap items-center gap-3"><Button variant="ghost" onClick={onBack}><ArrowLeft className="size-4" />返回注册审核</Button><span className="ml-auto font-mono text-xs text-muted-foreground">申请 #{registration.id} · v{registration.revision}</span></section>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>申请更新失败，当前显示上次结果</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">重新读取前已锁定审核操作。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {reviewPending && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>审核结果未确认</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">原请求可能仍在处理中，请读取最新申请与押金记录后再继续。<Button variant="link" className="h-auto p-0" onClick={() => { setReviewChecked(true); setFeedback('已读取原型中的最新申请，请核对当前状态。') }}>重新读取申请</Button>{reviewChecked && <Button variant="link" className="h-auto p-0" onClick={() => setReviewResolved(true)}>已核对，解除限制</Button>}</AlertDescription></Alert>}
    {conflictPending && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>申请已被其他审核人处理</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">本次审核未提交，请重新读取最新状态。<Button variant="link" className="h-auto p-0" onClick={() => { setConflictResolved(true); onScenarioChange(registration.depositMode === 'EARNINGS' ? 'earnings' : 'default') }}>重新读取</Button></AlertDescription></Alert>}

    <Card className="overflow-hidden border-[#10285b] bg-[#10285b] text-white"><CardContent className="grid gap-6 py-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="flex min-w-0 items-center gap-4"><span className="grid size-20 shrink-0 place-items-center rounded-2xl border border-white/15 bg-white/10 text-2xl font-semibold">{registration.displayName.slice(0, 1)}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="text-2xl font-semibold tracking-tight">{registration.displayName}</h1><Badge className={cn('border-white/20 text-white hover:bg-white/10', status === 'PENDING' ? 'bg-amber-300/15 text-amber-100' : status === 'APPROVED' ? 'bg-sky-300/15 text-sky-100' : 'bg-rose-300/15 text-rose-100')}>{adminRegistrationStatusLabel[status]}</Badge></div><p className="mt-2 font-mono text-sm text-blue-100/75">#{registration.id} · {registration.username}</p><p className="mt-2 text-sm text-blue-100/80">{registration.realName} · {registration.phone}</p></div></div>
      <dl className="grid grid-cols-3 gap-5 lg:min-w-[360px]">{[[adminDepositModeLabel[registration.depositMode], '押金方式'], [`${registration.depositRequired} 元`, '当前应缴'], [registration.submittedAt.slice(5, 16), '提交时间']].map(([value, label]) => <div key={label} className="border-l border-white/15 pl-4"><dt className="text-[11px] text-blue-100/65">{label}</dt><dd className="mt-2 text-sm font-semibold">{value}</dd></div>)}</dl>
    </CardContent></Card>

    <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_380px]">
      <div className="flex min-w-0 flex-col gap-4">
        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><UserRound className="size-4 text-primary" />申请信息</CardTitle><CardDescription>陪玩账号、结单昵称与实名申报资料</CardDescription></CardHeader><CardContent><dl>
          <DetailRow label="陪玩账号"><span className="font-mono">{registration.username}</span></DetailRow>
          <DetailRow label="结单昵称">{registration.displayName}<span className="ml-2 text-xs text-muted-foreground">用于接单与报单展示</span></DetailRow>
          <DetailRow label="真实姓名">{registration.realName}</DetailRow>
          <DetailRow label="身份证号"><span className="font-mono">{registration.idNumber}</span><Badge variant="outline" className="ml-2 text-[10px]">示例脱敏</Badge></DetailRow>
          <DetailRow label="手机号">{registration.phone}</DetailRow>
          <DetailRow label="押金方式">{adminDepositModeLabel[registration.depositMode]}<span className="ml-2 text-xs text-muted-foreground">{cash ? '须核实实际到账与凭证一致' : '从后续订单净收益抵扣，本次不登记现金'}</span></DetailRow>
          <DetailRow label="应缴押金"><strong>{registration.depositRequired} 元</strong><span className="ml-2 text-xs text-muted-foreground">{cash ? '按实际到账登记' : '单抵目标，不是本次现金到账'}</span></DetailRow>
          {status === 'APPROVED' && <DetailRow label="开通等级"><strong>{levelName || '未记录'}</strong></DetailRow>}
          {status === 'APPROVED' && cash && <DetailRow label="登记现金押金"><strong>{depositPaid} 元</strong></DetailRow>}
          {(registration.reviewedAt || status !== 'PENDING') && <><DetailRow label="审核人">{registration.reviewerName || '审核管理员 · 岚'}</DetailRow><DetailRow label="审核时间"><span className="tabular-nums">{registration.reviewedAt || '2026-11-04 17:36'}</span></DetailRow></>}
          {reviewNote && status !== 'PENDING' && <DetailRow label="审核意见"><span className="whitespace-pre-wrap">{reviewNote}</span></DetailRow>}
        </dl></CardContent></Card>

        <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><FileImage className="size-4 text-primary" />核对材料</CardTitle><CardDescription>{documents.length} 份申请附件 · 点击放大核对 · 示例材料不含真实身份信息</CardDescription></CardHeader><CardContent><div className={cn('grid gap-3', cash ? 'md:grid-cols-3' : 'md:grid-cols-2')}>
          {documents.map(document => <section key={document.key} className="min-w-0 rounded-xl border bg-muted/20 p-3"><div className="mb-3 flex min-h-8 items-start justify-between gap-2"><div><h3 className="text-xs font-medium leading-5">{document.label}</h3><p className={cn('mt-1 text-[11px]', document.available ? 'text-success' : 'text-destructive')}>{document.available ? '材料可读取' : '照片不可用，请重新读取'}</p></div>{document.available && <Button variant="ghost" size="icon" className="size-8 shrink-0" aria-label={`放大${document.label}`} onClick={() => setPreview(document.key)}><ZoomIn className="size-4" /></Button>}</div>{document.available ? <button type="button" className="group relative block w-full text-left" onClick={() => setPreview(document.key)} aria-label={`查看${document.label}`}><DocumentArtwork type={document.key} registration={registration} /><span className="absolute inset-0 grid place-items-center rounded-lg bg-[#10285b]/0 text-white opacity-0 transition group-hover:bg-[#10285b]/35 group-hover:opacity-100"><Eye className="size-6" /></span></button> : <div className="grid h-40 place-items-center rounded-lg border border-dashed bg-muted/30 p-4 text-center text-xs text-muted-foreground"><span><CircleAlert className="mx-auto mb-2 size-5" />材料读取失败</span></div>}</section>)}
        </div><Alert className="mt-4"><LockKeyhole className="size-4" /><AlertDescription>证件照片属于私密材料。原型使用示意图片，不上传、不请求也不保存任何真实证件。</AlertDescription></Alert></CardContent></Card>

        <Card><CardHeader><CardTitle className="text-base">申请与审核记录</CardTitle><CardDescription>每次重新提交生成新的资料版本</CardDescription></CardHeader><CardContent><ol>{events.map((event, index) => <li key={`${event.title}-${index}`} className="relative border-l pb-5 pl-5 last:pb-0"><span className="absolute -left-[5px] top-1 size-2.5 rounded-full border-2 border-background bg-primary" /><div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-sm font-medium">{event.title}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">{event.note}</p></div><time className="text-[11px] text-muted-foreground">{event.time}</time></div></li>)}</ol></CardContent></Card>
      </div>

      <aside className="flex min-w-0 flex-col gap-4 xl:sticky xl:top-[168px]">
        {readOnly && isPending ? <Card className="border-primary/20"><CardHeader className="border-b bg-primary/[0.04]"><CardTitle className="flex items-center gap-2 text-base"><Eye className="size-4 text-primary" />仅查看申请</CardTitle><CardDescription>当前角色可核对资料，但没有审核处理权限</CardDescription></CardHeader><CardContent><Alert><LockKeyhole className="size-4" /><AlertTitle>审核操作已隐藏</AlertTitle><AlertDescription>证件、申报信息与材料仍可查看；需由具备注册审核权限的成员处理，不会在只读视图显示可编辑表单。</AlertDescription></Alert></CardContent></Card> : isPending ? <Card className="border-primary/25 shadow-md"><CardHeader className="border-b bg-primary/[0.04]"><CardTitle className="flex items-center gap-2 text-base"><UserCheck className="size-4 text-primary" />审核处理</CardTitle><CardDescription>先核对材料，再选择审核结果</CardDescription></CardHeader><CardContent className="space-y-4">
          <div className="space-y-2"><Label>开通后的陪玩等级</Label><Select value={levelId} disabled={locked} onValueChange={setLevelId}><SelectTrigger className="h-11 w-full" aria-label="开通后的陪玩等级"><SelectValue placeholder="选择有效陪玩等级" /></SelectTrigger><SelectContent>{adminRegistrationLevels.map(level => <SelectItem key={level.id} value={String(level.id)}>{level.name}</SelectItem>)}</SelectContent></Select><p className="text-[11px] text-muted-foreground">选项来自启用中的等级配置，此处为示例。</p></div>
          {cash && <div className="space-y-2"><Label htmlFor="registration-deposit">已确认到账的现金押金（元）</Label><Input id="registration-deposit" type="number" min={registration.depositRequired} max={100000} step="0.01" value={depositAmount} disabled={locked} onChange={event => setDepositAmount(event.target.value)} className="h-11" /><p className="text-[11px] text-muted-foreground">不得低于当前应缴 {registration.depositRequired} 元。</p></div>}
          <div className="space-y-2"><label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border p-3 text-xs leading-5"><Checkbox checked={identityConfirmed} disabled={locked} onCheckedChange={value => setIdentityConfirmed(value === true)} className="mt-0.5" />已核对身份证正反面、姓名与号码</label>{cash && <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border p-3 text-xs leading-5"><Checkbox checked={depositConfirmed} disabled={locked} onCheckedChange={value => setDepositConfirmed(value === true)} className="mt-0.5" />已核实押金实际到账与交付凭证一致</label>}</div>
          <div className="space-y-2"><div className="flex items-center justify-between"><Label htmlFor="registration-note">审核意见</Label><span className="text-[10px] text-muted-foreground">{reviewNote.length} / 500</span></div><Textarea id="registration-note" value={reviewNote} disabled={locked} onChange={event => { setReviewNote(event.target.value); setReviewError('') }} maxLength={500} placeholder="退回补充资料时，必须填写需要修改的内容" className="min-h-24" /></div>
          <Alert><Info className="size-4" /><AlertDescription>{cash ? '通过后登记现金押金并开通账号；使用入驻绑定的微信快捷登录，首次登录可选择开启二次验证。' : '单抵从后续订单净收益抵扣，本次不登记现金押金。'}</AlertDescription></Alert>
          {reviewError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{reviewError}</AlertDescription></Alert>}
          <div className="grid gap-2 border-t pt-4"><Button className="h-11 w-full" disabled={!canApprove || locked} onClick={() => { setReviewError(''); setAction('approve') }}><BadgeCheck className="size-4" />通过并开通账号</Button><Button variant="outline" className="h-11 w-full" disabled={locked} onClick={requestReject}><RotateCcw className="size-4" />退回补充资料</Button>{!canApprove && <p className="text-center text-[11px] leading-5 text-muted-foreground">选择等级并完成{cash ? '实名、押金' : '实名'}核对后可通过。</p>}</div>
        </CardContent></Card> : <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base">{status === 'APPROVED' ? <CheckCircle2 className="size-4 text-success" /> : <RotateCcw className="size-4 text-warning-foreground" />}{status === 'APPROVED' ? '账号已开通' : '等待补充资料'}</CardTitle><CardDescription>{status === 'APPROVED' ? '本次审核已完成，不可重复处理' : '申请人重新提交后进入待审核队列'}</CardDescription></CardHeader><CardContent>{status === 'APPROVED' ? <dl><DetailRow label="开通等级">{levelName || '未记录'}</DetailRow><DetailRow label="押金结果">{cash ? `登记 ${depositPaid} 元` : '单抵，不登记现金'}</DetailRow><DetailRow label="后续步骤">使用入驻绑定的微信快捷登录，首次登录可选择开启二次验证</DetailRow></dl> : <div className="rounded-xl border border-warning/30 bg-warning/10 p-4"><p className="text-xs text-warning-foreground">需要申请人修改</p><p className="mt-2 text-sm leading-6">{reviewNote}</p></div>}</CardContent></Card>}

        <Card><CardHeader><CardTitle className="text-base">核对清单</CardTitle><CardDescription>审核前逐项确认，不以材料齐全代替人工判断</CardDescription></CardHeader><CardContent><ul className="space-y-3">{[
          ['账号与结单昵称', '可用于登录、接单与报单展示'],
          ['实名证件', '正反面、姓名与号码一致'],
          [cash ? '现金押金' : '单抵方式', cash ? '实际到账与凭证金额一致' : '本次不登记现金到账'],
          ['开通等级', '仅从当前启用等级中选择'],
        ].map(([title, note], index) => <li key={title} className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{index + 1}</span><div><p className="text-xs font-medium">{title}</p><p className="mt-1 text-[11px] leading-5 text-muted-foreground">{note}</p></div></li>)}</ul></CardContent></Card>
        <p className="px-2 text-center text-[11px] leading-5 text-muted-foreground">申请与审核结果均为高保真示例，不代表真实人员、证件或资金结果。</p>
      </aside>
    </div>

    <Dialog open={!!preview} onOpenChange={open => { if (!open) setPreview(null) }}><DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{documents.find(item => item.key === preview)?.label || '审核材料'}</DialogTitle><DialogDescription>申请 #{registration.id} · {registration.displayName} · 示例材料</DialogDescription></DialogHeader>{preview && <DocumentArtwork type={preview} registration={registration} large />}<Alert><LockKeyhole className="size-4" /><AlertDescription>此处仅用于演示私密材料放大核对，不是可识别真实身份的证件图片。</AlertDescription></Alert><DialogFooter><Button onClick={() => setPreview(null)}>完成核对</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!action} onOpenChange={open => { if (!open) setAction(null) }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>{action === 'approve' ? '核对并开通账号' : '核对退回申请'}</DialogTitle><DialogDescription>申请 #{registration.id} · 账号 {registration.username} · 结单昵称 {registration.displayName}</DialogDescription></DialogHeader>{action === 'approve' ? <div className="rounded-xl border bg-muted/30 p-4"><dl><DetailRow label="开通等级">{adminRegistrationLevels.find(item => String(item.id) === levelId)?.name}</DetailRow><DetailRow label="押金结果">{cash ? `登记 ${depositAmount} 元（应缴 ${registration.depositRequired} 元）` : `单抵目标 ${registration.depositRequired} 元，本次不登记现金`}</DetailRow><DetailRow label="开通后">使用入驻绑定的微信快捷登录，首次登录可选择开启二次验证</DetailRow><DetailRow label="审核意见"><span className="whitespace-pre-wrap">{reviewNote.trim() || '未填写'}</span></DetailRow></dl></div> : <div className="rounded-xl border border-warning/30 bg-warning/10 p-4"><p className="text-xs font-medium text-warning-foreground">退回原因</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{reviewNote}</p></div>}<Alert><ShieldCheck className="size-4" /><AlertDescription>请按当前读取的申请版本确认，原型不会创建真实账号或登记真实押金。</AlertDescription></Alert><DialogFooter><Button variant="outline" onClick={() => setAction(null)}>返回修改</Button><Button variant={action === 'reject' ? 'destructive' : 'default'} onClick={confirmReview}>{action === 'approve' ? '确认开通' : '确认退回'}</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
