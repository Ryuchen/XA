import { useMemo, useState } from 'react'
import { Archive, ArchiveRestore, Boxes, CircleAlert, Edit3, Gamepad2, Info, Layers3, Plus, RotateCcw, Search, ShieldAlert, SlidersHorizontal, Sparkles } from 'lucide-react'
import type { AdminGameplaysScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminGameplayFormDialog } from '@/pages/admin/AdminGameplayFormDialog'
import { gameplayThemes, initialAdminGameplays, type AdminGameplayItem } from '@/pages/admin/admin-gameplay-data'
import { cn } from '@/lib/utils'

interface AdminGameplaysPageProps {
  scenario: AdminGameplaysScenario
  onScenarioChange: (scenario: AdminGameplaysScenario) => void
  onConfigure: (gameplay: AdminGameplayItem) => void
}

export function AdminGameplaysPage({ scenario, onScenarioChange, onConfigure }: AdminGameplaysPageProps) {
  const [gameplays, setGameplays] = useState<AdminGameplayItem[]>(initialAdminGameplays)
  const [viewArchived, setViewArchived] = useState(scenario === 'archived')
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kindFilter, setKindFilter] = useState('ALL')
  const [formOpen, setFormOpen] = useState(scenario === 'save-error' || scenario === 'save-review')
  const [editing, setEditing] = useState<AdminGameplayItem | null>(scenario === 'save-error' || scenario === 'save-review' ? initialAdminGameplays[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(scenario === 'save-review')
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState<AdminGameplayItem | null>(null)
  const [restoreTarget, setRestoreTarget] = useState<AdminGameplayItem | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')

  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'
  const saveUnknown = scenario === 'save-review' && formOpen
  const currentGameplays = gameplays.filter(item => !item.archived)
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return gameplays.filter(item => {
      if (item.archived !== viewArchived) return false
      if (keyword && !`${item.name} ${item.code}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (kindFilter !== 'ALL' && item.kind !== kindFilter) return false
      return true
    }).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
  }, [gameplays, scenario, viewArchived, keyword, kindFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setKindFilter('ALL'); setViewArchived(false)
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openCreate() {
    if (!canEdit || saveReviewActive) return
    setEditing(null); setFormOpen(true); setNotice(''); setActionError('')
  }
  function openEdit(item: AdminGameplayItem) {
    if (!canEdit || saveReviewActive || item.archived) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveGameplay(item: AdminGameplayItem) {
    const id = item.id || Math.max(...gameplays.map(gameplay => gameplay.id)) + 1
    const payload = { ...item, id }
    setGameplays(current => item.id ? current.map(gameplay => gameplay.id === item.id ? payload : gameplay) : [...current, payload])
    setViewArchived(payload.archived)
    setFormOpen(false)
    setNotice(`玩法「${payload.name}」已在本地示例中保存；真实目录、商品与订单未修改。`)
  }
  function locateReview() {
    const target = editing || initialAdminGameplays[0]
    setKeywordInput(target.name); setKeyword(target.name); setKindFilter('ALL'); setViewArchived(target.archived); setReviewSearched(true)
  }
  function archiveGameplay() {
    if (!archiveTarget) return
    if (scenario === 'action-error') { setActionError(`玩法「${archiveTarget.name}」归档失败，请重试；当前目录未修改。`); setArchiveTarget(null); return }
    setGameplays(current => current.map(item => item.id === archiveTarget.id ? { ...item, archived: true, active: false } : item))
    setNotice(`玩法「${archiveTarget.name}」已归档；历史订单快照保留，可在已归档列表恢复。`)
    setArchiveTarget(null)
  }
  function restoreGameplay() {
    if (!restoreTarget) return
    if (scenario === 'action-error') { setActionError(`玩法「${restoreTarget.name}」恢复失败，请重试；当前仍保持归档。`); setRestoreTarget(null); return }
    setGameplays(current => current.map(item => item.id === restoreTarget.id ? { ...item, archived: false, active: false } : item))
    setViewArchived(false)
    setNotice(`玩法「${restoreTarget.name}」已恢复并保持停用，启用前请核对所属商品。`)
    setRestoreTarget(null)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="玩法配置加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">玩法目录加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠目录快照，不展示推测状态或商品关联。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">玩法配置</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">目录与规则</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">组织老板端玩法入口、主题封面与通用履约说明，让商品配置和前台展示保持一致。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{currentGameplays.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">当前玩法</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{currentGameplays.filter(item => item.active).length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">启用中</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{currentGameplays.reduce((total, item) => total + item.serviceCount, 0)}</dd><dt className="mt-1 text-[11px] text-blue-100/65">关联商品</dt></div></dl>{canEdit && <Button variant="secondary" disabled={saveReviewActive} onClick={openCreate}><Plus className="size-4" />新增玩法</Button>}</div>
    </div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选玩法并进入商品列表；新增、编辑和恢复需要 service:edit，归档需要 service:delete。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑，但不可归档</AlertTitle><AlertDescription>玩法资料与归档恢复已开放，归档操作需要单独的 service:delete 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>目录刷新失败，当前显示上次成功读取的玩法</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">状态与商品关联可能已有变化，请先重新加载。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>目录操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>玩法保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.name || initialAdminGameplays[0].name}」的保存结果未确认，请查询玩法并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询玩法</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>目录变更不会改写历史订单</AlertTitle><AlertDescription>停用会隐藏入口并阻止新下单；归档会同时隐藏所属商品。恢复后仍保持停用，历史订单始终使用下单时的玩法、价格和分账快照。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Layers3 className="size-4 text-primary" />玩法目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按排序值与编号升序</CardDescription></div><div className="flex rounded-lg border bg-muted/50 p-1" role="group" aria-label="玩法归档状态"><Button size="sm" variant={!viewArchived ? 'secondary' : 'ghost'} aria-pressed={!viewArchived} onClick={() => setViewArchived(false)}>当前玩法<Badge variant="outline" className="ml-1 px-1.5">{currentGameplays.length}</Badge></Button><Button size="sm" variant={viewArchived ? 'secondary' : 'ghost'} aria-pressed={viewArchived} onClick={() => setViewArchived(true)}>已归档<Badge variant="outline" className="ml-1 px-1.5">{gameplays.filter(item => item.archived).length}</Badge></Button></div></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 lg:grid-cols-[minmax(220px,1fr)_200px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="玩法名称或标识" placeholder="搜索玩法名称或唯一标识" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Select value={kindFilter} onValueChange={setKindFilter}><SelectTrigger aria-label="玩法类型筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部类型</SelectItem><SelectItem value="STANDARD">普通玩法</SelectItem><SelectItem value="ROULETTE">转盘单</SelectItem></SelectContent></Select><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button></div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1000px]"><TableHeader><TableRow><TableHead>玩法与封面</TableHead><TableHead>类型</TableHead><TableHead>关联商品</TableHead><TableHead>排序</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{rows.map(item => { const theme = gameplayThemes[item.theme]; return <TableRow key={item.id}>
        <TableCell><div className="flex items-center gap-3"><div className={cn('relative grid h-16 w-24 shrink-0 place-items-center overflow-hidden rounded-xl bg-gradient-to-br text-3xl font-light text-white shadow-sm', theme.className)}><span className="absolute -right-1 -top-3 text-6xl text-white/15">{theme.symbol}</span><span className="relative">{item.coverUrl ? 'IMG' : theme.symbol}</span></div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{item.name}</h3>{item.coverUrl && <Badge variant="outline">自定义封面</Badge>}</div><p className="mt-1 max-w-64 truncate text-xs text-muted-foreground">{item.subtitle || '暂无介绍'}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">ID {item.id} · {item.code}</p></div></div></TableCell>
        <TableCell><Badge variant="outline" className={item.kind === 'ROULETTE' ? 'border-violet-500/30 bg-violet-500/10 text-violet-500' : ''}>{item.kind === 'ROULETTE' ? <><Sparkles className="size-3" />转盘单</> : <><Gamepad2 className="size-3" />普通玩法</>}</Badge>{item.kind === 'ROULETTE' && <p className="mt-2 text-[11px] text-muted-foreground">商品内配置奖池</p>}</TableCell>
        <TableCell><p className="text-sm font-semibold tabular-nums">{item.serviceCount}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.serviceCount ? '个服务商品' : '尚未关联'}</p></TableCell>
        <TableCell><span className="font-mono text-xs">{item.sortOrder}</span></TableCell>
        <TableCell><Badge variant="outline" className={cn(item.active && !item.archived && 'border-success/30 bg-success/10 text-success')}>{item.archived ? '已归档' : item.active ? '启用' : '停用'}</Badge><p className="mt-2 text-[11px] text-muted-foreground">{item.archived ? '历史快照保留' : item.active ? '允许新订单' : '阻止新下单'}</p></TableCell>
        <TableCell><div className="flex justify-end gap-2">{!item.archived && <Button variant="outline" size="sm" onClick={() => onConfigure(item)}><Boxes className="size-3.5" />配置商品</Button>}{canEdit && !item.archived && <Button variant="outline" size="sm" disabled={saveReviewActive} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />编辑</Button>}{canDelete && !item.archived && <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" disabled={saveReviewActive} onClick={() => { setArchiveTarget(item); setNotice(''); setActionError('') }}><Archive className="size-3.5" />归档</Button>}{canEdit && item.archived && <Button variant="outline" size="sm" disabled={saveReviewActive} onClick={() => { setRestoreTarget(item); setNotice(''); setActionError('') }}><ArchiveRestore className="size-3.5" />恢复</Button>}{item.archived && !canEdit && <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
      </TableRow>})}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><SlidersHorizontal className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的玩法</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词和类型筛选后查看当前玩法。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>玩法、封面和保存结果均为本地示例，不请求外部图片。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <AdminGameplayFormDialog key={`${editing?.id ?? 'new'}-${formOpen}`} open={formOpen} item={editing} items={gameplays} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对玩法后再操作，避免重复提交。' : ''} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveGameplay} />

    <AlertDialog open={!!archiveTarget} onOpenChange={open => { if (!open) setArchiveTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>归档玩法？</AlertDialogTitle><AlertDialogDescription>确定归档「{archiveTarget?.name}」吗？玩法及其 {archiveTarget?.serviceCount ?? 0} 个所属商品将隐藏并停止新下单，历史订单保留，可在已归档列表恢复。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={archiveGameplay}>确认归档</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={!!restoreTarget} onOpenChange={open => { if (!open) setRestoreTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>恢复玩法到当前目录？</AlertDialogTitle><AlertDialogDescription>恢复「{restoreTarget?.name}」后仍保持停用，不会立即开放下单。请核对所属商品、平台和难度后再编辑启用。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction onClick={restoreGameplay}>确认恢复</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除玩法保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对玩法及请求处理情况。同名玩法需另行核实，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
