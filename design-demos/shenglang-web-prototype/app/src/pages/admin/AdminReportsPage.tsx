import { useMemo, useState, type FormEvent } from 'react'
import {
  BadgeCheck,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Eye,
  FileCheck2,
  FileImage,
  ImageIcon,
  Info,
  ReceiptText,
  RefreshCw,
  Search,
  ShieldCheck,
  XCircle,
  ZoomIn,
} from 'lucide-react'
import type { AdminReportsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
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
import { adminReports, adminReportStatusLabel, type AdminReport, type AdminReportStatus } from '@/pages/admin/admin-report-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminReportsPageProps {
  scenario: AdminReportsScenario
  onScenarioChange: (scenario: AdminReportsScenario) => void
  onOpenOrder: (orderNo: string) => void
  onOpenCommission: () => void
}

function payoutText(report: AdminReport) {
  if (report.status === 'PENDING') return `预计 ${formatWaveAmount(report.expectedIncome)} 浪花`
  if (report.status === 'REJECTED') return '—'
  if (report.paymentStatus === 'PARTIALLY_REFUNDED') return `原核定 ${formatWaveAmount(report.expectedIncome)} 浪花`
  return `${formatWaveAmount(report.expectedIncome)} 浪花 · ${report.settlement === 'SETTLED' ? '已入账（可能冻结）' : '待整单结算'}`
}

