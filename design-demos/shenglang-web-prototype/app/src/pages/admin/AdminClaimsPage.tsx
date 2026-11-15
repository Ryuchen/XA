import { useMemo, useState } from 'react'
import { BadgeCheck, CheckCircle2, CircleAlert, Clock3, Coins, Eye, Gift, Info, ListFilter, LockKeyhole, PackageCheck, RotateCcw, Search, ShieldAlert, Tag, Truck } from 'lucide-react'
import type { AdminClaimsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AdminClaimProcessDialog } from '@/pages/admin/AdminClaimProcessDialog'
import { adminClaimDetailLabels, adminClaimKindLabels, adminClaimSourceLabels, adminClaimStatusLabels, formatAdminClaimTime, initialAdminClaims, isAdminClaimTagReady, isAdminClaimTagReviewNeeded, maskAdminClaimDetail, type AdminClaim, type AdminClaimAccountType, type AdminClaimKind, type AdminClaimSource, type AdminClaimStatus } from '@/pages/admin/admin-claim-data'
import { cn } from '@/lib/utils'

interface AdminClaimsPageProps {
  scenario: AdminClaimsScenario
  onScenarioChange: (scenario: AdminClaimsScenario) => void
}

const dialogScenarios: AdminClaimsScenario[] = ['save-error', 'save-review', 'details-conflict', 'status-conflict', 'terms-conflict', 'tag-review', 'tag-suspend']

function claimById(id: number) {
  return initialAdminClaims.find(claim => claim.id === id) || initialAdminClaims[0]
}

