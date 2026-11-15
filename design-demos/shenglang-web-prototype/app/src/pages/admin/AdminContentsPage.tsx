import { useMemo, useState } from 'react'
import { CalendarDays, CircleAlert, Edit3, FileText, Image as ImageIcon, Info, Plus, RotateCcw, Search, ShieldAlert, SlidersHorizontal, Trash2 } from 'lucide-react'
import type { AdminContentsScenario } from '@/components/admin/admin-config'
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
import { AdminContentFormDialog } from '@/pages/admin/AdminContentFormDialog'
import { adminContentKindLabels, initialAdminContents, type AdminContentItem } from '@/pages/admin/admin-content-data'
import { cn } from '@/lib/utils'

interface AdminContentsPageProps {
  scenario: AdminContentsScenario
  onScenarioChange: (scenario: AdminContentsScenario) => void
}

const prototypeNow = '2026-11-04 08:00'
type ContentStatus = 'LIVE' | 'SCHEDULED' | 'ARCHIVED' | 'EXPIRED' | 'INACTIVE'
const statusLabels: Record<ContentStatus, string> = { LIVE: '展示中', SCHEDULED: '待上架', ARCHIVED: '已归档', EXPIRED: '已下架', INACTIVE: '已停用' }

function contentStatus(item: AdminContentItem): ContentStatus {
  if (!item.active) return 'INACTIVE'
  if (item.startsAt && item.startsAt > prototypeNow) return 'SCHEDULED'
  if (item.endsAt && item.endsAt <= prototypeNow) return item.kind === 'HOT' ? 'ARCHIVED' : 'EXPIRED'
  return 'LIVE'
}

