import { useMemo, useState } from 'react'
import { Boxes, CircleAlert, Edit3, Filter, Gamepad2, Gift, Info, Layers3, PackagePlus, Plus, RotateCcw, Search, Settings2, ShieldAlert, Trash2 } from 'lucide-react'
import type { AdminServicesScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminServiceFormDialog } from '@/pages/admin/AdminServiceFormDialog'
import { formatServiceRaw, initialAdminGameCategories, initialAdminServiceCategories, initialAdminServices, serviceCommissionLabel, type AdminGameCategory, type AdminServiceCategory, type AdminServiceItem } from '@/pages/admin/admin-service-data'
import { cn } from '@/lib/utils'

interface AdminServicesPageProps {
  scenario: AdminServicesScenario
  initialGameplay?: string
  onScenarioChange: (scenario: AdminServicesScenario) => void
}

const platformLabel = { MOBILE: '手游', PC: '端游' } as const
const securityLabel = { CONFIDENTIAL: '机密', TOP_SECRET: '绝密', ANY: '不限难度' } as const

export function AdminServicesPage({ scenario, initialGameplay, onScenarioChange }: AdminServicesPageProps) {
  const [services, setServices] = useState<AdminServiceItem[]>(initialAdminServices)
  const [gameCategories, setGameCategories] = useState<AdminGameCategory[]>(initialAdminGameCategories)
  const [serviceCategories, setServiceCategories] = useState<AdminServiceCategory[]>(initialAdminServiceCategories)
  const [keywordInput, setKeywordInput] = useState('')
  const [itemIdInput, setItemIdInput] = useState('')
  const [applied, setApplied] = useState({ keyword: '', itemId: '' })
  const [gameFilter, setGameFilter] = useState<number | null>(null)
  const [gameplayFilter, setGameplayFilter] = useState(initialGameplay ?? 'ALL')
  const [platformFilter, setPlatformFilter] = useState('ALL')
  const [securityFilter, setSecurityFilter] = useState('ALL')
  const [categoryFilter, setCategoryFilter] = useState('ALL')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [metadataResolved, setMetadataResolved] = useState(false)
  const [formOpen, setFormOpen] = useState(scenario === 'save-error' || scenario === 'save-review')
  const [editing, setEditing] = useState<AdminServiceItem | null>(scenario === 'save-error' || scenario === 'save-review' ? initialAdminServices[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(scenario === 'save-review')
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AdminServiceItem | null>(null)
  const [notice, setNotice] = useState('')
  const [dictionary, setDictionary] = useState<'game' | 'service' | null>(null)
  const [dictionaryName, setDictionaryName] = useState('')
  const [dictionaryGift, setDictionaryGift] = useState('PLAY')
  const [dictionaryError, setDictionaryError] = useState('')

  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'
  const metadataLoading = scenario === 'metadata-loading' && !metadataResolved
  const metadataError = scenario === 'metadata-error' && !metadataResolved
  const metadataReady = !metadataLoading && !metadataError
  const saveUnknown = scenario === 'save-review' && formOpen
  const advancedCount = [platformFilter !== 'ALL', securityFilter !== 'ALL', categoryFilter !== 'ALL'].filter(Boolean).length

  function categoryFor(item: AdminServiceItem) {
    return serviceCategories.find(category => category.id === item.serviceCategoryId)
  }
  function isGift(item: AdminServiceItem) {
    return categoryFor(item)?.isGift ?? false
  }
  function isDisplayActive(item: AdminServiceItem) {
    const game = gameCategories.find(category => category.id === item.gameCategoryId)
    return item.active && (game?.active ?? true) && (categoryFor(item)?.active ?? true)
  }

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return services.filter(item => {
      if (applied.keyword && !item.name.toLocaleLowerCase().includes(applied.keyword.toLocaleLowerCase())) return false
      if (applied.itemId && String(item.id) !== applied.itemId) return false
      if (gameFilter !== null && item.gameCategoryId !== gameFilter) return false
      if (gameplayFilter !== 'ALL' && item.gameplay !== gameplayFilter) return false
      if (platformFilter !== 'ALL' && item.platform !== platformFilter) return false
      if (securityFilter !== 'ALL' && item.securityLevel !== securityFilter) return false
      if (categoryFilter !== 'ALL' && String(item.serviceCategoryId) !== categoryFilter) return false
      return true
    }).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
  }, [services, scenario, applied, gameFilter, gameplayFilter, platformFilter, securityFilter, categoryFilter])

  function applySearch() {
    setApplied({ keyword: keywordInput.trim(), itemId: itemIdInput.trim() })
  }
  function clearFilters() {
    setKeywordInput(''); setItemIdInput(''); setApplied({ keyword: '', itemId: '' }); setGameFilter(null); setGameplayFilter('ALL'); setPlatformFilter('ALL'); setSecurityFilter('ALL'); setCategoryFilter('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openCreate() {
    if (!canEdit || !metadataReady || saveReviewActive) return
    setEditing(null); setFormOpen(true); setNotice('')
  }
  function openEdit(item: AdminServiceItem) {
    if (!canEdit || !metadataReady || saveReviewActive) return
    setEditing(item); setFormOpen(true); setNotice('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveService(item: AdminServiceItem) {
    const id = item.id || Math.max(...services.map(service => service.id)) + 1
    const payload = { ...item, id }
    setServices(current => item.id ? current.map(service => service.id === item.id ? payload : service) : [...current, payload])
    setFormOpen(false)
    setNotice(`商品「${payload.name}」已在本地示例中保存；真实商品、价格与抽成配置未修改。`)
  }
  function locateReview() {
    const name = editing?.name || initialAdminServices[0].name
    const id = editing?.id || initialAdminServices[0].id
    setKeywordInput(id ? '' : name); setItemIdInput(id ? String(id) : ''); setApplied({ keyword: id ? '' : name, itemId: id ? String(id) : '' })
    setGameFilter(null); setGameplayFilter('ALL'); setPlatformFilter('ALL'); setSecurityFilter('ALL'); setCategoryFilter('ALL'); setReviewSearched(true)
  }
  function openDictionary(kind: 'game' | 'service') {
    setDictionary(kind); setDictionaryName(''); setDictionaryError(''); setDictionaryGift('PLAY')
  }
  function addDictionaryItem() {
    const name = dictionaryName.trim()
    if (!name) { setDictionaryError('请输入名称'); return }
    if (dictionary === 'game') {
      if (gameCategories.some(category => category.name === name)) { setDictionaryError('游戏类目已存在'); return }
      setGameCategories(current => [...current, { id: Math.max(...current.map(item => item.id)) + 1, name, active: true, sortOrder: Math.max(...current.map(item => item.sortOrder)) + 10, escortCount: 0 }])
    } else {
      if (serviceCategories.some(category => category.name === name && category.isGift === (dictionaryGift === 'GIFT'))) { setDictionaryError('该分类已存在'); return }
      setServiceCategories(current => [...current, { id: Math.max(...current.map(item => item.id)) + 1, name, isGift: dictionaryGift === 'GIFT', active: true, sortOrder: Math.max(...current.map(item => item.sortOrder)) + 10 }])
    }
    setDictionaryName(''); setDictionaryError(''); setNotice(`已新增${dictionary === 'game' ? '游戏类目' : '服务分类'}「${name}」，仅用于本地原型。`)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="服务商品加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">服务商品加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠商品快照，不展示推测价格、状态或抽成。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">服务商品</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">商品与价目</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一核对陪玩服务、附加费用和礼物的价格、下单方式、接单资格与抽成规则。</p></div>
      <div className="flex flex-wrap items-end gap-4"><dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{services.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部商品</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{services.filter(isDisplayActive).length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">当前展示</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{services.filter(isGift).length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">礼物商品</dt></div></dl>{canEdit && <Button variant="secondary" disabled={!metadataReady || saveReviewActive} onClick={openCreate}><Plus className="size-4" />新增服务项</Button>}</div>
    </div></section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并核对商品；新增、编辑和类目设置需要 service:edit，删除需要 service:delete。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑，但不可删除</AlertTitle><AlertDescription>商品与类目配置已开放，删除操作需要单独的 service:delete 权限。</AlertDescription></Alert>}
    {metadataLoading && <Alert><RotateCcw className="size-4 animate-spin" /><AlertTitle>正在读取商品配置</AlertTitle><AlertDescription>游戏类目、服务分类、玩法与接单资格读取完成前，编辑操作暂不可用。</AlertDescription></Alert>}
    {metadataError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>商品配置读取失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">暂不能新增或编辑商品，现有列表仅供核对。<Button variant="link" className="h-auto p-0" onClick={() => setMetadataResolved(true)}>重新读取</Button></AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>列表刷新失败，当前显示上次成功读取的商品</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">价格与状态可能已有变化，请先重新加载。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>商品保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.name || initialAdminServices[0].name}」的保存结果未确认，请查询商品并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询商品</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>商品上架不等于前台一定展示</AlertTitle><AlertDescription>展示状态同时受商品、游戏类目、服务分类与玩法启用情况影响；已有订单继续使用下单时的商品与分账快照。</AlertDescription></Alert>

    <section aria-label="按游戏查看商品" className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
      <button type="button" aria-pressed={gameFilter === null} onClick={() => setGameFilter(null)} className={cn('rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent', gameFilter === null && 'border-primary ring-1 ring-primary')}><span className="flex items-center gap-2 text-sm font-medium"><Layers3 className="size-4 text-primary" />全部游戏</span><strong className="mt-3 block text-2xl font-semibold tabular-nums">{services.filter(item => item.gameCategoryId !== null).length}</strong><span className="mt-1 block text-[11px] text-muted-foreground">{gameCategories.length} 个游戏类目</span></button>
      {gameCategories.map(category => { const items = services.filter(item => item.gameCategoryId === category.id); return <button type="button" key={category.id} aria-pressed={gameFilter === category.id} onClick={() => setGameFilter(category.id)} className={cn('rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent', gameFilter === category.id && 'border-primary ring-1 ring-primary')}><span className="flex items-center justify-between gap-2 text-sm font-medium"><span className="flex items-center gap-2"><Gamepad2 className="size-4 text-primary" />{category.name}</span>{!category.active && <Badge variant="outline">停用</Badge>}</span><strong className="mt-3 block text-2xl font-semibold tabular-nums">{items.filter(isDisplayActive).length}</strong><span className="mt-1 block text-[11px] text-muted-foreground">{items.length} 项服务 · {category.escortCount} 位陪玩</span></button> })}
    </section>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Boxes className="size-4 text-primary" />商品列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 归档玩法商品不在此列表</CardDescription></div>{canEdit && <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!metadataReady} onClick={() => openDictionary('game')}><Settings2 className="size-4" />游戏类目</Button><Button variant="outline" disabled={!metadataReady} onClick={() => openDictionary('service')}><Settings2 className="size-4" />服务分类</Button></div>}</CardHeader>
      <div className="space-y-3 border-t p-4">
        <div data-admin-filter className="grid gap-3 lg:grid-cols-[minmax(180px,1fr)_160px_180px_auto]"><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="商品名称" placeholder="商品名称" maxLength={100} value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div><Input aria-label="商品编号" placeholder="商品编号" inputMode="numeric" maxLength={18} value={itemIdInput} onChange={event => setItemIdInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} /><Select value={gameplayFilter} onValueChange={setGameplayFilter}><SelectTrigger aria-label="玩法筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部玩法</SelectItem><SelectItem value="娱乐双排">娱乐双排</SelectItem><SelectItem value="技术提升">技术提升</SelectItem><SelectItem value="周末盲盒">周末盲盒</SelectItem></SelectContent></Select><div className="flex items-center gap-2"><Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button><Button variant="outline" className="whitespace-nowrap" aria-expanded={advancedOpen} aria-controls="service-advanced-filters" onClick={() => setAdvancedOpen(value => !value)}><Filter className="size-4" />{advancedOpen ? '收起筛选' : '更多筛选'}{advancedCount > 0 && <Badge className="px-1.5">{advancedCount}</Badge>}</Button></div></div>
        {advancedOpen && <div id="service-advanced-filters" data-admin-filter-advanced className="grid gap-3 border-t pt-3 md:grid-cols-3"><Select value={platformFilter} onValueChange={setPlatformFilter}><SelectTrigger aria-label="平台筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部平台</SelectItem><SelectItem value="MOBILE">手游</SelectItem><SelectItem value="PC">端游</SelectItem></SelectContent></Select><Select value={securityFilter} onValueChange={setSecurityFilter}><SelectTrigger aria-label="难度筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部难度</SelectItem><SelectItem value="CONFIDENTIAL">机密</SelectItem><SelectItem value="TOP_SECRET">绝密</SelectItem><SelectItem value="ANY">不限难度</SelectItem></SelectContent></Select><Select value={categoryFilter} onValueChange={setCategoryFilter}><SelectTrigger aria-label="分类筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部分类</SelectItem>{serviceCategories.map(category => <SelectItem key={category.id} value={String(category.id)}>{category.isGift ? '礼物' : '陪玩'} · {category.name}</SelectItem>)}</SelectContent></Select></div>}
      </div>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1080px]"><TableHeader><TableRow><TableHead>商品</TableHead><TableHead>玩法与规格</TableHead><TableHead>接单要求</TableHead><TableHead className="text-right">价格</TableHead><TableHead>下单方式</TableHead><TableHead>抽成配置</TableHead><TableHead>状态</TableHead>{canEdit && <TableHead className="text-right">操作</TableHead>}</TableRow></TableHeader><TableBody>{rows.map(item => { const gift = isGift(item); const game = gameCategories.find(category => category.id === item.gameCategoryId); const category = categoryFor(item); return <TableRow key={item.id}>
        <TableCell><div className="flex items-start gap-3"><span className={cn('grid size-11 shrink-0 place-items-center rounded-xl', gift ? 'bg-sky-500/10 text-sky-500' : 'bg-primary/10 text-primary')}>{gift ? <Gift className="size-5" /> : <Gamepad2 className="size-5" />}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{item.name}</h3>{item.isAddon && <Badge variant="outline">附加费用</Badge>}</div><p className="mt-1 max-w-52 truncate text-xs text-muted-foreground">{item.description || '—'}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">ID {item.id} · 排序 {item.sortOrder}</p></div></div></TableCell>
        <TableCell><p className="text-xs font-medium">{gift ? category?.name : item.gameplay || '普通服务'}</p><p className="mt-1 text-[11px] text-muted-foreground">{gift ? '礼物独立赠送' : `${game?.name || '未选游戏'} · ${item.platform ? platformLabel[item.platform] : '通用'} / ${item.securityLevel ? securityLabel[item.securityLevel] : '通用'}`}</p></TableCell>
        <TableCell><p className="text-xs font-medium">{gift ? '无需陪玩接单' : item.providerKind ? `${item.providerKind} · ${item.providerGrade}` : item.requiredLevel || '不限档位'}</p><p className="mt-1 text-[11px] text-muted-foreground">{category?.name || '未分类'}</p></TableCell>
        <TableCell className="text-right"><p className="text-sm font-semibold tabular-nums">{formatServiceRaw(item.priceRaw)} 浪花</p><p className="mt-1 text-[11px] text-muted-foreground">/ {item.priceUnit}{item.billingProviderCount === 2 ? ' · 每人' : ''}</p>{item.billingProviderCount === 2 && <p className="mt-1 text-[11px] text-primary">双陪合计 {formatServiceRaw(item.priceRaw * 2)}</p>}</TableCell>
        <TableCell><Badge variant="outline">{item.purchaseMode === 'DIRECT' ? '直接下单' : '客服确认'}</Badge><p className="mt-1 text-[11px] text-muted-foreground">{item.billingProviderCount === 2 ? '双陪' : '单陪'}{item.isAddon ? ' · 不可独立购买' : ''}</p></TableCell>
        <TableCell><p className={cn('text-xs font-medium', item.commissionOverride && !gift && 'text-primary')}>{serviceCommissionLabel(item, gift)}</p><p className="mt-1 text-[11px] text-muted-foreground">{gift ? '不使用订单默认抽成' : item.commissionOverride ? '商品自定义' : item.commissionPolicy}</p></TableCell>
        <TableCell><Badge variant="outline" className={cn(isDisplayActive(item) && 'border-success/30 bg-success/10 text-success')}>{isDisplayActive(item) ? '展示中' : '已下架'}</Badge>{item.active && !isDisplayActive(item) && <p className="mt-1 text-[11px] text-muted-foreground">关联配置停用</p>}</TableCell>
        {canEdit && <TableCell><div className="flex justify-end gap-2"><Button variant="outline" size="sm" disabled={!metadataReady || saveReviewActive} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />编辑</Button>{canDelete && <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setDeleteTarget(item)}><Trash2 className="size-3.5" />删除</Button>}</div></TableCell>}
      </TableRow>})}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><PackagePlus className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的服务商品</h2><p className="mt-2 text-sm text-muted-foreground">清除筛选后查看全部商品，归档玩法商品不会恢复显示。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>商品、类目与保存结果均为本地示例，不连接真实业务。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <AdminServiceFormDialog key={`${editing?.id ?? 'new'}-${formOpen}`} open={formOpen} item={editing} gameCategories={gameCategories} serviceCategories={serviceCategories} initialError={scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对商品后再操作，避免重复提交。' : ''} saveUnknown={saveUnknown} onClose={closeForm} onSave={saveService} />

    <AlertDialog open={!!deleteTarget} onOpenChange={open => { if (!open) setDeleteTarget(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除服务项？</AlertDialogTitle><AlertDialogDescription>确定删除「{deleteTarget?.name}」吗？此操作只移除本地原型记录；真实项目仍需按接口约束处理历史关联。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={() => { if (!deleteTarget) return; setServices(current => current.filter(item => item.id !== deleteTarget.id)); setNotice(`商品「${deleteTarget.name}」已从本地示例删除。`); setDeleteTarget(null) }}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>

    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除商品保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对商品及请求处理情况。同名商品或归档玩法商品需另行核实，原请求可能仍在处理。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>

    <Dialog open={!!dictionary} onOpenChange={open => { if (!open) setDictionary(null) }}><DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{dictionary === 'game' ? '游戏类目设置' : '服务分类设置'}</DialogTitle><DialogDescription>{dictionary === 'game' ? '停用后老板端目录和陪玩技能选项隐藏，已有订单与档案保留。' : '陪玩与礼物分类分别维护；礼物属性影响下单方式和抽成规则。'}</DialogDescription></DialogHeader>
      <div className="space-y-3">{(dictionary === 'game' ? gameCategories : serviceCategories).map(category => { const isGame = dictionary === 'game'; const item = category as AdminGameCategory & AdminServiceCategory; const linked = isGame ? services.filter(service => service.gameCategoryId === category.id).length : services.filter(service => service.serviceCategoryId === category.id).length; return <div key={category.id} className="flex min-h-16 items-center gap-3 rounded-xl border p-3"><span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">{isGame ? <Gamepad2 className="size-4" /> : item.isGift ? <Gift className="size-4" /> : <Layers3 className="size-4" />}</span><div className="min-w-0 flex-1"><p className="text-sm font-medium">{category.name}</p><p className="mt-1 text-[11px] text-muted-foreground">{isGame ? `${linked} 项服务 · ${item.escortCount} 位陪玩` : `${item.isGift ? '礼物' : '陪玩'} · ${linked} 项商品`} · 排序 {category.sortOrder}</p></div><Badge variant="outline">{category.active ? '启用' : '停用'}</Badge><Switch aria-label={`启用${category.name}`} checked={category.active} onCheckedChange={value => { if (isGame) setGameCategories(current => current.map(row => row.id === category.id ? { ...row, active: value } : row)); else setServiceCategories(current => current.map(row => row.id === category.id ? { ...row, active: value } : row)) }} /></div>})}</div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-[minmax(0,1fr)_160px_auto]"><div className="space-y-2"><Label htmlFor="dictionary-name">新增{dictionary === 'game' ? '游戏类目' : '服务分类'}</Label><Input id="dictionary-name" value={dictionaryName} onChange={event => { setDictionaryName(event.target.value); setDictionaryError('') }} placeholder="输入名称" /></div>{dictionary === 'service' ? <div className="space-y-2"><Label>归属</Label><Select value={dictionaryGift} onValueChange={setDictionaryGift}><SelectTrigger aria-label="新增分类归属"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="PLAY">陪玩</SelectItem><SelectItem value="GIFT">礼物</SelectItem></SelectContent></Select></div> : <div />}<Button className="self-end" onClick={addDictionaryItem}><Plus className="size-4" />新增</Button></div>{dictionaryError && <p role="alert" className="text-xs text-destructive">{dictionaryError}</p>}<p className="text-[11px] leading-5 text-muted-foreground">本原型仅演示新增与启停；真实删除须先确认没有关联商品、订单或陪玩。</p>
    </DialogContent></Dialog>
  </div>
}
