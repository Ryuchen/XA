import { useMemo, useState } from 'react'
import { Bell, CircleAlert, Edit3, Info, Pin, Plus, RotateCcw, Search, ShieldAlert, SlidersHorizontal, Trash2 } from 'lucide-react'
import type { AdminAnnouncementsScenario } from '@/components/admin/admin-config'
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
import { AdminAnnouncementFormDialog } from '@/pages/admin/AdminAnnouncementFormDialog'
import { initialAdminAnnouncements, type AdminAnnouncementItem } from '@/pages/admin/admin-announcement-data'
import { cn } from '@/lib/utils'

interface AdminAnnouncementsPageProps {
  scenario: AdminAnnouncementsScenario
  onScenarioChange: (scenario: AdminAnnouncementsScenario) => void
}

const prototypeTimestamp = '2026-11-04 07:30'

export function AdminAnnouncementsPage({ scenario, onScenarioChange }: AdminAnnouncementsPageProps) {
  const [announcements, setAnnouncements] = useState<AdminAnnouncementItem[]>(initialAdminAnnouncements)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [pinFilter, setPinFilter] = useState(scenario === 'pinned' ? 'PINNED' : 'ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = scenario === 'save-error' || scenario === 'save-review'
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminAnnouncementItem | null>(opensForm ? initialAdminAnnouncements[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AdminAnnouncementItem | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')

  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const activeCount = announcements.filter(item => item.active).length
  const pinnedCount = announcements.filter(item => item.pinned).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return announcements.filter(item => {
      if (keyword && !`${item.id} ${item.title} ${item.content}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (pinFilter === 'PINNED' && !item.pinned) return false
      if (pinFilter === 'NORMAL' && item.pinned) return false
      if (statusFilter === 'ACTIVE' && !item.active) return false
      if (statusFilter === 'INACTIVE' && item.active) return false
      return true
    }).sort((a, b) => Number(b.pinned) - Number(a.pinned) || a.sortOrder - b.sortOrder || b.createdAt.localeCompare(a.createdAt))
  }, [announcements, scenario, keyword, pinFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setPinFilter('ALL'); setStatusFilter('ALL')
    if (scenario === 'empty' || scenario === 'pinned') onScenarioChange('default')
  }
  function openCreate() {
    if (!canEdit || operationsLocked) return
    setEditing(null); setFormOpen(true); setNotice(''); setActionError('')
  }
  function openEdit(item: AdminAnnouncementItem) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveAnnouncement(item: AdminAnnouncementItem) {
    const id = item.id || Math.max(0, ...announcements.map(announcement => announcement.id)) + 1
    const payload = { ...item, id, createdAt: item.createdAt || prototypeTimestamp, updatedAt: prototypeTimestamp }
    setAnnouncements(current => item.id ? current.map(announcement => announcement.id === item.id ? payload : announcement) : [...current, payload])
    setFormOpen(false)
    setNotice(`公告「${payload.title}」已在本地示例中保存；真实老板端与关联 Banner 未修改。`)
  }
  function toggleAnnouncement(item: AdminAnnouncementItem, active: boolean) {
    if (!canEdit || operationsLocked) return
    if (scenario === 'action-error') { setActionError(`公告「${item.title}」状态更新失败，请重试；当前仍保持${item.active ? '启用' : '停用'}。`); return }
    setAnnouncements(current => current.map(announcement => announcement.id === item.id ? { ...announcement, active, updatedAt: prototypeTimestamp } : announcement))
    setNotice(`公告「${item.title}」已${active ? '启用，老板端可按当前顺序读取' : '停用，首页、列表和详情不再展示'}。`)
    setActionError('')
  }
  function deleteAnnouncement() {
    if (!deleteTarget) return
    if (scenario === 'action-error') { setActionError(`公告「${deleteTarget.title}」删除失败，请重试；公告与关联 Banner 均未修改。`); setDeleteTarget(null); return }
    setAnnouncements(current => current.filter(item => item.id !== deleteTarget.id))
    setNotice(`公告「${deleteTarget.title}」已删除；指向它的 Banner 需重新核对目标。`)
    setDeleteTarget(null)
  }
  function locateReview() {
    const target = editing || initialAdminAnnouncements[0]
    setKeywordInput(target.title); setKeyword(target.title); setPinFilter('ALL'); setStatusFilter('ALL'); setReviewSearched(true)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="公告管理加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">公告管理加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠公告快照，不展示推测正文、顺序或状态。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">公告管理</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">官方信息与使用说明</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一发布服务调整、活动和规则说明，控制老板端阅读顺序与可见状态。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{announcements.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部公告</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{activeCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">启用中</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{pinnedCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">置顶公告</dt></div></dl>{canEdit && <Button variant="secondary" disabled={operationsLocked} onClick={openCreate}><Plus className="size-4" />新增公告</Button>}</div>
    </div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并阅读公告；新增、编辑和启停需要 announcement:edit，删除需要 announcement:delete。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑，但不可删除</AlertTitle><AlertDescription>公告资料与启停已开放，删除操作需要单独的 announcement:delete 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>公告刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">编辑、启停和删除已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>公告操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>公告保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.title || initialAdminAnnouncements[0].title}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询公告</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>启停会同时影响首页、公告列表与详情</AlertTitle><AlertDescription>公告没有独立投放排期。首页最多读取 8 条，列表最多 20 条；停用或删除后详情不可访问，指向该公告的 Banner 也会变为不可用。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Bell className="size-4 text-primary" />公告列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 置顶优先，再按排序值与发布时间排列</CardDescription></div><Badge variant="outline" className="px-3 py-1.5"><Pin className="size-3.5" />{pinnedCount} 条置顶</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(240px,1fr)_180px_180px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="公告标题、正文或编号" placeholder="搜索标题、正文或公告编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Select value={pinFilter} onValueChange={setPinFilter}><SelectTrigger aria-label="公告置顶筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部公告</SelectItem><SelectItem value="PINNED">仅置顶</SelectItem><SelectItem value="NORMAL">仅普通公告</SelectItem></SelectContent></Select><Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="公告状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="ACTIVE">仅启用</SelectItem><SelectItem value="INACTIVE">仅停用</SelectItem></SelectContent></Select><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button></div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1040px]"><TableHeader><TableRow><TableHead>公告标题与正文</TableHead><TableHead>置顶</TableHead><TableHead>排序</TableHead><TableHead>最近更新</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(item => <TableRow key={item.id}>
        <TableCell><div className="max-w-[430px]"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{item.title}</h3><span className="font-mono text-[10px] text-muted-foreground">#{item.id}</span></div><p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.content || '暂无公告正文'}</p>{item.content && <details className="mt-1 text-xs"><summary className="w-fit cursor-pointer rounded text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring">查看正文原文</summary><p className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/50 p-3 leading-5 text-muted-foreground phone-scrollbar">{item.content}</p></details>}</div></TableCell>
        <TableCell>{item.pinned ? <Badge variant="secondary"><Pin className="size-3" />置顶</Badge> : <span className="text-xs text-muted-foreground">普通</span>}</TableCell>
        <TableCell><span className="font-mono text-xs">{item.sortOrder}</span></TableCell>
        <TableCell><p className="text-xs tabular-nums">{item.updatedAt}</p><p className="mt-1 text-[11px] text-muted-foreground">发布于 {item.createdAt.slice(0, 10)}</p></TableCell>
        <TableCell><div className="flex items-center gap-2"><Switch aria-label={`启停 ${item.title}`} checked={item.active} disabled={!canEdit || operationsLocked} onCheckedChange={value => toggleAnnouncement(item, value)} /><span className={cn('text-xs', item.active ? 'text-success' : 'text-muted-foreground')}>{item.active ? '启用' : '停用'}</span></div></TableCell>
        <TableCell><div className="flex justify-end gap-2">{canEdit && <Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />编辑</Button>}{canDelete && <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" disabled={operationsLocked} onClick={() => { setDeleteTarget(item); setNotice(''); setActionError('') }}><Trash2 className="size-3.5" />删除</Button>}{!canEdit && <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><SlidersHorizontal className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的公告</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、置顶和状态筛选后查看公告列表。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>公告、顺序和保存结果均为本地示例，不请求外部内容。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <AdminAnnouncementFormDialog key={`${editing?.id ?? 'new'}-${formOpen}`} open={formOpen} item={editing} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对公告后再操作，避免重复提交。' : ''} saveFailed={scenario === 'save-error'} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveAnnouncement} />

    <AlertDialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除平台公告？</AlertDialogTitle><AlertDialogDescription>确定删除「{deleteTarget?.title}」吗？删除后老板端首页、公告列表和详情不再展示，指向它的 Banner 也需要更换目标。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={deleteAnnouncement}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除公告保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对公告及请求处理情况。同名公告需另行核实，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
