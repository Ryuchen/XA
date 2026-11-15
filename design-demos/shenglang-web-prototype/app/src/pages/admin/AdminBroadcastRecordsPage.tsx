import { useMemo, useState } from 'react'
import { CircleAlert, Clock3, Edit3, Eye, EyeOff, Info, ListFilter, Megaphone, RotateCcw, Search, ShieldAlert } from 'lucide-react'
import type { AdminBroadcastRecordsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminBroadcastRecordFormDialog } from '@/pages/admin/AdminBroadcastRecordFormDialog'
import { broadcastPublicStatusLabels, formatAdminBroadcastTime, getAdminBroadcastBlockReason, getAdminBroadcastPublicStatus, initialAdminBroadcastRecords, type AdminBroadcastPublicStatus, type AdminBroadcastRecordItem } from '@/pages/admin/admin-broadcast-record-data'
import { broadcastKindLabels } from '@/pages/admin/admin-broadcast-rule-data'
import { cn } from '@/lib/utils'

interface AdminBroadcastRecordsPageProps {
  scenario: AdminBroadcastRecordsScenario
  onScenarioChange: (scenario: AdminBroadcastRecordsScenario) => void
}

const statusStyles: Record<AdminBroadcastPublicStatus, string> = {
  PUBLIC: 'border-success/25 bg-success/10 text-success',
  HIDDEN: 'border-border bg-muted text-muted-foreground',
  EXPIRED: 'border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-400',
  UNPUBLISHED: 'border-primary/25 bg-primary/10 text-primary',
  SCHEDULED: 'border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-400',
}

