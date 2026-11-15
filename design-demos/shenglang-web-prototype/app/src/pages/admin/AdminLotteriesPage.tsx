import { useMemo, useState } from 'react'
import { CalendarDays, CircleAlert, Coins, Edit3, Gift, Info, ListFilter, Plus, RotateCcw, Search, ShieldAlert, ShieldCheck, Sparkles, Trophy } from 'lucide-react'
import type { AdminLotteriesScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminLotteryFormDialog } from '@/pages/admin/AdminLotteryFormDialog'
import { formatAdminLotteryTime, getAdminLotteryPublicState, getAdminLotteryReward, getAdminLotteryTenUnavailableReason, initialAdminLotteryCampaigns, LOTTERY_PITY_LIMIT, LOTTERY_PITY_REWARD_CODE, type AdminLotteryCampaign } from '@/pages/admin/admin-lottery-data'
import { cn } from '@/lib/utils'

interface AdminLotteriesPageProps {
  scenario: AdminLotteriesScenario
  onScenarioChange: (scenario: AdminLotteriesScenario) => void
}

const formScenarios: AdminLotteriesScenario[] = ['save-error', 'save-review', 'version-conflict']
const stateLabels = { OPEN: '开放中', SCHEDULED: '待开始', ENDED: '已结束', DISABLED: '已停用' }