export function AdminReportsPage({ scenario, onScenarioChange, onOpenOrder, onOpenCommission }: AdminReportsPageProps) {
  const [rows, setRows] = useState(adminReports)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<'ALL' | AdminReportStatus>(scenario === 'pending' || scenario === 'blocked' ? 'PENDING' : scenario === 'approved' || scenario === 'aftersale' ? 'APPROVED' : scenario === 'rejected' ? 'REJECTED' : 'ALL')
  const [orderBlocked, setOrderBlocked] = useState(scenario === 'blocked')
  const [current, setCurrent] = useState<AdminReport | null>(() => scenario === 'aftersale' ? adminReports.find(report => report.id === 8205) ?? null : scenario === 'detail-loading' || scenario === 'detail-error' ? adminReports[0] : null)
  const [detailResolved, setDetailResolved] = useState(false)
  const [previewImage, setPreviewImage] = useState<{ url: string; label: string } | null>(null)
  const [approveTarget, setApproveTarget] = useState<AdminReport | null>(null)
  const [rejectTarget, setRejectTarget] = useState<AdminReport | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  const [rejectAttempted, setRejectAttempted] = useState(false)
  const [notice, setNotice] = useState('')
  const [issue, setIssue] = useState('')
  const [reviewChecked, setReviewChecked] = useState(false)
  const [reviewResolved, setReviewResolved] = useState(false)

  const readOnly = scenario === 'readonly'
  const canViewOrders = scenario !== 'no-order-permission'
  const conflict = scenario === 'conflict'
  const auditReview = scenario === 'review' && !reviewResolved
  const auditLocked = conflict || auditReview
  const detailLoading = scenario === 'detail-loading' && !detailResolved
  const detailError = scenario === 'detail-error' && !detailResolved
  const pendingCount = rows.filter(report => report.status === 'PENDING').length
  const blockedCount = rows.filter(report => report.status === 'PENDING' && report.orderBlockReason).length

  const filteredRows = useMemo(() => {
    if (scenario === 'empty') return []
    const normalized = keyword.trim().toLowerCase()
    return rows.filter(report => {
      if (status !== 'ALL' && report.status !== status) return false
      if (orderBlocked && !report.orderBlockReason) return false
      if (normalized && !`${report.providerName} ${report.providerNo} ${report.providerUsername} ${report.gameName}`.toLowerCase().includes(normalized)) return false
      return true
    })
  }, [keyword, orderBlocked, rows, scenario, status])

  function search(event: FormEvent) {
    event.preventDefault()
    setKeyword(keywordInput.trim())
  }

  function clearFilters() {
    setKeywordInput('')
    setKeyword('')
    setStatus('ALL')
    setOrderBlocked(false)
    if (scenario === 'empty') onScenarioChange('default')
  }

  function openDetail(report: AdminReport) {
    setCurrent(report)
    setDetailResolved(false)
    setNotice('')
    setIssue('')
  }

  function approve() {
    if (!approveTarget || readOnly || auditLocked) return
    const target = approveTarget
    setApproveTarget(null)
    const missingMaterials: string[] = []
    if (!target.entryImage) missingMaterials.push('入队截图')
    if (!target.completionImage) missingMaterials.push('结单截图')
    if (target.resultImages.length === 0) missingMaterials.push('战绩截图')
    if (missingMaterials.length > 0) {
      setIssue(`报单 #${target.id} 缺少：${missingMaterials.join('、')}。审核未完成，可驳回并说明需补充的材料。`)
      return
    }
    setRows(items => items.map(report => report.id === target.id ? { ...report, status: 'APPROVED', auditedAt: '2026-11-04 17:06', auditorName: '运营管理员', commissionSource: '审核快照' } : report))
    setCurrent(report => report?.id === target.id ? { ...report, status: 'APPROVED', auditedAt: '2026-11-04 17:06', auditorName: '运营管理员', commissionSource: '审核快照' } : report)
    setIssue('')
    setNotice(`报单 #${target.id} 凭证已通过。全体陪玩审核通过、售后窗口结束且无未结客诉后才结算，本次未直接入账。`)
  }

  function reject() {
    setRejectAttempted(true)
    if (!rejectTarget || !rejectReason.trim() || readOnly || auditLocked) return
    const target = rejectTarget
    setRows(items => items.map(report => report.id === target.id ? { ...report, status: 'REJECTED', auditRemark: rejectReason.trim(), auditedAt: '2026-11-04 17:06', auditorName: '运营管理员' } : report))
    setCurrent(report => report?.id === target.id ? { ...report, status: 'REJECTED', auditRemark: rejectReason.trim(), auditedAt: '2026-11-04 17:06', auditorName: '运营管理员' } : report)
    setRejectTarget(null)
    setRejectReason('')
    setRejectAttempted(false)
    setIssue('')
    setNotice(`报单 #${target.id} 已驳回，原因已写入本地审核记录；未通知真实陪玩。`)
  }

  function checkReview() {
    setRows(items => items.map(report => report.id === 8201 ? { ...report, status: 'APPROVED', auditedAt: '2026-11-04 17:02', auditorName: '运营管理员', commissionSource: '审核快照' } : report))
    setReviewChecked(true)
    setIssue('')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="报单审核加载中"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">报单列表加载失败</CardTitle><CardDescription className="mt-2">请检查网络与 report:view 权限后重试，当前没有展示推测报单。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RefreshCw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">履约凭证审核</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">订单分账快照</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">核验入队、结单和战绩截图。凭证通过不等于立即到账，收益与冻结状态以订单结算和钱包流水为准。</p><Button variant="secondary" size="sm" className="mt-4" onClick={onOpenCommission}>订单默认抽成配置</Button></div>
        <dl className="grid grid-cols-3 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{pendingCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">待审核</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{blockedCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">订单状态待核对</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{rows.filter(report => report.status === 'APPROVED').length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">已通过示例</dt></div></dl>
      </div>
    </section>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>报单更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">现有筛选与详情快照已保留，恢复后再执行审核。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {auditReview && <Alert className="border-warning/35 bg-warning/5"><Clock3 className="size-4 text-warning" /><AlertTitle>报单 #8201 的审核结果待核对</AlertTitle><AlertDescription><p>原请求可能仍在处理，重新读取只反映当前状态，不会再次提交审核。</p>{reviewChecked && <p className="mt-1 font-medium">当前状态：已通过 · 审核人：运营管理员 · 2026-11-04 17:02</p>}<div className="mt-2 flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={checkReview} disabled={reviewChecked}><RefreshCw className="size-4" />{reviewChecked ? '已重新读取' : '重新读取'}</Button>{reviewChecked && <Button variant="outline" size="sm" onClick={() => { setReviewResolved(true); setNotice('已核对报单状态与审核记录，审核限制已解除。') }}>已核对，解除限制</Button>}</div></AlertDescription></Alert>}
    {conflict && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>报单状态已被其他审核人更新</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">当前快照不能继续审核，也不会重复入账。请重新读取最新状态。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('approved')}>重新读取报单</Button></AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><BadgeCheck className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    {issue && <Alert variant="destructive" role="alert"><CircleAlert className="size-4" /><AlertDescription>{issue}</AlertDescription></Alert>}
    {readOnly && <Alert><Eye className="size-4" /><AlertTitle>当前账号只有 report:view 权限</AlertTitle><AlertDescription>可核对凭证与结算快照，但不显示通过或驳回操作。</AlertDescription></Alert>}

    <Card>
      <CardContent className="py-4">
        <form data-admin-filter onSubmit={search} className="grid items-end gap-3 md:grid-cols-2 xl:grid-cols-[minmax(300px,1fr)_200px_auto_minmax(180px,auto)]">
          <div className="space-y-2"><Label htmlFor="report-keyword">查找报单</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="report-keyword" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} placeholder="陪玩、陪玩编号或游戏项目" className="h-11 pl-9" /></div></div>
          <div className="space-y-2"><Label>审核状态</Label><Select value={status} onValueChange={value => { setStatus(value as 'ALL' | AdminReportStatus); if (value !== 'PENDING') setOrderBlocked(false) }}><SelectTrigger className="h-11 w-full" aria-label="报单审核状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部审核状态</SelectItem><SelectItem value="PENDING">待审核</SelectItem><SelectItem value="APPROVED">已通过</SelectItem><SelectItem value="REJECTED">已驳回</SelectItem></SelectContent></Select></div>
          <div className="flex items-center gap-2">
            <Button type="submit" className="h-11 min-w-20 whitespace-nowrap">查询</Button>
            <Button type="button" variant="outline" className="h-11 min-w-24 whitespace-nowrap" onClick={clearFilters}>清除条件</Button>
          </div>
          <label className="flex min-h-11 items-center gap-2 rounded-lg border px-3 text-xs"><Checkbox checked={orderBlocked} onCheckedChange={value => { setOrderBlocked(value === true); if (value === true) setStatus('PENDING') }} aria-label="仅看订单状态待核对" />订单状态待核对</label>
        </form>
        {keywordInput.trim() !== keyword && <p role="status" className="mt-2 text-xs text-warning-foreground">搜索输入已更改，列表尚未更新；请点击查询。</p>}
      </CardContent>
    </Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="text-base">报单审核队列</CardTitle><CardDescription className="mt-1">本页 {filteredRows.length} 条 · 按创建时间倒序 · 不显示待提交草稿</CardDescription></div><Badge variant="outline">预计收益以分账快照计算</Badge></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1240px]"><TableHeader><TableRow><TableHead>报单 / 状态</TableHead><TableHead>陪玩</TableHead><TableHead>关联订单</TableHead><TableHead>游戏 / 玩法</TableHead><TableHead className="text-right">报单金额</TableHead><TableHead>预计 / 核定收益</TableHead><TableHead>抽成来源</TableHead><TableHead>提交时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{filteredRows.map(report => <TableRow key={report.id}>
        <TableCell><div className="flex items-center gap-2"><span className="font-mono text-xs font-medium">#{report.id}</span><Badge variant="outline" className={cn(report.status === 'PENDING' && 'border-warning/40 bg-warning/10 text-warning-foreground', report.status === 'APPROVED' && 'border-success/30 bg-success/10 text-success', report.status === 'REJECTED' && 'text-muted-foreground')}>{adminReportStatusLabel[report.status]}</Badge></div>{report.orderBlockReason && <p className="mt-1.5 max-w-44 text-[10px] leading-4 text-warning-foreground">{report.orderBlockReason}</p>}{report.aftersaleOpen && <p className="mt-1 text-[10px] text-warning-foreground">售后处理中 · 结算以钱包为准</p>}</TableCell>
        <TableCell><span className="block text-xs font-medium">{report.providerName}</span><span className="mt-1 block text-[11px] text-muted-foreground">{report.providerNo} · {report.providerUsername}</span></TableCell>
        <TableCell>{canViewOrders ? <button type="button" onClick={() => onOpenOrder(report.orderNo)} className="font-mono text-xs text-primary hover:underline" aria-label={`查看订单 ${report.orderNo}`}>{report.orderNo}</button> : <span className="font-mono text-xs">{report.orderNo}</span>}</TableCell>
        <TableCell><span className="block text-xs">{report.gameName}</span><span className="mt-1 block text-[11px] text-muted-foreground">{report.fulfillment}</span></TableCell>
        <TableCell className="whitespace-nowrap text-right text-xs font-medium tabular-nums">{formatWaveAmount(report.amount)} 浪花</TableCell>
        <TableCell><span className="block whitespace-nowrap text-xs">{payoutText(report)}</span>{report.paymentStatus === 'PARTIALLY_REFUNDED' && report.status === 'APPROVED' && <span className="mt-1 block text-[10px] text-muted-foreground">部分退款，净收益以流水为准</span>}</TableCell>
        <TableCell><span className="block whitespace-nowrap text-xs">{report.commissionRate}%</span><span className="mt-1 block text-[11px] text-muted-foreground">{report.commissionSource}</span></TableCell>
        <TableCell className="whitespace-nowrap text-[11px] text-muted-foreground">{report.submittedAt.slice(5)}</TableCell>
        <TableCell><div className="flex min-h-11 items-center justify-end gap-1"><Button variant="ghost" size="sm" onClick={() => openDetail(report)}>详情</Button>{!readOnly && report.status === 'PENDING' && <>{!report.orderBlockReason && <Button variant="outline" size="sm" disabled={auditLocked} onClick={() => setApproveTarget(report)}>通过</Button>}<Button variant="ghost" size="sm" disabled={auditLocked} onClick={() => { setRejectTarget(report); setRejectReason(''); setRejectAttempted(false) }}>驳回</Button></>}</div></TableCell>
      </TableRow>)}</TableBody></Table>{filteredRows.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><ReceiptText className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的报单</h2><p className="mt-2 text-sm text-muted-foreground">调整关键词、审核状态或订单阻塞筛选后再查看。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除全部条件</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>所有凭证与审核操作均为本地示例，不修改真实订单或钱包。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={current !== null} onOpenChange={open => !open && setCurrent(null)}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl"><DialogHeader><DialogTitle className="flex items-center gap-2"><FileCheck2 className="size-5 text-primary" />报单详情 {current && `#${current.id}`}</DialogTitle><DialogDescription>核对服务凭证、陪玩身份与订单结算快照。</DialogDescription></DialogHeader>
      {current && <>
        {detailLoading ? <div className="space-y-4" aria-label="报单详情加载中"><Skeleton className="h-24 w-full" /><Skeleton className="h-64 w-full" /><Skeleton className="h-28 w-full" /></div> : detailError ? <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>报单详情更新失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">列表与筛选不受影响，请独立重试。<Button variant="link" className="h-auto p-0" onClick={() => setDetailResolved(true)}>重新加载详情</Button></AlertDescription></Alert> : <>
          {current.orderBlockReason && <Alert className="border-warning/35 bg-warning/5"><CircleAlert className="size-4 text-warning" /><AlertTitle>{current.orderBlockReason}</AlertTitle><AlertDescription>可以核对或驳回凭证，但不能通过；此处仅表示订单状态限制，不替代其他提交校验。</AlertDescription></Alert>}
          {current.aftersaleOpen && <Alert className="mt-4 border-warning/35 bg-warning/5"><Info className="size-4 text-warning" /><AlertTitle>{current.settlement === 'SETTLED' ? '售后处理中，已结算收益以钱包流水为准' : '售后处理中，本单暂不结算'}</AlertTitle><AlertDescription>凭证状态与售后资金状态分开核对，不根据报单金额推测净收益。</AlertDescription></Alert>}
          <dl className="mt-4 grid grid-cols-3 gap-3"><div className="rounded-xl border bg-muted/30 p-4"><dt className="text-[11px] text-muted-foreground">报单状态</dt><dd className="mt-1 text-sm font-semibold">{adminReportStatusLabel[current.status]}</dd></div><div className="rounded-xl border bg-muted/30 p-4"><dt className="text-[11px] text-muted-foreground">报单金额</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{formatWaveAmount(current.amount)} 浪花</dd></div><div className="rounded-xl border bg-muted/30 p-4"><dt className="text-[11px] text-muted-foreground">{current.status === 'PENDING' ? '预计收益' : '核定收益'}</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{current.status === 'REJECTED' ? '—' : `${formatWaveAmount(current.expectedIncome)} 浪花`}</dd></div></dl>
          <div className="mt-4 grid gap-4 md:grid-cols-2"><dl className="space-y-3 rounded-xl border p-4 text-xs"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">陪玩</dt><dd className="font-medium">{current.providerName}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">陪玩编号</dt><dd>{current.providerNo}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">陪玩账号</dt><dd>{current.providerUsername}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">游戏 / 玩法</dt><dd>{current.gameName} · {current.fulfillment}</dd></div></dl><dl className="space-y-3 rounded-xl border p-4 text-xs"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">关联订单</dt><dd>{canViewOrders ? <button type="button" className="font-mono text-primary hover:underline" onClick={() => onOpenOrder(current.orderNo)}>{current.orderNo}</button> : <span className="font-mono">{current.orderNo}</span>}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">抽成来源</dt><dd>{current.commissionRate}% · {current.commissionSource}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">结算状态</dt><dd>{current.settlement === 'SETTLED' ? '已入账（可能冻结）' : current.settlement === 'WAITING' ? '待整单结算' : '收益待核对'}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">提交时间</dt><dd>{current.submittedAt}</dd></div></dl></div>
          <section className="mt-5"><div className="mb-3 flex items-center justify-between"><h3 className="flex items-center gap-2 text-sm font-semibold"><FileImage className="size-4 text-primary" />订单凭证</h3><span className="text-[11px] text-muted-foreground">点击图片查看本地示例大图</span></div><div className="grid gap-3 sm:grid-cols-3"><EvidenceImage label="入队截图" url={current.entryImage} onPreview={setPreviewImage} /><EvidenceImage label="结单截图" url={current.completionImage} onPreview={setPreviewImage} /><EvidenceImage label={`战绩截图（${current.resultImages.length} 张）`} url={current.resultImages[0]} onPreview={setPreviewImage} /></div></section>
          <section className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-xl border p-4"><h3 className="text-xs font-semibold">报单说明</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{current.description}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">备注：{current.remark}</p></div><div className="rounded-xl border p-4"><h3 className="text-xs font-semibold">审核记录</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">审核人：{current.auditorName || '—'}</p><p className="mt-1 text-xs leading-5 text-muted-foreground">审核时间：{current.auditedAt || '—'}</p>{current.auditRemark && <p className="mt-2 rounded-lg bg-destructive/5 p-2 text-xs leading-5 text-destructive">驳回原因：{current.auditRemark}</p>}</div></section>
          {!readOnly && current.status === 'PENDING' && <DialogFooter className="mt-5 border-t pt-4"><Button variant="outline" disabled={auditLocked} onClick={() => { setRejectTarget(current); setRejectReason(''); setRejectAttempted(false) }}>驳回报单</Button>{!current.orderBlockReason && <Button disabled={auditLocked} onClick={() => setApproveTarget(current)}><ShieldCheck className="size-4" />审核通过</Button>}</DialogFooter>}
        </>}
      </>}
    </DialogContent></Dialog>

    <AlertDialog open={approveTarget !== null} onOpenChange={open => !open && setApproveTarget(null)}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>确认通过报单 #{approveTarget?.id}？</AlertDialogTitle><AlertDialogDescription asChild><span className="block space-y-3"><span className="block">确认「{approveTarget?.providerName} · {approveTarget?.providerNo}」的入队、结单和战绩截图完整有效？</span>{approveTarget && <span className="block rounded-lg border bg-muted/40 p-3 text-xs leading-5">订单 {approveTarget.orderNo}<br />报单 {formatWaveAmount(approveTarget.amount)} 浪花 · 预计收益 {formatWaveAmount(approveTarget.expectedIncome)} 浪花 · {approveTarget.commissionRate}%（{approveTarget.commissionSource}）<br /><strong className="text-foreground">凭证通过后仍须等待整单结算，不代表收益已入账。</strong></span>}</span></AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>返回核对</AlertDialogCancel><AlertDialogAction onClick={approve}><CheckCircle2 className="size-4" />确认通过</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>

    <Dialog open={rejectTarget !== null} onOpenChange={open => { if (!open) { setRejectTarget(null); setRejectReason(''); setRejectAttempted(false) } }}><DialogContent className="sm:max-w-lg"><DialogHeader><DialogTitle>驳回报单 #{rejectTarget?.id}</DialogTitle><DialogDescription>{rejectTarget?.providerName} · {rejectTarget?.providerNo} · {rejectTarget?.gameName}。驳回原因会展示给陪玩。</DialogDescription></DialogHeader><div className="space-y-2"><Label htmlFor="report-reject-reason">驳回原因</Label><Textarea id="report-reject-reason" value={rejectReason} onChange={event => setRejectReason(event.target.value)} maxLength={255} rows={4} placeholder="说明缺失或无法核对的凭证，以及需要补充的材料" aria-invalid={rejectAttempted && !rejectReason.trim()} />{rejectAttempted && !rejectReason.trim() ? <p role="alert" className="text-xs text-destructive">请填写 1–255 字的驳回原因</p> : <p className="text-right text-[11px] text-muted-foreground">{rejectReason.length} / 255</p>}</div><DialogFooter><Button variant="outline" onClick={() => setRejectTarget(null)}>返回</Button><Button variant="destructive" disabled={auditLocked} onClick={reject}><XCircle className="size-4" />确认驳回</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={previewImage !== null} onOpenChange={open => !open && setPreviewImage(null)}><DialogContent className="sm:max-w-3xl"><DialogHeader><DialogTitle>{previewImage?.label}</DialogTitle><DialogDescription>本地示例凭证，不读取或修改真实私有媒体。</DialogDescription></DialogHeader>{previewImage && <img src={previewImage.url} alt={`${previewImage.label}大图`} className="max-h-[68vh] w-full rounded-lg border object-contain" />}</DialogContent></Dialog>
  </div>
}

function EvidenceImage({ label, url, onPreview }: { label: string; url?: string; onPreview: (value: { url: string; label: string }) => void }) {
  if (!url) return <div className="flex min-h-48 flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 p-4 text-center"><ImageIcon className="size-6 text-muted-foreground" /><p className="mt-2 text-xs font-medium">{label}</p><p className="mt-1 text-[11px] text-destructive">未上传</p></div>
  return <button type="button" onClick={() => onPreview({ url, label })} className="group overflow-hidden rounded-xl border bg-muted/20 text-left" aria-label={`查看${label}`}><div className="relative h-40 overflow-hidden"><img src={url} alt={label} className="size-full object-cover transition-transform group-hover:scale-[1.02]" /><span className="absolute bottom-2 right-2 grid size-8 place-items-center rounded-full bg-background/90 shadow-sm"><ZoomIn className="size-4" /></span></div><span className="block px-3 py-2 text-xs font-medium">{label}</span></button>
}
