import { useMemo, useState } from 'react'
import {
  ArrowDownToLine,
  Banknote,
  Check,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Info,
  LockKeyhole,
  ReceiptText,
  RotateCcw,
  Search,
  Settings2,
  ShieldAlert,
  SlidersHorizontal,
  UserRound,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminWithdrawScenario } from '@/components/admin/admin-config'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { initialAdminWithdraws, type AdminWithdraw, type AdminWithdrawClaim, type AdminWithdrawStatus } from '@/pages/admin/admin-withdraw-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminWithdrawPageProps {
  scenario: AdminWithdrawScenario
  onScenarioChange: (scenario: AdminWithdrawScenario) => void
}

const statusLabel: Record<AdminWithdrawStatus, string> = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回' }
const claimLabel: Record<AdminWithdrawClaim, string> = { unclaimed: '未领取', mine: '我已领取', other: '其他客服已领取' }
const emptyFilters = { providerUsername: '', providerNickname: '', payeeName: '', payeeAccount: '' }

function coins(amount: number) {
  return formatWaveAmount(amount / 10)
}

function rmb(amount: number) {
  return `¥${(amount / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function AdminWithdrawPage({ scenario, onScenarioChange }: AdminWithdrawPageProps) {
  const [withdraws, setWithdraws] = useState<AdminWithdraw[]>(initialAdminWithdraws)
  const [filters, setFilters] = useState(emptyFilters)
  const [applied, setApplied] = useState(emptyFilters)
  const [status, setStatus] = useState<'ALL' | AdminWithdrawStatus>(scenario === 'pending' ? 'PENDING' : 'ALL')
  const [claimScope, setClaimScope] = useState<'ALL' | AdminWithdrawClaim>('ALL')
  const [advanced, setAdvanced] = useState(false)
  const [detail, setDetail] = useState<AdminWithdraw | null>(null)
  const [audit, setAudit] = useState<{ row: AdminWithdraw; action: 'approve' | 'reject' } | null>(null)
  const [payoutReference, setPayoutReference] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [auditError, setAuditError] = useState('')
  const [release, setRelease] = useState<AdminWithdraw | null>(null)
  const [releaseReason, setReleaseReason] = useState('')
  const [releaseConfirmed, setReleaseConfirmed] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [configRecovered, setConfigRecovered] = useState(false)
  const [minAmount, setMinAmount] = useState('1000')
  const [taxRate, setTaxRate] = useState('10')
  const [configError, setConfigError] = useState('')
  const [reviewChecked, setReviewChecked] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [feedback, setFeedback] = useState<{ text: string; tone: 'success' | 'warning' } | null>(null)

  const canAudit = scenario !== 'readonly'
  const canRelease = scenario !== 'readonly' && scenario !== 'audit-only'
  const locked = scenario === 'review'
  const configFailed = scenario === 'config-error' && !configRecovered
  const pending = withdraws.filter(row => row.status === 'PENDING')
  const pendingPayout = pending.reduce((sum, row) => sum + row.actualAmount, 0)
  const advancedCount = [applied.payeeName, applied.payeeAccount].filter(Boolean).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const match = (value: string, query: string) => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    return withdraws.filter(row =>
      (!applied.providerUsername || match(row.providerUsername, applied.providerUsername)) &&
      (!applied.providerNickname || match(row.providerName, applied.providerNickname)) &&
      (!applied.payeeName || match(row.payeeName, applied.payeeName)) &&
      (!applied.payeeAccount || match(row.payeeAccount, applied.payeeAccount)) &&
      (status === 'ALL' || row.status === status) &&
      (claimScope === 'ALL' || row.claim === claimScope),
    ).sort((a, b) => status === 'PENDING' ? a.createdAt.localeCompare(b.createdAt) || a.id - b.id : b.createdAt.localeCompare(a.createdAt) || b.id - a.id)
  }, [applied, claimScope, scenario, status, withdraws])

  function notify(text: string, tone: 'success' | 'warning' = 'success') {
    setFeedback({ text, tone })
  }

  function preflight() {
    if (scenario === 'read-error') { notify('提现状态读取失败，请重试；本次未变更申请或资金状态。', 'warning'); return false }
    if (scenario === 'conflict') { notify('申请状态或领取人已变化，请刷新后重新核对；本次未执行操作。', 'warning'); return false }
    return true
  }

  function claim(row: AdminWithdraw) {
    if (!canAudit || locked || row.status !== 'PENDING' || row.claim !== 'unclaimed' || !preflight()) return
    setWithdraws(items => items.map(item => item.id === row.id ? { ...item, claim: 'mine', claimedBy: '运营管理员', claimedAt: '刚刚 · 示例' } : item))
    notify(`已领取申请 #${row.id}，请核对收款信息后再处理。`)
  }

  function openAudit(row: AdminWithdraw, action: 'approve' | 'reject') {
    if (!canAudit || locked || row.status !== 'PENDING' || row.claim !== 'mine' || !preflight()) return
    setAudit({ row, action })
    setPayoutReference('')
    setRejectReason('')
    setAuditError('')
  }

  function submitAudit() {
    if (!audit || locked) return
    if (audit.action === 'approve' && !payoutReference.trim()) { setAuditError('请填写 1–100 字的打款流水号或凭证编号'); return }
    if (audit.action === 'reject' && !rejectReason.trim()) { setAuditError('请填写 1–255 字的驳回原因'); return }
    const approved = audit.action === 'approve'
    setWithdraws(items => items.map(item => item.id === audit.row.id ? {
      ...item,
      status: approved ? 'APPROVED' : 'REJECTED',
      payoutReference: approved ? payoutReference.trim() : '',
      paidAt: approved ? '刚刚 · 示例' : null,
      auditRemark: approved ? '' : rejectReason.trim(),
      auditorName: '运营管理员',
      auditedAt: '刚刚 · 示例',
    } : item))
    notify(approved ? '已在原型中登记人工打款，未发起真实转账。' : `已在原型中驳回并退还 ${coins(audit.row.amount)} 浪花，未改动真实钱包。`)
    setAudit(null)
  }

  function openRelease(row: AdminWithdraw) {
    if (!canRelease || locked || row.status !== 'PENDING' || row.claim === 'unclaimed' || !preflight()) return
    setRelease(row)
    setReleaseReason('')
    setReleaseConfirmed(false)
  }

  function submitRelease() {
    if (!release || !releaseReason.trim() || !releaseConfirmed || locked) return
    setWithdraws(items => items.map(item => item.id === release.id ? { ...item, claim: 'unclaimed', claimedBy: '', claimedAt: null } : item))
    notify(`申请 #${release.id} 已在原型中释放，其他客服可重新领取。`)
    setRelease(null)
  }

  function clearFilters() {
    setFilters(emptyFilters)
    setApplied(emptyFilters)
    setStatus('ALL')
    setClaimScope('ALL')
    if (scenario === 'empty' || scenario === 'pending') onScenarioChange('default')
  }

  function saveConfig() {
    if (configFailed) return
    if (!/^\d+$/.test(minAmount) || !Number.isSafeInteger(Number(minAmount)) || !/^\d+$/.test(taxRate) || Number(taxRate) > 100) {
      setConfigError('最低提现须为非负整数，税率须为 0–100 的整数。')
      return
    }
    setConfigError('')
    setConfigOpen(false)
    notify(`提现规则已在原型中更新：最低 ${formatWaveAmount(minAmount)} 浪花，税率 ${taxRate}%。`)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="提现列表加载中"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-20 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">提现申请加载失败</CardTitle><CardDescription className="mt-2">请检查网络或提现查看权限后重试，当前没有展示推测申请。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">提现审核</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">人工打款 · 示例</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">先领取申请，再核对收款信息并完成人工打款。1 元 = 10 浪花，金额与税额以申请快照为准。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-2 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{pending.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">待审核申请</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{rmb(pendingPayout)}</dd><dt className="mt-1 text-[11px] text-blue-100/65">待核对打款</dt></div></dl><div className="flex gap-2"><Button variant="outline" className="border-white/25 bg-white/10 text-white shadow-none hover:bg-white/20 hover:text-white" onClick={() => setStatus('PENDING')}><Clock3 className="size-4" />待审核队列</Button>{canAudit && <Button variant="outline" className="border-white/25 bg-transparent text-white hover:bg-white/10 hover:text-white" onClick={() => { setConfigOpen(true); setConfigError('') }}><Settings2 className="size-4" />{configFailed ? '提现规则 · 待读取' : `最低 ${formatWaveAmount(minAmount)} 浪花 · ${taxRate}%`}</Button>}</div></div>
    </div></section>

    <Alert><ShieldAlert className="size-4" /><AlertTitle>先确认实际打款，再登记审核通过</AlertTitle><AlertDescription>通过会扣减冻结金额并记录税额；驳回会退还全部申请浪花。本页只模拟状态变化，不发起转账、不修改真实钱包。</AlertDescription></Alert>
    {scenario === 'readonly' && <Alert><LockKeyhole className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可核对申请、收款信息和打款凭证；领取、审核、释放与规则配置需要相应权限。</AlertDescription></Alert>}
    {scenario === 'audit-only' && <Alert><Info className="size-4" /><AlertTitle>当前无释放领取权限</AlertTitle><AlertDescription>可领取和审核本人申请；释放领取需要独立的提现释放权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>提现列表更新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">资金操作前请重新读取申请状态。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {locked && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>提现审核结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">申请 #9205 的通过结果未确认，请核对状态和打款凭证，勿重复打款。其他资金操作已锁定。<Button variant="link" className="h-auto p-0" onClick={() => setReviewChecked(true)}>重新核对</Button>{reviewChecked && <span className="text-xs">当前：已通过 · DEMO-PAY-20261104-9205</span>}<Button variant="link" className="h-auto p-0" disabled={!reviewChecked} onClick={() => setReleaseOpen(true)}>完成核对</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><WalletCards className="size-4 text-primary" />提现申请记录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · {status === 'PENDING' ? '待审核按提交时间从早到晚' : '按提交时间倒序'}</CardDescription></div><Badge variant="outline" className="font-normal">金额与账号均为示例</Badge></div>
      <form data-admin-filter className="grid items-end gap-3 md:grid-cols-2 xl:grid-cols-[minmax(170px,1fr)_minmax(150px,1fr)_150px_170px_auto]" onSubmit={event => { event.preventDefault(); setApplied(filters) }}>
        <div className="space-y-2"><Label htmlFor="withdraw-provider">陪玩账号</Label><Input id="withdraw-provider" value={filters.providerUsername} onChange={event => setFilters(value => ({ ...value, providerUsername: event.target.value }))} placeholder="provider_username" className="h-10" /></div>
        <div className="space-y-2"><Label htmlFor="withdraw-nickname">陪玩昵称</Label><Input id="withdraw-nickname" value={filters.providerNickname} onChange={event => setFilters(value => ({ ...value, providerNickname: event.target.value }))} placeholder="输入昵称" className="h-10" /></div>
        <div className="space-y-2"><Label htmlFor="withdraw-status">审核状态</Label><Select value={status} onValueChange={value => setStatus(value as 'ALL' | AdminWithdrawStatus)}><SelectTrigger id="withdraw-status" className="h-10 w-full" aria-label="提现审核状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部审核状态</SelectItem><SelectItem value="PENDING">待审核</SelectItem><SelectItem value="APPROVED">已通过</SelectItem><SelectItem value="REJECTED">已驳回</SelectItem></SelectContent></Select></div>
        <div className="space-y-2"><Label htmlFor="withdraw-claim">领取状态</Label><Select value={claimScope} onValueChange={value => setClaimScope(value as 'ALL' | AdminWithdrawClaim)}><SelectTrigger id="withdraw-claim" className="h-10 w-full" aria-label="提现领取状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部领取状态</SelectItem><SelectItem value="unclaimed">未领取</SelectItem><SelectItem value="mine">我已领取</SelectItem><SelectItem value="other">其他客服已领取</SelectItem></SelectContent></Select></div>
        <div className="flex items-center gap-2 md:col-span-2 xl:col-span-1"><Button type="submit" variant="outline" className="h-10 min-w-20 whitespace-nowrap"><Search className="size-4" />查询</Button><Button type="button" variant="outline" className="h-10 whitespace-nowrap" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)}><SlidersHorizontal className="size-4" />更多筛选{advancedCount > 0 && `（${advancedCount}）`}</Button></div>
        {advanced && <div data-admin-filter-advanced className="grid gap-3 border-t pt-4 md:col-span-2 md:grid-cols-2 xl:col-span-5"><div className="space-y-2"><Label htmlFor="withdraw-payee">收款人</Label><Input id="withdraw-payee" value={filters.payeeName} onChange={event => setFilters(value => ({ ...value, payeeName: event.target.value }))} placeholder="按收款人筛选" className="h-10" /></div><div className="space-y-2"><Label htmlFor="withdraw-account">收款账号</Label><Input id="withdraw-account" value={filters.payeeAccount} onChange={event => setFilters(value => ({ ...value, payeeAccount: event.target.value }))} placeholder="按收款账号筛选" className="h-10" /></div></div>}
      </form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1060px]"><TableHeader><TableRow><TableHead>申请 / 陪玩</TableHead><TableHead className="text-right">提现金额</TableHead><TableHead className="text-right">应打款</TableHead><TableHead>状态 / 领取</TableHead><TableHead>收款信息</TableHead><TableHead>提交时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id}>
        <TableCell><div className="flex items-center gap-2"><UserRound className="size-4 text-primary" /><strong className="text-sm font-medium">{row.providerName}</strong></div><p className="mt-1 font-mono text-[10px] text-muted-foreground">#{row.id} · {row.providerUsername}</p></TableCell>
        <TableCell className="text-right"><strong className="text-sm font-semibold tabular-nums">{coins(row.amount)} 浪花</strong><p className="mt-1 text-[11px] text-muted-foreground">税率 {row.taxRate}% · 税 {coins(row.taxAmount)}</p></TableCell>
        <TableCell className="text-right"><strong className="text-sm font-semibold tabular-nums">{rmb(row.actualAmount)}</strong><p className="mt-1 text-[11px] text-muted-foreground">{coins(row.actualAmount)} 浪花</p></TableCell>
        <TableCell><Badge variant="outline" className={cn(row.status === 'PENDING' ? 'border-primary/30 bg-primary/10 text-primary' : row.status === 'APPROVED' ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive')}>{statusLabel[row.status]}</Badge><p className="mt-2 text-[11px] text-muted-foreground">{row.claim === 'other' && row.claimedBy ? `${row.claimedBy}已领取` : claimLabel[row.claim]}</p></TableCell>
        <TableCell><p className="text-xs font-medium">{row.payeeMethod} · {row.payeeName}</p><p className="mt-1 max-w-44 break-all text-[11px] text-muted-foreground">{row.payeeAccount}</p></TableCell>
        <TableCell><p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Clock3 className="size-3.5" />{row.createdAt}</p></TableCell>
        <TableCell><div className="flex min-w-44 flex-wrap justify-end gap-1.5"><Button variant="outline" size="sm" onClick={() => setDetail(row)}>详情</Button>{canAudit && row.status === 'PENDING' && row.claim === 'unclaimed' && <Button size="sm" disabled={locked} onClick={() => claim(row)}>领取</Button>}{canAudit && row.status === 'PENDING' && row.claim === 'mine' && <><Button size="sm" disabled={locked} onClick={() => openAudit(row, 'approve')}><Check className="size-3.5" />通过</Button><Button variant="ghost" size="sm" disabled={locked} className="text-destructive hover:text-destructive" onClick={() => openAudit(row, 'reject')}><X className="size-3.5" />驳回</Button></>}{canRelease && row.status === 'PENDING' && row.claim !== 'unclaimed' && <Button variant="ghost" size="sm" disabled={locked} onClick={() => openRelease(row)}>释放</Button>}</div></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ArrowDownToLine className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有符合筛选条件的提现申请</h2><p className="mt-2 text-sm text-muted-foreground">清除陪玩、收款信息、审核与领取状态后查看全部记录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>收款账号仅用于原型展示，不对应真实账户。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!detail} onOpenChange={open => { if (!open) setDetail(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>提现详情</DialogTitle><DialogDescription>申请 #{detail?.id} · 核对申请、收款、领取与审核记录</DialogDescription></DialogHeader>{detail && <><section className="rounded-xl border bg-primary/5 p-4"><div className="flex items-center justify-between gap-3"><div><p className="text-xs text-muted-foreground">应打款人民币</p><p className="mt-1 text-2xl font-semibold tabular-nums">{rmb(detail.actualAmount)}</p></div><Banknote className="size-7 text-primary" /></div><p className="mt-2 text-xs text-muted-foreground">{detail.payeeMethod} · {detail.payeeName} · {detail.payeeAccount}</p></section><dl className="grid gap-x-6 rounded-xl border bg-muted/20 p-4 sm:grid-cols-2">{[
      ['陪玩', `${detail.providerName} · ${detail.providerUsername}`], ['提现金额', `${coins(detail.amount)} 浪花`], ['税率 / 税额', `${detail.taxRate}% · ${coins(detail.taxAmount)} 浪花`], ['扣税后金额', `${coins(detail.actualAmount)} 浪花`], ['状态', statusLabel[detail.status]], ['领取状态', `${claimLabel[detail.claim]}${detail.claimedBy ? ` · ${detail.claimedBy}` : ''}`], ['领取时间', detail.claimedAt || '—'], ['申请备注', detail.remark || '—'], ...(detail.status === 'REJECTED' ? [['驳回原因', detail.auditRemark || '—']] : []), ...(detail.status === 'APPROVED' ? [['打款流水号', detail.payoutReference || '—'], ['实际打款时间', detail.paidAt || '—']] : []), ['审核人', detail.auditorName || '—'], ['提交时间', detail.createdAt], ['审核时间', detail.auditedAt || '—'],
    ].map(([label, value]) => <div key={label} className="border-b py-3 last:border-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{value}</dd></div>)}</dl></>}<DialogFooter><Button variant="outline" onClick={() => setDetail(null)}>关闭</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!audit} onOpenChange={open => { if (!open) setAudit(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{audit?.action === 'approve' ? '登记人工打款并通过' : '驳回提现申请'}</DialogTitle><DialogDescription>申请 #{audit?.row.id} · 已重新读取申请和领取状态</DialogDescription></DialogHeader>{audit && <><section className="rounded-xl border bg-primary/5 p-4"><div className="flex items-start justify-between gap-4"><div><p className="text-xs text-muted-foreground">{audit.action === 'approve' ? '确认已向以下收款人实际打款' : '驳回后退回陪玩余额'}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{audit.action === 'approve' ? rmb(audit.row.actualAmount) : `${coins(audit.row.amount)} 浪花`}</p></div><ReceiptText className="size-7 text-primary" /></div><p className="mt-3 text-sm font-medium">{audit.row.payeeName} · {audit.row.payeeMethod}</p><p className="mt-1 break-all text-xs text-muted-foreground">{audit.row.payeeAccount}</p><p className="mt-3 border-t pt-3 text-xs text-muted-foreground">申请 {coins(audit.row.amount)} 浪花 · 税额 {coins(audit.row.taxAmount)} 浪花 · 实付 {coins(audit.row.actualAmount)} 浪花</p></section>{audit.action === 'approve' ? <div className="space-y-2"><Label htmlFor="payout-reference">打款流水号 / 凭证编号</Label><Input id="payout-reference" value={payoutReference} onChange={event => { setPayoutReference(event.target.value); setAuditError('') }} maxLength={100} placeholder="填写实际转账凭证编号" className="h-11" /><p className="text-[11px] text-muted-foreground">必填，最多 100 字。不要在未打款时登记通过。</p></div> : <div className="space-y-2"><Label htmlFor="withdraw-reject-reason">驳回原因</Label><Textarea id="withdraw-reject-reason" value={rejectReason} onChange={event => { setRejectReason(event.target.value); setAuditError('') }} maxLength={255} rows={3} placeholder="说明驳回原因，帮助陪玩修正后重新申请" /><p className="text-[11px] text-muted-foreground">必填，最多 255 字；申请的全额浪花将退回余额。</p></div>}{auditError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{auditError}</AlertDescription></Alert>}<Alert><ShieldAlert className="size-4" /><AlertDescription>{audit.action === 'approve' ? '原型仅登记示例结果，不会真实打款或扣减钱包。' : '原型仅模拟驳回与退还，不会修改真实钱包或发送通知。'}</AlertDescription></Alert></>}<DialogFooter><Button variant="outline" onClick={() => setAudit(null)}>返回</Button><Button variant={audit?.action === 'reject' ? 'destructive' : 'default'} onClick={submitAudit}>{audit?.action === 'approve' ? '确认已打款' : '确认驳回并退还'}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!release} onOpenChange={open => { if (!open) setRelease(null) }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>释放提现领取</DialogTitle><DialogDescription>申请 #{release?.id} · {release?.claimedBy}已领取</DialogDescription></DialogHeader><Alert><ShieldAlert className="size-4" /><AlertTitle>释放后其他客服可重新领取</AlertTitle><AlertDescription>请先与领取人及财务核实，确认尚未线下打款；如已打款或无法确认，请勿释放。</AlertDescription></Alert><div className="space-y-2"><Label htmlFor="release-reason">释放原因</Label><Textarea id="release-reason" value={releaseReason} onChange={event => setReleaseReason(event.target.value)} maxLength={255} rows={3} placeholder="填写核实情况或释放原因" /><p className="text-[11px] text-muted-foreground">必填，最多 255 字；将记录到后台审计日志。</p></div><label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-xl border p-3 text-xs leading-5"><Checkbox checked={releaseConfirmed} onCheckedChange={value => setReleaseConfirmed(value === true)} className="mt-0.5" />我已核实此申请尚未打款</label><DialogFooter><Button variant="outline" onClick={() => setRelease(null)}>取消</Button><Button disabled={!releaseReason.trim() || !releaseConfirmed} onClick={submitRelease}>确认释放</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={configOpen} onOpenChange={setConfigOpen}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>提现规则</DialogTitle><DialogDescription>设置后续申请的最低提现金额与税率，历史申请仍使用原快照。</DialogDescription></DialogHeader>{configFailed ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>提现规则读取失败</AlertTitle><AlertDescription>当前不能保存未知规则。<Button variant="link" className="h-auto p-0" onClick={() => setConfigRecovered(true)}>重新读取</Button></AlertDescription></Alert> : <><div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="withdraw-min">最低提现（浪花）</Label><Input id="withdraw-min" inputMode="numeric" value={minAmount} onChange={event => setMinAmount(event.target.value)} /><p className="text-[11px] text-muted-foreground">非负整数。</p></div><div className="space-y-2"><Label htmlFor="withdraw-tax">提现税率（%）</Label><Input id="withdraw-tax" inputMode="numeric" value={taxRate} onChange={event => setTaxRate(event.target.value)} /><p className="text-[11px] text-muted-foreground">0–100 的整数。</p></div></div><Alert><Info className="size-4" /><AlertDescription>多人修改时按最后保存的配置生效；本原型不会影响后续真实申请。</AlertDescription></Alert></>}{configError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{configError}</AlertDescription></Alert>}<DialogFooter><Button variant="outline" onClick={() => setConfigOpen(false)}>取消</Button><Button disabled={configFailed} onClick={saveConfig}>保存规则</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={releaseOpen} onOpenChange={setReleaseOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>完成提现结果核对</DialogTitle><DialogDescription>当前快照为已通过，凭证 DEMO-PAY-20261104-9205。确认已核对申请与打款记录？</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setReleaseOpen(false)}>继续核对</Button><Button onClick={() => { setReleaseOpen(false); onScenarioChange('default') }}>确认完成</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl">{feedback.tone === 'success' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="leading-6">{feedback.text}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback(null)} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
