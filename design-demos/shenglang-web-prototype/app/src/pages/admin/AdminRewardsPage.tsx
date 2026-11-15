import { useMemo, useState } from 'react'
import { CircleAlert, Coins, Edit3, Eye, EyeOff, Gift, Info, ListFilter, Plus, RotateCcw, Search, ShieldAlert, Trash2 } from 'lucide-react'
import type { AdminRewardsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminRewardFormDialog } from '@/pages/admin/AdminRewardFormDialog'
import { adminRewardAudienceLabels, adminRewardKindLabels, adminRewardServiceOptions, getAdminRewardShopBlockReason, initialAdminRewards, type AdminRewardItem } from '@/pages/admin/admin-reward-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminRewardsPageProps {
  scenario: AdminRewardsScenario
  onScenarioChange: (scenario: AdminRewardsScenario) => void
}

const formScenarios: AdminRewardsScenario[] = ['save-error', 'save-review', 'stock-conflict', 'lottery-conflict']

export function AdminRewardsPage({ scenario, onScenarioChange }: AdminRewardsPageProps) {
  const [rewards, setRewards] = useState<AdminRewardItem[]>(initialAdminRewards)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [kindFilter, setKindFilter] = useState('ALL')
  const [audienceFilter, setAudienceFilter] = useState('ALL_AUDIENCES')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = formScenarios.includes(scenario)
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminRewardItem | null>(() => opensForm ? initialAdminRewards.find(item => scenario === 'lottery-conflict' ? item.id === 104 : item.id === 101) || initialAdminRewards[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [deleting, setDeleting] = useState<AdminRewardItem | null>(null)
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const canEdit = scenario !== 'readonly'
  const canDelete = canEdit && scenario !== 'edit-only'
  const operationsLocked = saveReviewActive || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const shopVisibleCount = rewards.filter(item => !getAdminRewardShopBlockReason(item)).length
  const soldOutCount = rewards.filter(item => item.stock === 0).length
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return rewards.filter(item => {
      if (keyword && !`${item.id} ${item.code} ${item.name} ${item.description}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (kindFilter !== 'ALL' && item.kind !== kindFilter) return false
      if (audienceFilter !== 'ALL_AUDIENCES' && item.audience !== audienceFilter) return false
      if (statusFilter === 'ACTIVE' && !item.isActive) return false
      if (statusFilter === 'INACTIVE' && item.isActive) return false
      if (statusFilter === 'SHOP' && !item.inShop) return false
      if (statusFilter === 'HIDDEN' && item.inShop) return false
      if (statusFilter === 'SOLD_OUT' && item.stock !== 0) return false
      return true
    }).sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
  }, [rewards, scenario, keyword, kindFilter, audienceFilter, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setKindFilter('ALL'); setAudienceFilter('ALL_AUDIENCES'); setStatusFilter('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openEdit(item: AdminRewardItem | null) {
    if (!canEdit || operationsLocked) return
    setEditing(item); setFormOpen(true); setNotice(''); setActionError('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveReward(item: AdminRewardItem) {
    const saved = item.id ? item : { ...item, id: Math.max(...rewards.map(reward => reward.id)) + 1 }
    setRewards(current => item.id ? current.map(reward => reward.id === item.id ? saved : reward) : [...current, saved])
    setEditing(saved)
    setFormOpen(false)
    setNotice(`好礼「${saved.name}」已在本地示例中保存；历史兑换记录与交付快照未改变。`)
    setActionError('')
  }
  function deleteReward() {
    if (!deleting || !canDelete || operationsLocked) return
    if (scenario === 'action-error' || deleting.activeLottery) {
      setActionError(deleting.activeLottery ? `好礼「${deleting.name}」仍关联抽奖活动，无法删除；请先核对活动奖品。` : `好礼「${deleting.name}」删除失败，当前记录保持不变，请重试。`)
      setDeleting(null)
      return
    }
    setRewards(current => current.filter(item => item.id !== deleting.id))
    setNotice(`好礼「${deleting.name}」已从本地目录删除；既有兑换记录仍保留商品快照。`)
    setActionError('')
    setDeleting(null)
  }
  function locateReview() {
    const value = editing?.code || ''
    setKeywordInput(value); setKeyword(value); setKindFilter('ALL'); setAudienceFilter('ALL_AUDIENCES'); setStatusFilter('ALL'); setReviewSearched(true)
  }
  const initialFormError = scenario === 'save-error' ? '保存未完成：请求被拒绝，请核对输入和 engagement:edit 权限。' : scenario === 'save-review' ? '保存结果未确认。请关闭并核对好礼后再操作，避免重复提交。' : scenario === 'stock-conflict' ? '商品库存或兑换记录已变化，请刷新后再保存。' : scenario === 'lottery-conflict' ? '请先暂停使用此奖品的抽奖活动，再修改奖品。' : ''

  if (scenario === 'loading') return <div className="space-y-4" aria-label="积分好礼加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-[560px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">积分好礼加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠商品快照，不展示推测价格、库存、权益或商城状态。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">积分好礼</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">商城商品与交付权益</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一维护积分价格、适用身份、库存和交付内容，让后台配置与老板端商城保持一致。</p></div>
        <dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{rewards.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部好礼</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{shopVisibleCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">老板端可见</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{soldOutCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">已兑完</dt></div></dl>
      </div>
    </section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并核对好礼、价格与库存；新增、编辑和删除需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'edit-only' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前可编辑但不可删除</AlertTitle><AlertDescription>可新增和调整好礼配置；删除入口已按权限隐藏。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>好礼刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">新增、编辑与删除已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {actionError && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>好礼操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>好礼保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.name || '当前好礼'}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询好礼</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}
    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>启用与商城展示共同决定兑换入口</AlertTitle><AlertDescription>老板端只展示已启用、已上架且身份匹配的好礼；有限库存为 0 时保留卡片并显示“已兑完”。余额兑换自动到账，其他类型可在兑换后补充交付资料。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Gift className="size-4 text-primary" />好礼目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 按展示排序与商品编号排列</CardDescription></div>{canEdit && <Button disabled={operationsLocked} onClick={() => openEdit(null)}><Plus className="size-4" />新增好礼</Button>}</CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(220px,1fr)_180px_160px_180px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="好礼名称、标识或编号" placeholder="搜索名称、标识或编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div>
        <Select value={kindFilter} onValueChange={setKindFilter}><SelectTrigger aria-label="好礼类型筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部交付类型</SelectItem>{Object.entries(adminRewardKindLabels).map(([kind, label]) => <SelectItem key={kind} value={kind}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={audienceFilter} onValueChange={setAudienceFilter}><SelectTrigger aria-label="好礼身份筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL_AUDIENCES">全部身份</SelectItem>{Object.entries(adminRewardAudienceLabels).map(([audience, label]) => <SelectItem key={audience} value={audience}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="好礼状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem><SelectItem value="ACTIVE">已启用</SelectItem><SelectItem value="INACTIVE">已停用</SelectItem><SelectItem value="SHOP">商城已展示</SelectItem><SelectItem value="HIDDEN">商城未展示</SelectItem><SelectItem value="SOLD_OUT">库存已兑完</SelectItem></SelectContent></Select>
        <Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button>
      </div>
      <div className="overflow-x-auto border-t">
        <Table className="min-w-[1040px]">
          <TableHeader><TableRow><TableHead>好礼</TableHead><TableHead>交付与身份</TableHead><TableHead>兑换 / 权益</TableHead><TableHead>库存 / 已兑换</TableHead><TableHead>商城状态</TableHead><TableHead>排序</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(item => {
            const blockReason = getAdminRewardShopBlockReason(item)
            const service = adminRewardServiceOptions.find(option => option.id === item.serviceId)
            return <TableRow key={item.id}>
              <TableCell><div className="flex max-w-[280px] items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Gift className="size-4" /></span><div className="min-w-0"><p className="truncate text-xs font-semibold">{item.name}</p><p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">#{item.id} · {item.code}</p>{item.activeLottery && <p className="mt-1 text-[10px] text-amber-700 dark:text-amber-400">活动占用 · {item.activeLottery}</p>}</div></div></TableCell>
              <TableCell><div className="min-w-28"><Badge variant="secondary">{adminRewardKindLabels[item.kind]}</Badge><p className="mt-1.5 text-[11px] text-muted-foreground">{adminRewardAudienceLabels[item.audience]}</p>{service && <p className="mt-1 max-w-36 truncate text-[10px] text-muted-foreground" title={service.name}>{service.name}</p>}</div></TableCell>
              <TableCell><div className="min-w-32"><p className="text-sm font-semibold tabular-nums">{item.pointCost.toLocaleString('zh-CN')} <span className="text-[11px] font-normal text-muted-foreground">积分</span></p><p className="mt-1.5 text-[11px] text-muted-foreground">{item.kind === 'BALANCE' ? `到账 ${formatWaveAmount(item.balanceCoins)} 浪花` : item.kind === 'POINTS' ? `奖励 ${item.pointValue} 积分` : ['TAG', 'NAMING'].includes(item.kind) ? item.durationMonths ? `${item.durationMonths} 个自然月` : item.durationDays ? `${item.durationDays} 天` : '未设到期日' : item.kind === 'SERVICE' ? '服务权益' : '兑换后补充交付资料'}</p></div></TableCell>
              <TableCell><div className="min-w-24"><p className={cn('text-xs font-medium tabular-nums', item.stock === 0 && 'text-destructive')}>{item.stock === null ? '不限库存' : item.stock === 0 ? '已兑完' : `剩余 ${item.stock} 份`}</p><p className="mt-1.5 text-[11px] text-muted-foreground tabular-nums">已兑换 {item.redeemedCount}</p></div></TableCell>
              <TableCell><div className="flex min-w-32 flex-wrap gap-1.5"><Badge variant="outline" className={item.isActive ? 'border-success/25 bg-success/10 text-success' : ''}>{item.isActive ? '已启用' : '已停用'}</Badge><Badge variant="outline">{item.inShop ? <Eye className="size-3" /> : <EyeOff className="size-3" />}{item.inShop ? '商城展示' : '商城隐藏'}</Badge></div>{blockReason && <p className="mt-1.5 max-w-32 text-[10px] leading-4 text-muted-foreground">老板端不可见：{blockReason}</p>}</TableCell>
              <TableCell><span className="text-xs tabular-nums">{item.sortOrder}</span></TableCell>
              <TableCell><div className="flex justify-end gap-1.5">{canEdit ? <><Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(item)}><Edit3 className="size-3.5" />配置</Button>{canDelete && <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive" disabled={operationsLocked} onClick={() => { setDeleting(item); setActionError('') }} aria-label={`删除好礼 ${item.name}`}><Trash2 className="size-3.5" /></Button>}</> : <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
        {rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ListFilter className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的积分好礼</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词、交付类型、身份和状态筛选后查看完整目录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span className="flex items-center gap-1.5"><Coins className="size-3.5" />商品、预览和保存结果均为本地示例，不产生真实兑换或交付。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    {formOpen && <AdminRewardFormDialog key={`${editing?.id || 'new'}-${formOpen}`} open={formOpen} item={editing} existingCodes={rewards.map(item => item.code)} initialError={initialFormError} saveFailed={scenario === 'save-error'} saveUnknown={saveUnknown} stockConflict={scenario === 'stock-conflict'} lotteryConflict={scenario === 'lottery-conflict'} onClose={closeForm} onSave={saveReward} />}
    <AlertDialog open={!!deleting} onOpenChange={value => { if (!value) setDeleting(null) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除好礼「{deleting?.name}」？</AlertDialogTitle><AlertDialogDescription>仅从当前本地目录删除商品；已产生的 {deleting?.redeemedCount || 0} 条兑换记录及其名称、价格、权益快照仍保留。关联活动或受保护记录可能阻止删除。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction className="bg-destructive text-white hover:bg-destructive/90" onClick={deleteReward}>确认删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除好礼保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已核对当前好礼、库存和原请求处理情况，避免重复保存。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