export function AdminClaimsPage({ scenario, onScenarioChange }: AdminClaimsPageProps) {
  const opensDialog = dialogScenarios.includes(scenario)
  const initialItem = claimById(scenario === 'tag-review' ? 8104 : scenario === 'tag-suspend' ? 8103 : 8108)
  const [claims, setClaims] = useState<AdminClaim[]>(initialAdminClaims)
  const [selected, setSelected] = useState<AdminClaim>(initialItem)
  const [dialogOpen, setDialogOpen] = useState(opensDialog)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState<'ALL' | AdminClaimStatus>('ALL')
  const [source, setSource] = useState<'ALL' | AdminClaimSource>('ALL')
  const [kind, setKind] = useState<'ALL' | AdminClaimKind>('ALL')
  const [accountType, setAccountType] = useState<'ALL' | AdminClaimAccountType>('ALL')
  const [tagReady, setTagReady] = useState(false)
  const [tagReview, setTagReview] = useState(false)
  const [notice, setNotice] = useState('')
  const [reviewActive, setReviewActive] = useState(false)
  const [reviewSearched, setReviewSearched] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const readonly = scenario === 'readonly'
  const operationsLocked = readonly || reviewActive || scenario === 'refresh-error'
  const pendingCount = claims.filter(claim => claim.status === 'PENDING').length
  const fulfillingCount = claims.filter(claim => claim.status === 'FULFILLING').length
  const tagReadyCount = claims.filter(isAdminClaimTagReady).length
  const tagReviewCount = claims.filter(isAdminClaimTagReviewNeeded).length

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = keyword.toLocaleLowerCase()
    return claims.filter(claim => {
      if (status !== 'ALL' && claim.status !== status) return false
      if (source !== 'ALL' && claim.source !== source) return false
      if (kind !== 'ALL' && claim.kindSnapshot !== kind) return false
      if (accountType !== 'ALL' && claim.accountType !== accountType) return false
      if (tagReady && !isAdminClaimTagReady(claim)) return false
      if (tagReview && !isAdminClaimTagReviewNeeded(claim)) return false
      if (query) {
        const text = `${claim.id} ${claim.account} ${claim.sourceOrder || ''} ${claim.nameSnapshot} ${claim.accountName} ${claim.username} ${claim.details.tag || ''}`.toLocaleLowerCase()
        if (!text.includes(query)) return false
      }
      return true
    }).sort((a, b) => b.id - a.id)
  }, [claims, scenario, keyword, status, source, kind, accountType, tagReady, tagReview])

  function applySearch() { setKeyword(keywordInput.trim()) }
  function clearFilters() {
    setKeywordInput(''); setKeyword(''); setStatus('ALL'); setSource('ALL'); setKind('ALL'); setAccountType('ALL'); setTagReady(false); setTagReview(false)
    if (scenario === 'empty') onScenarioChange('default')
  }
  function openClaim(claim: AdminClaim) {
    setSelected(claim); setDialogOpen(true); setNotice('')
  }
  function closeDialog(resultUnknown = false) {
    setDialogOpen(false)
    if (resultUnknown) { setReviewActive(true); setReviewSearched(false) }
  }
  function locateReview() {
    setKeywordInput(String(selected.id)); setKeyword(String(selected.id)); setStatus('ALL'); setSource('ALL'); setKind('ALL'); setAccountType('ALL'); setTagReady(false); setTagReview(false); setReviewSearched(true)
  }
  function processClaim(item: AdminClaim, nextStatus: AdminClaimStatus, note: string) {
    const at = '2026-11-04T16:30:00'
    const completedAt = nextStatus === 'COMPLETED' ? at : ''
    const expiresAt = nextStatus === 'COMPLETED' && item.durationDays ? '2026-12-04T16:30:00' : item.expiresAt
    const updated: AdminClaim = {
      ...item, status: nextStatus, deliveryNote: note, completedAt, expiresAt,
      pointsReturned: nextStatus === 'CANCELLED' && item.source === 'REDEMPTION' ? true : item.pointsReturned,
      tagReviewed: item.kindSnapshot === 'TAG' && nextStatus === 'COMPLETED' ? true : item.tagReviewed,
      tagRevisionRequired: item.kindSnapshot === 'TAG' && nextStatus === 'PENDING' ? true : false,
      timeline: [{ id: Math.max(...item.timeline.map(event => event.id)) + 1, status: nextStatus, note, operator: '客服 · 林岚', at }, ...item.timeline],
    }
    setClaims(current => current.map(claim => claim.id === item.id ? updated : claim))
    setSelected(updated); setDialogOpen(false)
    setNotice(nextStatus === 'CANCELLED' ? `记录 #${item.id} 已取消，示例退回 ${item.pointsSpent} 积分并恢复商品库存。` : `记录 #${item.id} 已更新为「${adminClaimStatusLabels[nextStatus]}」，处理说明已保存到本地示例。`)
  }
  function reviewTag(item: AdminClaim) {
    const updated: AdminClaim = { ...item, tagReviewed: true, tagSuspensionReason: '', tagEditable: false, timeline: [{ id: Math.max(...item.timeline.map(event => event.id)) + 1, status: 'COMPLETED', note: 'TAG 内容复核通过，恢复或继续展示。', operator: '客服 · 林岚', at: '2026-11-04T16:32:00' }, ...item.timeline] }
    setClaims(current => current.map(claim => claim.id === item.id ? updated : claim)); setSelected(updated); setDialogOpen(false); setNotice(`记录 #${item.id} 的 TAG 已复核通过。`)
  }
  function suspendTag(item: AdminClaim, reason: string) {
    const updated: AdminClaim = { ...item, tagReviewed: false, tagSuspensionReason: reason, tagEditable: true, timeline: [{ id: Math.max(...item.timeline.map(event => event.id)) + 1, status: 'COMPLETED', note: `TAG 暂停展示：${reason}`, operator: '客服 · 林岚', at: '2026-11-04T16:34:00' }, ...item.timeline] }
    setClaims(current => current.map(claim => claim.id === item.id ? updated : claim)); setSelected(updated); setDialogOpen(false); setNotice(`记录 #${item.id} 的 TAG 已暂停展示，并进入待复核。`)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="权益交付记录加载中"><Skeleton className="h-32 rounded-xl" /><div className="grid grid-cols-4 gap-4">{[1, 2, 3, 4].map(item => <Skeleton key={item} className="h-24 rounded-xl" />)}</div><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">权益交付记录加载失败</CardTitle><CardDescription className="mt-2">当前没有可靠记录快照，不展示推测状态、私密资料或积分退回结果。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  const stats = [
    { label: '待处理', value: pendingCount, hint: '等待客服核对与安排', icon: Clock3, active: status === 'PENDING', onClick: () => { setStatus(status === 'PENDING' ? 'ALL' : 'PENDING'); setTagReady(false); setTagReview(false) } },
    { label: '交付中', value: fulfillingCount, hint: '已安排，等待完成交付', icon: Truck, active: status === 'FULFILLING', onClick: () => { setStatus(status === 'FULFILLING' ? 'ALL' : 'FULFILLING'); setTagReady(false); setTagReview(false) } },
    { label: 'TAG 待审核', value: tagReadyCount, hint: '内容已提交，可进入审核', icon: Tag, active: tagReady, onClick: () => { setTagReady(!tagReady); setTagReview(false); setStatus('ALL') } },
    { label: 'TAG 待复核', value: tagReviewCount, hint: '历史或暂停标签待处理', icon: BadgeCheck, active: tagReview, onClick: () => { setTagReview(!tagReview); setTagReady(false); setStatus('ALL') } },
  ]

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">权益交付</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">领取记录与客服履约</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">从积分兑换、转盘、会员和赠礼进入同一交付闭环，按历史快照核对资料、进度与结果。</p></div>
        <div className="border-l border-white/15 pl-4"><p className="text-2xl font-semibold tabular-nums">{claims.length}</p><p className="mt-1 text-[11px] text-blue-100/65">全部交付记录</p></div>
      </div>
    </section>

    {readonly && <Alert><ShieldAlert className="size-4" /><AlertTitle>当前为仅查看权限</AlertTitle><AlertDescription>可筛选并核对记录与交付资料；状态处理、TAG 审核和暂停需要 engagement:fulfill 操作权限。</AlertDescription></Alert>}
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>记录刷新失败，当前显示上次成功读取的快照</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">交付、取消和 TAG 操作已暂时锁定，避免基于旧资料处理。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {reviewActive && <Alert variant="destructive"><ShieldAlert className="size-4" /><AlertTitle>记录 #{selected.id} 的处理结果待核对</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请求结果未确认，请查询并核实，避免重复交付或退回积分。<Button variant="link" className="h-auto p-0" onClick={locateReview}>查询本记录</Button><Button variant="link" className="h-auto p-0" disabled={!reviewSearched} onClick={() => setReleaseOpen(true)}>已核对，解除限制</Button></AlertDescription></Alert>}
    {notice && <Alert role="status" className="border-success/30 bg-success/10"><CheckCircle2 className="size-4 text-success" /><AlertDescription>{notice}</AlertDescription></Alert>}

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="交付队列摘要">{stats.map(stat => <button type="button" key={stat.label} onClick={stat.onClick} aria-pressed={stat.active} className={cn('rounded-xl border bg-card p-4 text-left shadow-sm transition-colors hover:border-primary/35 hover:bg-accent/30', stat.active && 'border-primary bg-primary/5 ring-1 ring-primary/20')}><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-medium text-muted-foreground">{stat.label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{stat.value}</p></div><span className={cn('grid size-9 place-items-center rounded-lg bg-primary/10 text-primary', stat.active && 'bg-primary text-primary-foreground')}><stat.icon className="size-4" /></span></div><p className="mt-2 text-[11px] text-muted-foreground">{stat.hint}</p></button>)}</section>

    <Alert className="border-primary/20 bg-primary/5"><LockKeyhole className="size-4 text-primary" /><AlertTitle>列表脱敏，详情按权限读取</AlertTitle><AlertDescription>交付资料仅用于本次履约，不进入公开播报。只有积分兑换可取消并退回积分；赠礼、会员与转盘权益必须按实际交付处理。</AlertDescription></Alert>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="flex items-center gap-2 text-base"><PackageCheck className="size-4 text-primary" />交付队列</CardTitle><CardDescription className="mt-1">本页 {rows.length} 条 · 默认按记录编号倒序，优先核对资料与待办</CardDescription></div><Badge variant="outline" className="text-[10px]">示例数据 · 非真实业务验收</Badge></CardHeader>
      <div data-admin-filter className="grid gap-3 border-t p-4 xl:grid-cols-[minmax(240px,1.5fr)_repeat(4,minmax(128px,0.7fr))_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索权益交付记录" placeholder="权益、TAG、账号或记录 ID" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applySearch() }} className="pl-9" /></div>
        <Select value={status} onValueChange={value => setStatus(value as 'ALL' | AdminClaimStatus)}><SelectTrigger aria-label="交付状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem>{Object.entries(adminClaimStatusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={source} onValueChange={value => setSource(value as 'ALL' | AdminClaimSource)}><SelectTrigger aria-label="权益来源筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部来源</SelectItem>{Object.entries(adminClaimSourceLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={kind} onValueChange={value => setKind(value as 'ALL' | AdminClaimKind)}><SelectTrigger aria-label="权益类型筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部类型</SelectItem>{Object.entries(adminClaimKindLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        <Select value={accountType} onValueChange={value => setAccountType(value as 'ALL' | AdminClaimAccountType)}><SelectTrigger aria-label="申请人身份筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部身份</SelectItem><SelectItem value="BOSS">老板</SelectItem><SelectItem value="PROVIDER">陪玩</SelectItem></SelectContent></Select>
        <Button className="min-w-20 whitespace-nowrap" onClick={applySearch}><Search className="size-4" />查询</Button>
      </div>
      <div className="flex flex-wrap items-center gap-5 border-t bg-muted/10 px-4 py-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs"><Checkbox checked={tagReady} onCheckedChange={value => { setTagReady(value === true); if (value) setTagReview(false) }} aria-label="仅看 TAG 待审核" />TAG 待审核<span className="text-muted-foreground">已提交且无需退回</span></label>
        <label className="flex cursor-pointer items-center gap-2 text-xs"><Checkbox checked={tagReview} onCheckedChange={value => { setTagReview(value === true); if (value) setTagReady(false) }} aria-label="仅看 TAG 待复核" />TAG 待复核<span className="text-muted-foreground">历史未审或已暂停</span></label>
        {(keyword || status !== 'ALL' || source !== 'ALL' || kind !== 'ALL' || accountType !== 'ALL' || tagReady || tagReview) && <Button variant="ghost" size="sm" className="ml-auto" onClick={clearFilters}>清除筛选</Button>}
      </div>
      <div className="overflow-x-auto border-t">
        <Table className="min-w-[1060px]">
          <TableHeader><TableRow><TableHead>权益 / 记录</TableHead><TableHead>申请人</TableHead><TableHead>来源 / 类型</TableHead><TableHead>交付状态</TableHead><TableHead>资料摘要（已脱敏）</TableHead><TableHead>创建时间</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(claim => {
            const details = Object.entries(claim.details).filter(([, value]) => value).slice(0, 2)
            return <TableRow key={claim.id}>
              <TableCell><div className="flex max-w-[260px] items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">{claim.kindSnapshot === 'TAG' ? <Tag className="size-4" /> : <Gift className="size-4" />}</span><div className="min-w-0"><p className="truncate text-xs font-semibold">{claim.nameSnapshot}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">记录 #{claim.id} · 商品 #{claim.reward}</p>{claim.pointsSpent > 0 && <p className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground"><Coins className="size-3" />{claim.pointsSpent} 积分{claim.pointsReturned && ' · 已退回'}</p>}</div></div></TableCell>
              <TableCell><div className="min-w-32"><p className="text-xs font-medium">{claim.accountName}</p><p className="mt-1 text-[10px] text-muted-foreground">{claim.accountType === 'BOSS' ? '老板' : '陪玩'} · 账号 #{claim.account}</p><p className="mt-1 font-mono text-[10px] text-muted-foreground">{claim.username}</p></div></TableCell>
              <TableCell><div className="flex min-w-28 flex-col items-start gap-1.5"><Badge variant="secondary">{adminClaimSourceLabels[claim.source]}</Badge><span className="text-[11px] text-muted-foreground">{adminClaimKindLabels[claim.kindSnapshot]}{claim.kindSnapshot === 'TAG' && claim.highlighted ? ' · 高亮' : ''}</span>{claim.sourceOrder && <span className="text-[10px] text-muted-foreground">订单 #{claim.sourceOrder}</span>}</div></TableCell>
              <TableCell><div className="flex min-w-32 flex-col items-start gap-1.5"><Badge variant="outline" className={cn(claim.status === 'COMPLETED' && 'border-success/25 bg-success/10 text-success', claim.status === 'CANCELLED' && 'text-muted-foreground', claim.status === 'FULFILLING' && 'border-primary/25 bg-primary/10 text-primary')}>{adminClaimStatusLabels[claim.status]}</Badge>{claim.tagRevisionRequired && <span className="text-[10px] text-warning">等待用户修改 TAG</span>}{isAdminClaimTagReady(claim) && <span className="text-[10px] text-primary">TAG 内容待审核</span>}{isAdminClaimTagReviewNeeded(claim) && <span className="text-[10px] text-warning">{claim.tagSuspensionReason ? '已暂停 · 待复核' : '历史标签 · 待复核'}</span>}</div></TableCell>
              <TableCell><div className="max-w-[240px] space-y-1">{details.length ? details.map(([key, value]) => <p key={key} className="truncate text-[11px]" title={`${adminClaimDetailLabels[key] || key}：${maskAdminClaimDetail(key, value)}`}><span className="text-muted-foreground">{adminClaimDetailLabels[key] || key}：</span>{maskAdminClaimDetail(key, value)}</p>) : <span className="text-[11px] text-muted-foreground">无需补充资料</span>}</div></TableCell>
              <TableCell><span className="whitespace-nowrap text-[11px] text-muted-foreground tabular-nums">{formatAdminClaimTime(claim.createdAt)}</span></TableCell>
              <TableCell><div className="flex justify-end"><Button variant="outline" size="sm" disabled={operationsLocked && !readonly} onClick={() => openClaim(claim)}>{readonly || claim.status === 'CANCELLED' ? <Eye className="size-3.5" /> : <PackageCheck className="size-3.5" />}{readonly || claim.status === 'CANCELLED' ? '查看' : claim.status === 'COMPLETED' ? claim.kindSnapshot === 'TAG' ? '管理' : '查看' : '处理'}</Button></div></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
        {rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ListFilter className="size-8 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的交付记录</h2><p className="mt-2 text-sm text-muted-foreground">当前筛选没有结果，可清除关键词、状态与 TAG 队列条件。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除筛选</Button></div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span className="flex items-center gap-1.5"><Info className="size-3.5" />处理操作仅更新本地示例，不发送真实通知或资金变动。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    {dialogOpen && <AdminClaimProcessDialog key={`${selected.id}-${scenario}`} open={dialogOpen} item={selected} scenario={scenario} onClose={closeDialog} onProcess={processClaim} onReview={reviewTag} onSuspend={suspendTag} />}
    <AlertDialog open={releaseOpen} onOpenChange={setReleaseOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>解除交付处理限制？</AlertDialogTitle><AlertDialogDescription>请确认已查询记录 #{selected.id} 的最新状态、资料和说明，并核实原请求是否已经完成。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>继续核对</AlertDialogCancel><AlertDialogAction onClick={() => { setReleaseOpen(false); setReviewActive(false); onScenarioChange('default') }}>已核对，解除限制</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>
}