export function AdminBroadcastRecordsPage({ scenario, onScenarioChange }: AdminBroadcastRecordsPageProps) {
  const [records, setRecords] = useState<AdminBroadcastRecordItem[]>(initialAdminBroadcastRecords)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kindFilter, setKindFilter] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = scenario === 'save-error' || scenario === 'save-review'
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminBroadcastRecordItem>(initialAdminBroadcastRecords[0])
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const canEdit = scenario !== 'readonly'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const publicCount = records.filter(item => getAdminBroadcastPublicStatus(item) === 'PUBLIC').length
  const hiddenCount = records.filter(item => getAdminBroadcastPublicStatus(item) === 'HIDDEN').length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return records.filter(item => {
      if (keyword && !`${item.id} ${item.kind} ${broadcastKindLabels[item.kind]} ${item.content}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (kindFilter !== 'ALL' && item.kind !== kindFilter) return false
      if (statusFilter !== 'ALL' && getAdminBroadcastPublicStatus(item) !== statusFilter) return false
      return true
    })
  }, [records, scenario, keyword, kindFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setKindFilter('ALL'); setStatusFilter('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openEdit(item: AdminBroadcastRecordItem) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveRecord(item: AdminBroadcastRecordItem) {
    setRecords(current => current.map(record => record.id === item.id ? item : record))
    setFormOpen(false)
    const reason = getAdminBroadcastBlockReason(item)
    setNotice(`记录 #${item.id} 已在本地示例中保存；${reason ? `当前仍不会公开：${reason}。` : '当前已满足老板端公开展示条件。'}`)
  }
  function toggleRecord(item: AdminBroadcastRecordItem, isVisible: boolean) {
    if (!canEdit || operationsLocked) return
    if (scenario === 'action-error') { setActionError(`记录 #${item.id} 的前台显示更新失败，请重试；当前仍保持${item.isVisible ? '开启' : '关闭'}。`); return }
    const next = { ...item, isVisible }
    setRecords(current => current.map(record => record.id === item.id ? next : record))
    const reason = getAdminBroadcastBlockReason(next)
    setNotice(`记录 #${item.id} 的前台显示已${isVisible ? '开启' : '关闭'}；${reason ? `当前仍不会公开：${reason}。` : '当前已公开。'}`)
    setActionError('')
  }
  function locateReview() {
    setKeywordInput(String(editing.id)); setKeyword(String(editing.id)); setKindFilter('ALL'); setStatusFilter('ALL'); setReviewSearched(true)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="播报记录加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">播报记录加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠记录快照，不展示推测正文、可见状态或展示时间。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">播报记录</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">俱乐部动态台账</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">核对业务事件生成的历史动态，管理前台可见性与展示截止时间。</p></div>
        <dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{records.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部记录</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{publicCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">公开中</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{hiddenCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">已隐藏</dt></div></dl>
      </div>
    </section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并阅读播报记录；前台显示与截止时间配置需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>记录刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">可见性与截止时间配置已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>记录操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>记录保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">记录 #{editing.id} 的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询记录</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>公开状态由四项条件共同决定</AlertTitle><AlertDescription>仅当前台显示开启、记录已发布、已到开始时间且未过截止时间时，老板端才会展示。公开动态按记录编号倒序，每页 30 条；隐藏或到期不会删除后台记录。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Megaphone className="size-4 text-primary" />历史动态目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 条 · 状态按固定示例时间 2026-11-04 12:00 计算</CardDescription></div><Badge variant="outline" className="px-3 py-1.5"><Clock3 className="size-3.5" />只管理可见性与截止</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(240px,1fr)_220px_180px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="播报正文、事件或记录编号" placeholder="搜索正文、事件或记录编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div>
        <Select value={kindFilter} onValueChange={setKindFilter}><SelectTrigger aria-label="播报记录事件筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部事件</SelectItem>{Object.entries(broadcastKindLabels).map(([kind, label]) => <SelectItem key={kind} value={kind}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="播报记录公开状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部公开状态</SelectItem>{Object.entries(broadcastPublicStatusLabels).map(([status, label]) => <SelectItem key={status} value={status}>{label}</SelectItem>)}</SelectContent></Select>
        <Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button>
      </div>
      <div className="overflow-x-auto border-t">
        <Table className="min-w-[1040px]">
          <TableHeader><TableRow><TableHead>动态正文</TableHead><TableHead>播报事件</TableHead><TableHead>创建／发布</TableHead><TableHead>展示窗口</TableHead><TableHead>实际公开状态</TableHead><TableHead>前台显示</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(item => {
            const status = getAdminBroadcastPublicStatus(item)
            return <TableRow key={item.id}>
              <TableCell><div className="max-w-[310px]"><p className="whitespace-pre-wrap break-words text-xs font-medium leading-5">{item.content}</p><span className="mt-1.5 block font-mono text-[10px] text-muted-foreground">记录 #{item.id}</span></div></TableCell>
              <TableCell><div className="min-w-28"><Badge variant="secondary">{broadcastKindLabels[item.kind]}</Badge><p className="mt-1.5 font-mono text-[10px] text-muted-foreground">{item.kind}</p></div></TableCell>
              <TableCell><div className="min-w-32 space-y-1.5 text-[11px] tabular-nums"><p>创建 {formatAdminBroadcastTime(item.createdAt)}</p><p className="text-muted-foreground">发布 {formatAdminBroadcastTime(item.publishedAt)}</p></div></TableCell>
              <TableCell><div className="min-w-32 space-y-1.5 text-[11px] tabular-nums"><p>开始 {formatAdminBroadcastTime(item.startsAt)}</p><p className="text-muted-foreground">截止 {formatAdminBroadcastTime(item.expiresAt)}</p></div></TableCell>
              <TableCell><Badge variant="outline" className={cn('whitespace-nowrap', statusStyles[status])}>{status === 'PUBLIC' ? <Eye className="size-3" /> : <EyeOff className="size-3" />}{broadcastPublicStatusLabels[status]}</Badge>{status !== 'PUBLIC' && <p className="mt-1.5 max-w-28 text-[10px] leading-4 text-muted-foreground">{getAdminBroadcastBlockReason(item)}</p>}</TableCell>
              <TableCell><div className="flex items-center gap-2"><Switch aria-label={`前台显示 记录 ${item.id}`} checked={item.isVisible} disabled={!canEdit || operationsLocked} onCheckedChange={value => toggleRecord(item, value)} /><span className={cn('text-xs', item.isVisible ? 'text-success' : 'text-muted-foreground')}>{item.isVisible ? '开启' : '关闭'}</span></div></TableCell>
              <TableCell><div className="flex justify-end">{canEdit ? <Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />管理</Button> : <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
        {rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ListFilter className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的播报记录</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、事件和公开状态筛选后查看历史动态。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>记录、预览和保存结果均为本地示例，不发送真实消息。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    {formOpen && <AdminBroadcastRecordFormDialog key={`${editing.id}-${formOpen}`} open={formOpen} item={editing} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对记录后再操作，避免重复提交。' : ''} saveFailed={scenario === 'save-error'} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveRecord} />}
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除记录保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对当前记录及请求处理情况，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
