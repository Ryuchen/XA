import { useMemo, useState } from 'react'
import {
  CalendarClock,
  Check,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  FileText,
  Gamepad2,
  Headset,
  Info,
  Mail,
  RotateCcw,
  Search,
  ShieldAlert,
  UserRound,
  X,
} from 'lucide-react'
import type { AdminAuditionSignupsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { initialAdminAuditionSignups, type AdminAuditionSignup, type AuditionSignupStatus } from '@/pages/admin/admin-audition-signup-data'
import { cn } from '@/lib/utils'

interface AdminAuditionSignupsPageProps {
  scenario: AdminAuditionSignupsScenario
  onScenarioChange: (scenario: AdminAuditionSignupsScenario) => void
}

const statusLabel: Record<AuditionSignupStatus, string> = { PENDING: '待审核', APPROVED: '已通过', REJECTED: '已驳回' }

function identity(row: AdminAuditionSignup) {
  if (row.applicantNo) return `${row.applicantNo} · ${row.applicantUsername}`
  if (row.legacy) return `旧账号 · ${row.applicantUsername}`
  return '身份待核对'
}

export function AdminAuditionSignupsPage({ scenario, onScenarioChange }: AdminAuditionSignupsPageProps) {
  const [signups, setSignups] = useState<AdminAuditionSignup[]>(initialAdminAuditionSignups)
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [linkId, setLinkId] = useState<'ALL' | number>('ALL')
  const [status, setStatus] = useState<'ALL' | AuditionSignupStatus>(scenario === 'approved' ? 'APPROVED' : scenario === 'rejected' ? 'REJECTED' : 'ALL')
  const [detail, setDetail] = useState<AdminAuditionSignup | null>(null)
  const [review, setReview] = useState<{ row: AdminAuditionSignup; action: 'approve' | 'reject' } | null>(null)
  const [reason, setReason] = useState('')
  const [reviewError, setReviewError] = useState('')
  const [reviewChecked, setReviewChecked] = useState(false)
  const [reviewText, setReviewText] = useState('')
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [feedback, setFeedback] = useState<{ text: string; tone: 'success' | 'warning' } | null>(null)

  const canAudit = scenario !== 'readonly'
  const reviewPending = scenario === 'review'
  const activities = useMemo(() => [...new Map(signups.map(row => [row.linkId, row.linkTitle])).entries()].map(([id, title]) => ({ id, title })), [signups])
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = appliedKeyword.trim().toLocaleLowerCase()
    return signups.filter(row => {
      const matchesKeyword = !query || [row.applicantName, row.applicantNo, row.applicantUsername, row.game, row.contact].some(value => value.toLocaleLowerCase().includes(query))
      return matchesKeyword && (linkId === 'ALL' || row.linkId === linkId) && (status === 'ALL' || row.status === status)
    })
  }, [appliedKeyword, linkId, scenario, signups, status])

  function notify(text: string, tone: 'success' | 'warning' = 'success') {
    setFeedback({ text, tone })
  }

  function openReview(row: AdminAuditionSignup, action: 'approve' | 'reject') {
    if (!canAudit || reviewPending || row.status !== 'PENDING') return
    if (scenario === 'read-error') { notify('报名读取失败，请刷新后重试；本次未变更审核状态。', 'warning'); return }
    if (scenario === 'conflict') { notify('该报名已被其他审核人处理，请读取最新状态。', 'warning'); return }
    setReview({ row, action })
    setReason('')
    setReviewError('')
  }

  function submitReview() {
    if (!review || !canAudit || reviewPending) return
    const trimmed = reason.trim()
    if (review.action === 'reject' && !trimmed) { setReviewError('请填写驳回原因'); return }
    const nextStatus: AuditionSignupStatus = review.action === 'approve' ? 'APPROVED' : 'REJECTED'
    setSignups(items => items.map(item => item.id === review.row.id ? { ...item, status: nextStatus, auditRemark: review.action === 'reject' ? trimmed : '', auditorName: '运营管理员', auditedAt: '刚刚 · 示例' } : item))
    notify(review.action === 'approve' ? '报名已在原型中通过；未开通账号或发送真实通知。' : '报名已在原型中驳回；未发送真实通知。')
    setReview(null)
  }

  function clearFilters() {
    setKeyword('')
    setAppliedKeyword('')
    setLinkId('ALL')
    setStatus('ALL')
    if (scenario === 'empty' || scenario === 'approved' || scenario === 'rejected') onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="试音报名加载中"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-20 rounded-xl" /><Skeleton className="h-[500px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">试音报名加载失败</CardTitle><CardDescription className="mt-2">请检查网络或报名查看权限后重试，当前没有展示推测记录。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">试音审核</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">示例报名</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">核对报名人、擅长游戏和联系方式，再决定通过或驳回。审核不代替陪玩账号开通。</p></div>
      <div className="flex items-end gap-5"><dl className="grid grid-cols-2 gap-5"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{signups.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部报名</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{signups.filter(row => row.status === 'PENDING').length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">待审核</dt></div></dl><Button variant="outline" className="border-white/25 bg-white/10 text-white shadow-none hover:bg-white/20 hover:text-white" onClick={() => setStatus('PENDING')}><ClipboardCheck className="size-4" />只看待审核</Button></div>
    </div></section>

    <Alert><Info className="size-4" /><AlertTitle>审核结果与账号开通是两个独立步骤</AlertTitle><AlertDescription>通过或驳回只更新报名状态，并尝试创建站内通知；账号开通和通知送达需要另行核对。本页不会发送真实消息。</AlertDescription></Alert>
    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可查看报名与审核记录，通过和驳回操作需要试音审核权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>报名更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">审核前请重新读取，避免处理旧状态。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {reviewPending && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>审核结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">报名 #5011 · 青禾的原请求可能仍在处理，其他审核操作已锁定。<Button variant="link" className="h-auto p-0" onClick={() => { setReviewChecked(true); setReviewText('当前：已通过；这是读取时快照。') }}>读取当前状态</Button>{reviewText && <span className="text-xs">{reviewText}</span>}<Button variant="link" className="h-auto p-0" disabled={!reviewChecked} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><Headset className="size-4 text-primary" />试音报名记录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按提交时间倒序</CardDescription></div><Badge variant="outline" className="font-normal">全部数据均为示例</Badge></div>
      <form data-admin-filter className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); setAppliedKeyword(keyword) }}><div className="min-w-56 flex-1 space-y-2"><Label htmlFor="signup-keyword">搜索报名</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="signup-keyword" value={keyword} onChange={event => { setKeyword(event.target.value); if (!event.target.value) setAppliedKeyword('') }} placeholder="账号编号、昵称、游戏或联系方式" className="h-10 pl-9" /></div></div><div className="space-y-2"><Label htmlFor="signup-activity">试音活动</Label><Select value={String(linkId)} onValueChange={value => setLinkId(value === 'ALL' ? 'ALL' : Number(value))}><SelectTrigger id="signup-activity" className="h-10 w-56" aria-label="试音活动"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部活动</SelectItem>{activities.map(activity => <SelectItem key={activity.id} value={String(activity.id)}>{activity.title} · #{activity.id}</SelectItem>)}</SelectContent></Select></div><div className="space-y-2"><Label htmlFor="signup-status">审核状态</Label><Select value={status} onValueChange={value => setStatus(value as 'ALL' | AuditionSignupStatus)}><SelectTrigger id="signup-status" className="h-10 w-36" aria-label="审核状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="PENDING">待审核</SelectItem><SelectItem value="APPROVED">已通过</SelectItem><SelectItem value="REJECTED">已驳回</SelectItem></SelectContent></Select></div><Button type="submit" variant="outline" className="h-10 min-w-20 whitespace-nowrap">查询</Button></form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-full lg:min-w-[960px]"><TableHeader><TableRow><TableHead>活动 / 报名编号</TableHead><TableHead>报名人 / 账号</TableHead><TableHead>游戏 / 联系方式</TableHead><TableHead>审核 / 提交时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id}>
        <TableCell><div className="flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary"><FileText className="size-4" /></span><div><h2 className="text-sm font-medium">{row.linkTitle}</h2><p className="mt-1 font-mono text-[10px] text-muted-foreground">报名 #{row.id} · 活动 #{row.linkId}</p></div></div></TableCell>
        <TableCell><div className="flex items-center gap-2"><UserRound className="size-4 text-primary" /><strong className="text-sm font-medium">{row.applicantName}</strong></div><p className="mt-1 text-[11px] text-muted-foreground">{identity(row)}</p></TableCell>
        <TableCell><p className="flex items-center gap-2 text-xs"><Gamepad2 className="size-4 text-primary" />{row.game}</p><p className="mt-1 text-[11px] text-muted-foreground">联系 {row.contact}</p></TableCell>
        <TableCell><Badge variant="outline" className={cn(row.status === 'PENDING' ? 'border-primary/30 bg-primary/10 text-primary' : row.status === 'APPROVED' ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive')}>{statusLabel[row.status]}</Badge><p className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground"><CalendarClock className="size-3.5" />{row.createdAt}</p></TableCell>
        <TableCell><div className="flex justify-end gap-1.5"><Button variant="outline" size="sm" onClick={() => setDetail(row)}>详情</Button>{canAudit && row.status === 'PENDING' && <><Button size="sm" disabled={reviewPending} onClick={() => openReview(row, 'approve')}><Check className="size-3.5" />通过</Button><Button variant="ghost" size="sm" disabled={reviewPending} className="text-destructive hover:text-destructive" onClick={() => openReview(row, 'reject')}><X className="size-3.5" />驳回</Button></>}</div></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ClipboardCheck className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有符合筛选条件的试音报名</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词与审核状态后查看全部记录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>报名资料、账号与联系方式均为示例。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!detail} onOpenChange={open => { if (!open) setDetail(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>报名详情</DialogTitle><DialogDescription>报名 #{detail?.id} · 列表读取时的信息</DialogDescription></DialogHeader>{detail && <dl className="grid gap-x-6 rounded-xl border bg-muted/20 p-4 sm:grid-cols-2">{[
      ['试音活动', detail.linkTitle], ['报名人', `${detail.applicantName} · ${identity(detail)}`], ['擅长游戏', detail.game], ['联系方式', detail.contact], ['报名备注', detail.remark || '—'], ['审核状态', statusLabel[detail.status]], ...(detail.status === 'REJECTED' ? [['驳回原因', detail.auditRemark || '—']] : []), ['审核人', detail.auditorName || '—'], ['提交时间', detail.createdAt], ['审核时间', detail.auditedAt || '—'],
    ].map(([label, value]) => <div key={label} className="border-b py-3 last:border-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{value}</dd></div>)}</dl>}<DialogFooter><Button variant="outline" onClick={() => setDetail(null)}>关闭</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={!!review} onOpenChange={open => { if (!open) setReview(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{review?.action === 'approve' ? '通过试音报名' : '驳回试音报名'}</DialogTitle><DialogDescription>报名 #{review?.row.id} · 已重新读取报名资料</DialogDescription></DialogHeader>{review && <><dl className="grid gap-x-6 rounded-xl border bg-muted/20 p-4 sm:grid-cols-2">{[['试音活动', review.row.linkTitle], ['报名人', `${review.row.applicantName} · ${identity(review.row)}`], ['擅长游戏', review.row.game], ['联系方式', review.row.contact], ['报名备注', review.row.remark || '—']].map(([label, value]) => <div key={label} className="border-b py-3 last:border-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{value}</dd></div>)}</dl>{review.action === 'reject' && <div className="space-y-2"><Label htmlFor="signup-reject-reason">驳回原因</Label><Textarea id="signup-reject-reason" value={reason} onChange={event => setReason(event.target.value)} maxLength={255} rows={3} placeholder="填写反馈给报名人的原因" /><p className="text-[11px] text-muted-foreground">必填，最多 255 字；将随审核结果反馈给报名人。</p></div>}{reviewError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertDescription>{reviewError}</AlertDescription></Alert>}<Alert><Mail className="size-4" /><AlertDescription>提交将更新报名状态，并尝试创建站内通知。账号开通和通知送达需另外核对。</AlertDescription></Alert></>}<DialogFooter><Button variant="outline" onClick={() => setReview(null)}>返回</Button><Button variant={review?.action === 'reject' ? 'destructive' : 'default'} onClick={submitReview}>{review?.action === 'approve' ? '确认通过' : '确认驳回'}</Button></DialogFooter></DialogContent></Dialog>

    <Dialog open={releaseOpen} onOpenChange={setReleaseOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>解除待核对限制</DialogTitle><DialogDescription>当前状态是读取时快照，原请求仍可能处理。确认已核对并解除限制？</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setReleaseOpen(false)}>继续核对</Button><Button onClick={() => { setReleaseOpen(false); onScenarioChange('default') }}>已核对，解除</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl">{feedback.tone === 'success' ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />}<span className="leading-6">{feedback.text}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback(null)} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