export function AdminLotteriesPage({ scenario, onScenarioChange }: AdminLotteriesPageProps) {
  const [campaigns, setCampaigns] = useState<AdminLotteryCampaign[]>(initialAdminLotteryCampaigns)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const opensForm = formScenarios.includes(scenario)
  const [formOpen, setFormOpen] = useState(opensForm)
  const [editing, setEditing] = useState<AdminLotteryCampaign | null>(() => opensForm ? initialAdminLotteryCampaigns[0] : null)
  const [saveReviewActive, setSaveReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const canEdit = scenario !== 'readonly'
  const metadataUnavailable = scenario === 'metadata-loading' || scenario === 'metadata-error'
  const operationsLocked = saveReviewActive || metadataUnavailable || scenario === 'refresh-error'
  const saveUnknown = scenario === 'save-review' && formOpen
  const openCount = campaigns.filter(campaign => getAdminLotteryPublicState(campaign) === 'OPEN').length
  const prizeCount = campaigns.reduce((sum, campaign) => sum + campaign.prizes.length, 0)
  const activeCampaign = campaigns.find(campaign => getAdminLotteryPublicState(campaign) === 'OPEN') || campaigns[0]
  const activePity = activeCampaign?.prizes.find(prize => getAdminLotteryReward(prize)?.code === LOTTERY_PITY_REWARD_CODE)
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return campaigns.filter(campaign => {
      if (keyword && !`${campaign.id} ${campaign.name} ${campaign.rules}`.toLocaleLowerCase().includes(keyword.toLocaleLowerCase())) return false
      if (statusFilter !== 'ALL' && getAdminLotteryPublicState(campaign) !== statusFilter) return false
      return true
    }).sort((a, b) => b.id - a.id)
  }, [campaigns, scenario, keyword, statusFilter])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setStatusFilter('ALL')
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openEdit(campaign: AdminLotteryCampaign | null) {
    if (!canEdit || operationsLocked) return
    setEditing(campaign); setFormOpen(true); setNotice('')
  }
  function closeForm() {
    setFormOpen(false)
    if (saveUnknown) setSaveReviewActive(true)
  }
  function saveCampaign(campaign: AdminLotteryCampaign) {
    const saved = campaign.id
      ? { ...campaign, version: campaign.version + 1 }
      : { ...campaign, id: Math.max(...campaigns.map(item => item.id)) + 1, version: 1 }
    setCampaigns(current => campaign.id ? current.map(item => item.id === campaign.id ? saved : item) : [saved, ...current])
    setEditing(saved); setFormOpen(false)
    setNotice(`活动「${saved.name}」已保存为版本 v${saved.version}；仅更新本地示例，不产生真实抽奖或积分变动。`)
  }
  function locateReview() {
    const value = editing?.name || ''
    setKeywordInput(value); setKeyword(value); setStatusFilter('ALL'); setReviewSearched(true)
  }
  const initialFormError = scenario === 'save-error'
    ? '保存未完成：请求被拒绝，请核对输入和 engagement:edit 权限。当前活动与奖池输入已保留。'
    : scenario === 'save-review'
      ? '保存结果未确认。请关闭并查询活动后再操作，避免重复提交。'
      : scenario === 'version-conflict'
        ? '配置已被其他人员修改，请刷新后重试。当前输入已保留。'
        : ''

  if (scenario === 'loading') return <div className="space-y-4" aria-label="幸运转盘加载中"><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-[500px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">抽奖活动加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠活动快照，不展示推测概率、保底或开放状态。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">幸运转盘</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">抽奖活动与奖池配置</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">统一管理抽奖消耗、基础概率、十连保障与体验单保底，让每一次开放都有完整、可核对的规则。</p></div>
        <dl className="grid grid-cols-3 gap-4"><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{campaigns.length}</dd><dt className="mt-1 text-[11px] text-blue-100/65">全部活动</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{openCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">正在开放</dt></div><div className="border-l border-white/15 pl-4"><dd className="text-2xl font-semibold tabular-nums">{prizeCount}</dd><dt className="mt-1 text-[11px] text-blue-100/65">奖项配置</dt></div></dl>
      </div>
    </section>

    {scenario === 'readonly' && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并核对活动、概率和保底；新增与编辑需要 engagement:edit 权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>活动刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">新增与编辑已暂时锁定。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {scenario === 'metadata-loading' && <Alert><RotateCcw className="size-4 animate-spin" /><AlertTitle>正在读取奖品字典</AlertTitle><AlertDescription>活动快照保持可核对；读取完成前锁定新增与编辑，避免使用不完整奖品配置。</AlertDescription></Alert>}
    {scenario === 'metadata-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>奖品字典读取失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">活动快照仍保留，但不能打开编辑器。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重试奖品字典</Button></AlertDescription></Alert>}
    {saveReviewActive && !formOpen && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>活动保存结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">「{editing?.name || '当前活动'}」的保存结果未确认，请查询并核实，避免重复提交。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询活动</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && !formOpen && <Alert role="status" className="border-success/30 bg-success/10"><Info className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}

    {activeCampaign && <section className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(340px,1fr)]">
      <Card className="overflow-hidden border-primary/20">
        <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 pb-4"><div><div className="flex flex-wrap items-center gap-2"><CardTitle className="text-base">当前活动 · {activeCampaign.name}</CardTitle><Badge variant="outline" className="border-success/25 bg-success/10 text-success">开放中</Badge></div><CardDescription className="mt-1.5">活动 #{activeCampaign.id} · 配置版本 v{activeCampaign.version} · {formatAdminLotteryTime(activeCampaign.startsAt)} 至 {formatAdminLotteryTime(activeCampaign.endsAt)}</CardDescription></div><Sparkles className="size-5 text-primary" /></CardHeader>
        <CardContent className="grid gap-4 border-t pt-4 sm:grid-cols-[minmax(0,1fr)_220px]">
          <div><div className="flex items-end justify-between gap-3"><div><p className="text-[11px] text-muted-foreground">基础概率合计</p><p className="mt-1 text-2xl font-semibold tabular-nums">{(activeCampaign.prizes.reduce((sum, prize) => sum + prize.probability, 0) / 100).toFixed(2)}%</p></div><p className="text-xs text-muted-foreground">单抽 <strong className="text-foreground">{activeCampaign.pointCost}</strong> 积分 · 十连 <strong className="text-foreground">{activeCampaign.pointCost * 10}</strong> 积分</p></div><div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-muted">{activeCampaign.prizes.map((prize, index) => <span key={prize.id} style={{ width: `${prize.probability / 100}%` }} className={cn('h-full border-r border-background/60 last:border-0', ['bg-[#1e3a8a]', 'bg-[#2563eb]', 'bg-[#38bdf8]', 'bg-[#7dd3fc]', 'bg-[#f59e0b]', 'bg-[#64748b]'][index % 6])} />)}</div><div className="mt-3 grid gap-2 sm:grid-cols-2">{activeCampaign.prizes.map(prize => <div key={prize.id} className="flex items-center justify-between gap-2 text-[11px]"><span className="truncate text-muted-foreground">{getAdminLotteryReward(prize)?.name || `奖品 #${prize.rewardId}`}</span><span className="shrink-0 font-medium tabular-nums">{(prize.probability / 100).toFixed(2)}%</span></div>)}</div></div>
          <div className="rounded-xl border bg-muted/20 p-4"><div className="flex items-center gap-2"><Trophy className="size-4 text-warning" /><p className="text-xs font-semibold">68 体验单保底</p></div>{activePity ? <><div className="mt-3 flex items-end justify-between"><p className="text-2xl font-semibold tabular-nums">240<span className="ml-1 text-xs font-normal text-muted-foreground">抽</span></p><Badge variant="outline" className="border-warning/25 text-warning">共享进度</Badge></div><Progress value={70} className="mt-3 h-2" aria-label="当前活动保底规则预览" /><p className="mt-3 text-[11px] leading-5 text-muted-foreground">基础概率 {(activePity.probability / 100).toFixed(2)}% · 提前获得立即重置；单抽与十连共享进度。</p></> : <p className="mt-3 text-xs text-warning">未配置保底目标</p>}</div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-4"><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="size-4 text-primary" />开放前规则核对</CardTitle><CardDescription>当前活动的普通抽奖、十连与保底约束</CardDescription></CardHeader>
        <CardContent className="space-y-3 border-t pt-4">
          {[{ label: '奖池概率', value: '合计 100.00%', ok: true }, { label: '十连保障', value: getAdminLotteryTenUnavailableReason(activeCampaign) || '至少 1 份非积分奖', ok: !getAdminLotteryTenUnavailableReason(activeCampaign) }, { label: '参与频次', value: activeCampaign.dailyLimit ? `每人每天 ${activeCampaign.dailyLimit} 次` : '不限每日次数', ok: true }, { label: '保底权益', value: activePity ? '专属服务 · 商城隐藏' : '目标待核对', ok: !!activePity }].map(item => <div key={item.label} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"><span className="flex items-center gap-2 text-xs"><span className={cn('size-1.5 rounded-full', item.ok ? 'bg-success' : 'bg-warning')} />{item.label}</span><span className={cn('text-right text-[11px]', item.ok ? 'text-muted-foreground' : 'text-warning')}>{item.value}</span></div>)}
          <p className="text-[10px] leading-4 text-muted-foreground">十连第 10 抽仅从正基础概率的可领取非积分奖中按原权重抽取；240 抽保底优先。</p>
        </CardContent>
      </Card>
    </section>}

    <Alert className="border-primary/25 bg-primary/5"><Info className="size-4 text-primary" /><AlertTitle>完整奖池随活动版本一起保存</AlertTitle><AlertDescription>启用时概率合计必须为 100%；已有中奖记录的奖项不能删除。版本冲突、保存失败或结果未知时，不覆盖他人配置，也不重复提交。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><Sparkles className="size-4 text-primary" />活动目录</CardTitle><CardDescription className="mt-1">本页 {rows.length} 项 · 当前与历史活动均可核对完整配置</CardDescription></div>{canEdit && <Button disabled={operationsLocked} onClick={() => openEdit(null)}><Plus className="size-4" />新建活动</Button>}</CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 md:grid-cols-[minmax(240px,1fr)_200px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="活动名称或编号" placeholder="搜索活动名称或编号" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div>
        <Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger aria-label="活动状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部公开状态</SelectItem><SelectItem value="OPEN">开放中</SelectItem><SelectItem value="SCHEDULED">待开始</SelectItem><SelectItem value="ENDED">已结束</SelectItem><SelectItem value="DISABLED">已停用</SelectItem></SelectContent></Select>
        <Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button>
      </div>
      <div className="overflow-x-auto border-t">
        <Table className="min-w-[1040px]">
          <TableHeader><TableRow><TableHead>活动</TableHead><TableHead>开放状态 / 时间</TableHead><TableHead>抽奖消耗</TableHead><TableHead>奖项 / 概率</TableHead><TableHead>十连与保底</TableHead><TableHead>版本</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(campaign => {
            const state = getAdminLotteryPublicState(campaign)
            const total = campaign.prizes.reduce((sum, prize) => sum + prize.probability, 0)
            const tenReason = getAdminLotteryTenUnavailableReason(campaign)
            const pity = campaign.prizes.find(prize => getAdminLotteryReward(prize)?.code === LOTTERY_PITY_REWARD_CODE)
            return <TableRow key={campaign.id}>
              <TableCell><div className="flex max-w-[250px] items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Gift className="size-4" /></span><div className="min-w-0"><p className="truncate text-xs font-semibold">{campaign.name}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">活动 #{campaign.id}</p><p className="mt-1 truncate text-[10px] text-muted-foreground" title={campaign.rules}>{campaign.rules || '未填写参与说明'}</p></div></div></TableCell>
              <TableCell><div className="min-w-40"><Badge variant="outline" className={cn(state === 'OPEN' && 'border-success/25 bg-success/10 text-success')}>{stateLabels[state]}</Badge><p className="mt-2 flex items-center gap-1.5 text-[10px] text-muted-foreground"><CalendarDays className="size-3" />{formatAdminLotteryTime(campaign.startsAt)}</p><p className="mt-1 pl-[18px] text-[10px] text-muted-foreground">至 {formatAdminLotteryTime(campaign.endsAt)}</p></div></TableCell>
              <TableCell><div className="min-w-28"><p className="text-sm font-semibold tabular-nums">{campaign.pointCost} <span className="text-[11px] font-normal text-muted-foreground">积分 / 抽</span></p><p className="mt-1.5 text-[11px] text-muted-foreground">十连 {campaign.pointCost * 10} 积分</p><p className="mt-1 text-[10px] text-muted-foreground">{campaign.dailyLimit ? `每日限 ${campaign.dailyLimit} 次` : '每日不限次数'}</p></div></TableCell>
              <TableCell><div className="min-w-28"><p className="text-xs font-medium">{campaign.prizes.length} 个奖项</p><p className={cn('mt-1.5 text-xs tabular-nums', total === 10000 ? 'text-success' : 'text-warning')}>合计 {(total / 100).toFixed(2)}%</p></div></TableCell>
              <TableCell><div className="flex min-w-36 flex-col items-start gap-1.5"><Badge variant="outline" className={tenReason ? 'border-warning/25 text-warning' : ''}>{tenReason ? '十连不可用' : '十连保障已配置'}</Badge><span className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><Trophy className="size-3 text-warning" />{pity ? `${LOTTERY_PITY_LIMIT} 抽体验单保底` : '未配置体验单保底'}</span></div></TableCell>
              <TableCell><Badge variant="secondary" className="font-mono text-[10px]">v{campaign.version}</Badge></TableCell>
              <TableCell><div className="flex justify-end">{canEdit ? <Button variant="outline" size="sm" disabled={operationsLocked} onClick={() => openEdit(campaign)}><Edit3 className="size-3.5" />配置</Button> : <span className="text-xs text-muted-foreground">仅查看</span>}</div></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
        {rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ListFilter className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的抽奖活动</h2><p className="mt-2 text-sm text-muted-foreground">清除关键词与公开状态筛选后查看完整活动目录。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span className="flex items-center gap-1.5"><Coins className="size-3.5" />活动、预览和保存均为本地示例，不执行真实抽奖、扣积分或交付。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    {formOpen && <AdminLotteryFormDialog key={`${editing?.id || 'new'}-${formOpen}`} open={formOpen} item={editing} initialError={initialFormError} saveFailed={scenario === 'save-error'} saveUnknown={saveUnknown} versionConflict={scenario === 'version-conflict'} onClose={closeForm} onSave={saveCampaign} />}
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除活动保存限制？</AlertDialogTitle><AlertDialogDescription>请确认已查询当前活动版本并核对原请求处理情况，避免重复保存或覆盖他人配置。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setSaveReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
