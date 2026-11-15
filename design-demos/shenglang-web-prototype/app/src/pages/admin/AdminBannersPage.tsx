import { useMemo, useState } from 'react'
import { CircleAlert, Edit3, ExternalLink, Image, Images, Info, Plus, RotateCcw, Search, ShieldAlert, SlidersHorizontal, Trash2 } from 'lucide-react'
import type { AdminBannersScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminBannerFormDialog } from '@/pages/admin/AdminBannerFormDialog'
import { bannerLinkTypeLabels, getBannerTarget, getBannerTargetLabel, initialAdminBanners, type AdminBannerItem } from '@/pages/admin/admin-banner-data'
import { cn } from '@/lib/utils'

interface AdminBannersPageProps {
  scenario: AdminBannersScenario
  onScenarioChange: (scenario: AdminBannersScenario) => void
}

function hasTargetIssue(item: AdminBannerItem) {
  if (item.linkType === 'NONE' || item.linkType === 'URL') return false
  const target = getBannerTarget(item)
  return !target || !target.available
}

export function AdminBannersPage({ scenario, onScenarioChange }: AdminBannersPageProps) {
  const [banners, setBanners] = useState<AdminBannerItem[]>(initialAdminBanners)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState(scenario === 'unavailable' ? 'UNAVAILABLE' : 'ALL')
  const opensForm = scenario === 'save-error' || scenario === 'save-review' || scenario === 'target-error'
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminBannerItem | null>(opensForm ? initialAdminBanners[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AdminBannerItem | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')

  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const activeCount = banners.filter(item => item.active).length
  const issueCount = banners.filter(hasTargetIssue).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return banners.filter(item => {
      const targetText = `${getBannerTargetLabel(item)} ${item.target}`
      if (keyword && !`${item.title} ${targetText}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (typeFilter !== 'ALL' && item.linkType !== typeFilter) return false
      if (statusFilter === 'ACTIVE' && !item.active) return false
      if (statusFilter === 'INACTIVE' && item.active) return false
      if (statusFilter === 'UNAVAILABLE' && !hasTargetIssue(item)) return false
      return true
    }).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
  }, [banners, scenario, keyword, typeFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setTypeFilter('ALL'); setStatusFilter('ALL')
    if (scenario === 'empty' || scenario === 'unavailable') onScenarioChange('default')
  }
  function openCreate() {
    if (!canEdit || operationsLocked) return
    setEditing(null); setFormOpen(true); setNotice(''); setActionError('')
  }
  function openEdit(item: AdminBannerItem) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveBanner(item: AdminBannerItem) {
    const id = item.id || Math.max(0, ...banners.map(banner => banner.id)) + 1
    const payload = { ...item, id }
    setBanners(current => item.id ? current.map(banner => banner.id === item.id ? payload : banner) : [...current, payload])
    setFormOpen(false)
    setNotice(`Banner「${payload.title || `ID ${payload.id}`}」已在本地示例中保存；真实首页与投放数据未修改。`)
  }
  function toggleBanner(item: AdminBannerItem, active: boolean) {
    if (!canEdit || operationsLocked) return
    if (scenario === 'action-error') { setActionError(`Banner「${item.title || `ID ${item.id}`}」状态更新失败，请重试；当前仍保持${item.active ? '启用' : '停用'}。`); return }
    setBanners(current => current.map(banner => banner.id === item.id ? { ...banner, active } : banner))
    setNotice(`Banner「${item.title || `ID ${item.id}`}」已${active ? '启用' : '停用'}${active && hasTargetIssue(item) ? '，但跳转目标仍需核对' : ''}。`)
    setActionError('')
  }
  function deleteBanner() {
    if (!deleteTarget) return
    if (scenario === 'action-error') { setActionError(`Banner「${deleteTarget.title || `ID ${deleteTarget.id}`}」删除失败，请重试；列表与老板端示例均未修改。`); setDeleteTarget(null); return }
    setBanners(current => current.filter(item => item.id !== deleteTarget.id))
    setNotice(`Banner「${deleteTarget.title || `ID ${deleteTarget.id}`}」已删除，老板端示例不再显示。`)
    setDeleteTarget(null)
  }
  function locateReview() {
    const target = editing || initialAdminBanners[0]
    setKeywordInput(target.title || String(target.id)); setKeyword(target.title || String(target.id)); setTypeFilter('ALL'); setStatusFilter('ALL'); setReviewSearched(true)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="首页轮播加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">首页轮播加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠投放快照，不展示推测图片、状态或目标。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">首页轮播</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">Banner 与运营位</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">组织老板端首屏视觉与业务跳转，持续核对目标可用性，让每一次点击都有明确去向。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{banners.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部 Banner</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{activeCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">启用中</dt></div><div className="border-l border-white/15 pl-4"><dd className={cn('text-2xl font-semibold tabular-nums', issueCount > 0 && 'text-amber-300')}>{issueCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">异常目标</dt></div></dl>{canEdit && <Button variant="secondary" disabled={operationsLocked} onClick={openCreate}><Plus className="size-4" />新增 Banner</Button>}</div>
    </div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并核对 Banner；新增、编辑和启停需要 banner:edit，删除需要 banner:delete。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑，但不可删除</AlertTitle><AlertDescription>Banner 资料与启停已开放，删除操作需要单独的 banner:delete 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>轮播刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">编辑、启停和删除已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>轮播操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>Banner 保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.title || initialAdminBanners[0].title}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询 Banner</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>目标可用性会随后续业务状态变化</AlertTitle><AlertDescription>Banner 没有独立投放排期。商品资格以老板实际账号为准；公告、玩法须启用；运营内容须启用、非弹窗且处于自身展示窗口。异常时保留记录，建议更换目标或停用。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Images className="size-4 text-primary" />轮播列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按排序值与编号升序</CardDescription></div><Badge variant="outline" className="px-3 py-1.5"><Image className="size-3.5" />图片统一 750 × 320</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(240px,1fr)_180px_180px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Banner 标题或目标" placeholder="搜索标题、目标名称或编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Select value={typeFilter} onValueChange={setTypeFilter}><SelectTrigger aria-label="Banner 类型筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部跳转类型</SelectItem>{Object.entries(bannerLinkTypeLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select><Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="Banner 状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="ACTIVE">仅启用</SelectItem><SelectItem value="INACTIVE">仅停用</SelectItem><SelectItem value="UNAVAILABLE">目标需核对</SelectItem></SelectContent></Select><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button></div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1040px]"><TableHeader><TableRow><TableHead>Banner 与封面</TableHead><TableHead>跳转目标</TableHead><TableHead>目标可用性</TableHead><TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(item => { const target = getBannerTarget(item); const issue = hasTargetIssue(item); return <TableRow key={item.id}>
        <TableCell><div className="flex items-center gap-3"><img src={item.image} alt="" className="h-[54px] w-[126px] shrink-0 rounded-lg border object-cover shadow-sm" /><div className="min-w-0"><h3 className="max-w-56 truncate text-sm font-semibold">{item.title || '无标题 Banner'}</h3><p className="mt-1 font-mono text-[10px] text-muted-foreground">ID {item.id} · 750 × 320</p><Badge variant="outline" className="mt-2 text-[10px]">{bannerLinkTypeLabels[item.linkType]}</Badge></div></div></TableCell>
        <TableCell><p className={cn('max-w-56 truncate text-sm font-medium', item.linkType === 'URL' && 'font-mono text-xs')}>{getBannerTargetLabel(item)}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.linkType === 'NONE' ? '纯视觉展示' : item.linkType === 'URL' ? '老板端点击复制链接' : target?.meta || '目标记录缺失'}</p></TableCell>
        <TableCell>{issue ? <Popover><PopoverTrigger asChild><Button variant="outline" size="sm" className="border-amber-500/40 bg-amber-500/10 text-amber-700 hover:bg-amber-500/20 dark:text-amber-400"><CircleAlert className="size-3.5" />目标需核对</Button></PopoverTrigger><PopoverContent className="w-80"><p className="text-sm font-semibold">{target?.label || '跳转目标缺失'}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">{target?.reason || '目标记录不存在，请重新选择有效目标。'}</p><p className="mt-3 border-t pt-3 text-[11px] leading-5 text-muted-foreground">Banner 记录仍保留。更换目标或停用后，可避免老板点击时收到不可用反馈。</p></PopoverContent></Popover> : item.linkType === 'NONE' ? <span className="text-xs text-muted-foreground">无需目标</span> : item.linkType === 'URL' ? <Badge variant="outline"><ExternalLink className="size-3" />点击时复制</Badge> : <Badge variant="outline" className="border-success/30 bg-success/10 text-success">目标可用</Badge>}</TableCell>
        <TableCell><span className="font-mono text-xs">{item.sortOrder}</span></TableCell>
        <TableCell><div className="flex items-center gap-2"><Switch aria-label={`启停 ${item.title || `ID ${item.id}`}`} checked={item.active} disabled={!canEdit || operationsLocked} onCheckedChange={value => toggleBanner(item, value)} /><span className={cn('text-xs', item.active ? 'text-success' : 'text-muted-foreground')}>{item.active ? '启用' : '停用'}</span></div></TableCell>
        <TableCell><div className="flex justify-end gap-2">{canEdit && <Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />编辑</Button>}{canDelete && <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" disabled={operationsLocked} onClick={() => { setDeleteTarget(item); setNotice(''); setActionError('') }}><Trash2 className="size-3.5" />删除</Button>}{!canEdit && <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
      </TableRow>})}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><SlidersHorizontal className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的 Banner</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、类型和状态筛选后查看轮播列表。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>Banner、目标与保存结果均为本地示例，不请求外部图片。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <AdminBannerFormDialog key={`${editing?.id ?? 'new'}-${formOpen}`} open={formOpen} item={editing} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对 Banner 后再操作，避免重复提交。' : ''} initialTargetError={scenario === 'target-error'} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveBanner} />

    <AlertDialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除首页 Banner？</AlertDialogTitle><AlertDialogDescription>确定删除「{deleteTarget?.title || `ID ${deleteTarget?.id}`}」吗？删除后老板端不再显示，图片与跳转配置将从当前列表示例移除。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={deleteBanner}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除 Banner 保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对 Banner 及请求处理情况。同名或同图 Banner 需另行核实，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