export function AdminContentsPage({ scenario, onScenarioChange }: AdminContentsPageProps) {
  const [contents, setContents] = useState<AdminContentItem[]>(initialAdminContents)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kindFilter, setKindFilter] = useState(scenario === 'activities' ? 'HOT' : 'ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = scenario === 'save-error' || scenario === 'save-review'
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminContentItem | null>(opensForm ? initialAdminContents[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AdminContentItem | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')

  const canEdit = scenario !== 'readonly'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const liveCount = contents.filter(item => contentStatus(item) === 'LIVE').length
  const activityCount = contents.filter(item => item.kind === 'HOT').length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return contents.filter(item => {
      if (keyword && !`${item.id} ${item.code} ${item.title} ${item.content}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (kindFilter !== 'ALL' && item.kind !== kindFilter) return false
      if (statusFilter !== 'ALL' && contentStatus(item) !== statusFilter) return false
      return true
    }).sort((a, b) => {
      if (kindFilter === 'HOT') return (b.startsAt || b.createdAt).localeCompare(a.startsAt || a.createdAt) || b.id - a.id
      return b.sortOrder - a.sortOrder || b.id - a.id
    })
  }, [contents, scenario, keyword, kindFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setKindFilter('ALL'); setStatusFilter('ALL')
    if (scenario === 'empty' || scenario === 'activities') onScenarioChange('default')
  }
  function openCreate() {
    if (!canEdit || operationsLocked) return
    setEditing(null); setFormOpen(true); setNotice(''); setActionError('')
  }
  function openEdit(item: AdminContentItem) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveContent(item: AdminContentItem) {
    const id = item.id || Math.max(0, ...contents.map(content => content.id)) + 1
    const payload = { ...item, id, createdAt: item.createdAt || prototypeNow, updatedAt: prototypeNow }
    setContents(current => item.id ? current.map(content => content.id === item.id ? payload : content) : [...current, payload])
    setFormOpen(false)
    setNotice(`内容「${payload.title}」已在本地示例中保存；真实老板端与关联 Banner 未修改。`)
  }
  function toggleContent(item: AdminContentItem, active: boolean) {
    if (!canEdit || operationsLocked) return
    if (scenario === 'action-error') { setActionError(`内容「${item.title}」状态更新失败，请重试；当前仍保持${item.active ? '启用' : '停用'}。`); return }
    const next = { ...item, active, updatedAt: prototypeNow }
    setContents(current => current.map(content => content.id === item.id ? next : content))
    setNotice(`内容「${item.title}」已${active ? '启用' : '停用'}，当前状态：${statusLabels[contentStatus(next)]}。`)
    setActionError('')
  }
  function deleteContent() {
    if (!deleteTarget) return
    if (scenario === 'action-error') { setActionError(`内容「${deleteTarget.title}」删除失败，请重试；内容与关联 Banner 均未修改。`); setDeleteTarget(null); return }
    setContents(current => current.filter(item => item.id !== deleteTarget.id))
    setNotice(`内容「${deleteTarget.title}」已删除；指向它的 Banner 需重新核对目标。`)
    setDeleteTarget(null)
  }
  function locateReview() {
    const target = editing || initialAdminContents[0]
    setKeywordInput(target.title); setKeyword(target.title); setKindFilter('ALL'); setStatusFilter('ALL'); setReviewSearched(true)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="运营内容加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">运营内容加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠内容快照，不展示推测海报、正文或发布状态。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">运营内容管理</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">首页、活动与规则内容</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一维护老板端活动海报、会员权益与业务规范，核对上架时间和公开状态。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{contents.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部内容</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{liveCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">展示中</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{activityCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">活动与归档</dt></div></dl>{canEdit && <Button variant="secondary" disabled={operationsLocked} onClick={openCreate}><Plus className="size-4" />新增内容</Button>}</div>
    </div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并阅读运营内容；新增、编辑、启停和删除均需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>内容刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">编辑、启停和删除已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>内容操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>内容保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.title || initialAdminContents[0].title}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询内容</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>活动归档与内容下架规则不同</AlertTitle><AlertDescription>活动到期后仍可从活动目录阅读海报；其他内容到期后不再公开。未上架或停用内容不可访问，关联 Banner 的目标也会随之不可用。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><FileText className="size-4 text-primary" />内容目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 活动按上架时间，其他内容按排序值展示</CardDescription></div><Badge variant="outline" className="px-3 py-1.5"><CalendarDays className="size-3.5" />{contents.filter(item => contentStatus(item) === 'ARCHIVED').length} 条活动归档</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(220px,1fr)_180px_180px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="内容标题、标识或编号" placeholder="搜索标题、标识或内容编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Select value={kindFilter} onValueChange={setKindFilter}><SelectTrigger aria-label="内容类型筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部展示位置</SelectItem>{Object.entries(adminContentKindLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select><Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="内容状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem>{Object.entries(statusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button></div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1080px]"><TableHeader><TableRow><TableHead>内容</TableHead><TableHead>展示位置</TableHead><TableHead>上架与结束</TableHead><TableHead>排序</TableHead><TableHead>公开状态</TableHead><TableHead>启用</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(item => { const status = contentStatus(item); return <TableRow key={item.id}>
        <TableCell><div className="flex max-w-[390px] items-center gap-3">{item.imageUrl.startsWith('./') || item.imageUrl.startsWith('/') ? <img src={item.imageUrl} alt="" className={cn('shrink-0 rounded-lg border bg-[#020617] object-cover', item.kind === 'HOT' ? 'h-16 w-12' : 'h-12 w-20')} /> : <span className="grid size-12 shrink-0 place-items-center rounded-lg border bg-muted/40">{item.kind === 'HOT' ? <ImageIcon className="size-5 text-muted-foreground" /> : <FileText className="size-5 text-muted-foreground" />}</span>}<div className="min-w-0"><h3 className="truncate text-sm font-semibold" title={item.title}>{item.title}</h3><p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">#{item.id} · {item.code}</p>{item.kind !== 'HOT' && <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{item.content.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim() || '暂无正文'}</p>}</div></div></TableCell>
        <TableCell><Badge variant="secondary">{adminContentKindLabels[item.kind]}</Badge></TableCell>
        <TableCell><p className="text-xs tabular-nums">{item.startsAt || '立即上架'}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.endsAt ? `${item.kind === 'HOT' ? '结束' : '下架'} ${item.endsAt}` : '未设结束时间'}</p></TableCell>
        <TableCell><span className="font-mono text-xs">{item.kind === 'HOT' ? '按上架时间' : item.sortOrder}</span></TableCell>
        <TableCell><Badge variant="outline" className={cn(status === 'LIVE' && 'border-success/30 text-success', status === 'SCHEDULED' && 'border-primary/30 text-primary', status === 'ARCHIVED' && 'border-muted-foreground/30 text-muted-foreground', (status === 'EXPIRED' || status === 'INACTIVE') && 'text-muted-foreground')}>{statusLabels[status]}</Badge></TableCell>
        <TableCell><Switch aria-label={`启停 ${item.title}`} checked={item.active} disabled={!canEdit || operationsLocked} onCheckedChange={value => toggleContent(item, value)} /></TableCell>
        <TableCell><div className="flex justify-end gap-2">{canEdit ? <><Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />编辑</Button><Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" disabled={operationsLocked} onClick={() => { setDeleteTarget(item); setNotice(''); setActionError('') }}><Trash2 className="size-3.5" />删除</Button></> : <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
      </TableRow> })}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><SlidersHorizontal className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的运营内容</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、展示位置和状态筛选后查看内容目录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>海报、内容和保存结果均为本地示例，不请求外部内容。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <AdminContentFormDialog key={`${editing?.id ?? 'new'}-${formOpen}`} open={formOpen} item={editing} existingCodes={contents.map(item => item.code)} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对内容后再操作，避免重复提交。' : ''} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveContent} />
    <AlertDialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除运营内容？</AlertDialogTitle><AlertDialogDescription>确定删除「{deleteTarget?.title}」吗？删除后公开列表与详情不再展示，活动归档也不会保留，指向它的 Banner 需要更换目标。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={deleteContent}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除内容保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对内容及请求处理情况。同标识内容需另行核实，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
