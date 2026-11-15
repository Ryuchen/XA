import { useMemo, useState } from 'react'
import {
  BookOpenCheck,
  CircleAlert,
  CircleMinus,
  Eye,
  Medal,
  ReceiptText,
  RefreshCw,
  Scale,
  Search,
  ShieldCheck,
  UserRound,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminDisposesScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { adminDisposeRecords, adminDisposeTypeLabel, type AdminDisposeRecord, type AdminDisposeType } from '@/pages/admin/admin-dispose-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminDisposesPageProps {
  scenario: AdminDisposesScenario
  onScenarioChange: (scenario: AdminDisposesScenario) => void
}

const scenarioType: Partial<Record<AdminDisposesScenario, AdminDisposeType>> = {
  rewards: 'REWARD',
  penalties: 'PENALTY',
}

export function AdminDisposesPage({ scenario, onScenarioChange }: AdminDisposesPageProps) {
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [type, setType] = useState<'ALL' | AdminDisposeType>(scenarioType[scenario] || 'ALL')
  const [selectedRecord, setSelectedRecord] = useState<AdminDisposeRecord | null>(null)
  const [resolved, setResolved] = useState(false)
  const loading = scenario === 'loading' && !resolved
  const loadError = scenario === 'error' && !resolved
  const unavailable = loading || loadError

  const totals = useMemo(() => adminDisposeRecords.reduce((summary, row) => ({
    reward: summary.reward + (row.disposeType === 'REWARD' ? row.amount : 0),
    penalty: summary.penalty + (row.disposeType === 'PENALTY' ? row.amount : 0),
  }), { reward: 0, penalty: 0 }), [])

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = appliedKeyword.trim().toLocaleLowerCase()
    return adminDisposeRecords.filter(row => {
      const matchesKeyword = !query || [row.nickname, row.username].some(value => value.toLocaleLowerCase().includes(query))
      const matchesType = type === 'ALL' || row.disposeType === type
      const matchesAccount = scenario !== 'account' || row.accountNo === 'PW-0102'
      return matchesKeyword && matchesType && matchesAccount
    })
  }, [appliedKeyword, scenario, type])

  const visibleReward = rows.filter(row => row.disposeType === 'REWARD').reduce((sum, row) => sum + row.amount, 0)
  const visiblePenalty = rows.filter(row => row.disposeType === 'PENALTY').reduce((sum, row) => sum + row.amount, 0)

  function clearFilters() {
    setKeyword('')
    setAppliedKeyword('')
    setType('ALL')
    if (scenario === 'empty' || scenario === 'account' || scenario === 'rewards' || scenario === 'penalties') onScenarioChange('default')
  }

  function applyKeyword() {
    if (!unavailable) setAppliedKeyword(keyword)
  }

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm">
      <div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
        <div>
          <div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">奖惩记录</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">只读台账</Badge></div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">按陪玩与类型核对奖励、罚款及经办记录。每一笔金额对应已发生的钱包记账，不在此页补录或修改。</p>
        </div>
        <div className="flex items-center gap-3 rounded-xl border border-white/15 bg-white/5 px-4 py-3">
          <span className="grid size-10 place-items-center rounded-lg bg-white/10"><ShieldCheck className="size-5 text-sky-300" /></span>
          <div><p className="text-sm font-medium">账务留痕</p><p className="mt-1 text-[11px] text-blue-100/65">金额方向明确 · 操作人可追溯</p></div>
        </div>
      </div>
    </section>

    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">示例记录</dt><ReceiptText className="size-4 text-primary" /></div><dd className="mt-3 text-2xl font-semibold tabular-nums">{adminDisposeRecords.length}<span className="ml-1 text-xs font-normal text-muted-foreground">笔</span></dd><p className="mt-1 text-[11px] text-muted-foreground">按记账时间倒序</p></div>
      <div className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">累计奖励</dt><Medal className="size-4 text-success" /></div><dd className="mt-3 text-2xl font-semibold tabular-nums text-success">+{formatWaveAmount(totals.reward)}<span className="ml-1 text-xs font-normal">浪花</span></dd><p className="mt-1 text-[11px] text-muted-foreground">{adminDisposeRecords.filter(row => row.disposeType === 'REWARD').length} 笔正向钱包记账</p></div>
      <div className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">累计罚款</dt><CircleMinus className="size-4 text-destructive" /></div><dd className="mt-3 text-2xl font-semibold tabular-nums text-destructive">−{formatWaveAmount(totals.penalty)}<span className="ml-1 text-xs font-normal">浪花</span></dd><p className="mt-1 text-[11px] text-muted-foreground">{adminDisposeRecords.filter(row => row.disposeType === 'PENALTY').length} 笔负向钱包记账</p></div>
      <div className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">示例净变动</dt><WalletCards className="size-4 text-primary" /></div><dd className="mt-3 text-2xl font-semibold tabular-nums">{totals.reward - totals.penalty >= 0 ? '+' : '−'}{formatWaveAmount(Math.abs(totals.reward - totals.penalty))}<span className="ml-1 text-xs font-normal text-muted-foreground">浪花</span></dd><p className="mt-1 text-[11px] text-muted-foreground">仅为当前示例记录合计</p></div>
    </dl>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>最新记录刷新失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">当前仍展示上次成功读取的示例台账。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新刷新</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <div><CardTitle className="flex items-center gap-2 text-base"><Scale className="size-4 text-primary" />陪玩奖惩明细</CardTitle><CardDescription className="mt-1">账号、昵称和类型条件可叠加查询；结果以记录创建时间倒序排列</CardDescription></div>
        <Badge variant="outline" className="gap-1.5"><BookOpenCheck className="size-3.5" />审计视图</Badge>
      </CardHeader>
      <div className="border-t px-4 py-4">
        {scenario === 'account' && <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2.5 text-xs"><UserRound className="size-4 text-primary" /><span>仅查看陪玩账户 <strong className="font-mono">PW-0102</strong> 的奖惩记录</span><Button variant="ghost" size="sm" className="ml-auto h-8" onClick={() => onScenarioChange('default')}><X className="size-3.5" />清除账户筛选</Button></div>}
        <div data-admin-filter className="flex flex-wrap items-end gap-3">
          <div className="min-w-56 flex-1 space-y-1.5 md:max-w-md"><Label htmlFor="dispose-keyword" className="text-xs">查找陪玩</Label><Input id="dispose-keyword" disabled={unavailable} value={keyword} placeholder="输入陪玩账号或昵称" onChange={event => setKeyword(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') applyKeyword() }} /></div>
          <div className="space-y-1.5"><Label className="text-xs">奖惩类型</Label><Select value={type} disabled={unavailable} onValueChange={value => setType(value as 'ALL' | AdminDisposeType)}><SelectTrigger className="w-40" aria-label="奖惩类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部类型</SelectItem><SelectItem value="REWARD">奖励</SelectItem><SelectItem value="PENALTY">罚款</SelectItem></SelectContent></Select></div>
          <Button disabled={unavailable} onClick={applyKeyword}><Search className="size-4" />查询记录</Button>
          {(appliedKeyword || type !== 'ALL') && <Button variant="ghost" disabled={unavailable} onClick={clearFilters}>清除筛选</Button>}
        </div>
      </div>

      {loading && <div className="space-y-3 border-t p-4" aria-label="正在读取奖惩记录">{[0, 1, 2, 3, 4].map(item => <Skeleton key={item} className="h-20 rounded-lg" />)}</div>}
      {loadError && <div className="flex min-h-64 flex-col items-center justify-center border-t p-8 text-center"><CircleAlert className="size-8 text-destructive" /><h2 className="mt-4 text-base font-semibold">奖惩记录加载失败</h2><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">暂时无法读取台账。请重试，不要在结果未确认时重复进行奖励或罚款记账。</p><Button className="mt-5" onClick={() => setResolved(true)}><RefreshCw className="size-4" />重新加载</Button></div>}
      {!unavailable && <>
        <div className="overflow-x-auto border-t"><Table className="min-w-[1040px]"><TableHeader><TableRow><TableHead className="w-[24%]">陪玩 / 账号</TableHead><TableHead className="w-[18%]">类型 / 金额</TableHead><TableHead className="w-[30%]">奖惩原因</TableHead><TableHead>经办记录</TableHead><TableHead className="w-20 text-right">详情</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id} className="align-top">
          <TableCell><div className="flex items-start gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><UserRound className="size-4" /></span><div className="min-w-0"><p className="text-sm font-medium">{row.nickname || '陪玩待核对'}</p><p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{row.accountNo} · {row.username || '未记录'}</p></div></div></TableCell>
          <TableCell><Badge variant="outline" className={cn('gap-1.5', row.disposeType === 'REWARD' ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive')}>{row.disposeType === 'REWARD' ? <Medal className="size-3.5" /> : <CircleMinus className="size-3.5" />}{adminDisposeTypeLabel[row.disposeType]}</Badge><p className={cn('mt-2 text-lg font-semibold tabular-nums', row.disposeType === 'REWARD' ? 'text-success' : 'text-destructive')}>{row.disposeType === 'REWARD' ? '+' : '−'}{formatWaveAmount(row.amount)}<span className="ml-1 text-xs font-normal">浪花</span></p></TableCell>
          <TableCell><p className="max-w-lg whitespace-pre-wrap text-sm leading-6">{row.reason || '未填写'}</p></TableCell>
          <TableCell><time className="text-xs tabular-nums">{row.createdAt}</time><p className="mt-2 text-[11px] leading-5 text-muted-foreground">记录 #{row.id}<br />操作人 {row.operatorName || '未记录'}</p></TableCell>
          <TableCell className="text-right"><Button variant="ghost" size="icon" className="size-8" aria-label={`查看奖惩记录 ${row.id}`} onClick={() => setSelectedRecord(row)}><Eye className="size-4" /></Button></TableCell>
        </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-56 flex-col items-center justify-center border-t p-8 text-center"><Scale className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的奖惩记录</h2><p className="mt-2 text-sm text-muted-foreground">调整陪玩账号、昵称或奖惩类型后重新查询。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>查看全部记录</Button></div>}</div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/20 px-4 py-3 text-xs text-muted-foreground"><span>当前 {rows.length} 笔 · 奖励 +{formatWaveAmount(visibleReward)} 浪花 · 罚款 −{formatWaveAmount(visiblePenalty)} 浪花</span><span>明确示例数据，不连接真实钱包。</span></div>
      </>}
    </Card>

    <Alert><BookOpenCheck className="size-4" /><AlertTitle>记录与钱包同步留痕</AlertTitle><AlertDescription>奖励增加陪玩余额，罚款扣减余额且不得使余额为负；此页仅用于事后核对。新增奖惩应从陪玩档案发起，并单独确认记账内容。</AlertDescription></Alert>

    <Dialog open={!!selectedRecord} onOpenChange={open => { if (!open) setSelectedRecord(null) }}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader><DialogTitle>奖惩记录 #{selectedRecord?.id}</DialogTitle><DialogDescription>已发生记账的只读审计快照，不支持在此修改或撤销。</DialogDescription></DialogHeader>
        {selectedRecord && <div className="space-y-4">
          <section className="rounded-xl border bg-muted/20 p-4">
            <div className="flex items-center gap-3 border-b pb-4"><span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><UserRound className="size-5" /></span><div className="min-w-0"><h2 className="text-sm font-semibold">{selectedRecord.nickname || '陪玩待核对'}</h2><p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{selectedRecord.accountNo} · {selectedRecord.username || '未记录'}</p></div></div>
            <dl className="grid grid-cols-2 gap-x-5 gap-y-4 pt-4">
              <div><dt className="text-[11px] text-muted-foreground">奖惩类型</dt><dd className="mt-1.5"><Badge variant="outline" className={selectedRecord.disposeType === 'REWARD' ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive'}>{adminDisposeTypeLabel[selectedRecord.disposeType]}</Badge></dd></div>
              <div><dt className="text-[11px] text-muted-foreground">钱包变动</dt><dd className={cn('mt-1 text-lg font-semibold tabular-nums', selectedRecord.disposeType === 'REWARD' ? 'text-success' : 'text-destructive')}>{selectedRecord.disposeType === 'REWARD' ? '+' : '−'}{formatWaveAmount(selectedRecord.amount)} <span className="text-xs font-normal">浪花</span></dd></div>
              <div><dt className="text-[11px] text-muted-foreground">记账时间</dt><dd className="mt-1.5 text-xs tabular-nums">{selectedRecord.createdAt}</dd></div>
              <div><dt className="text-[11px] text-muted-foreground">操作人</dt><dd className="mt-1.5 text-xs">{selectedRecord.operatorName || '未记录'}</dd></div>
            </dl>
          </section>
          <section className="rounded-xl border p-4"><h3 className="text-xs font-medium text-muted-foreground">奖惩原因</h3><p className="mt-2 whitespace-pre-wrap text-sm leading-6">{selectedRecord.reason || '未填写'}</p></section>
          <Alert><WalletCards className="size-4" /><AlertTitle>{selectedRecord.disposeType === 'REWARD' ? '奖励已增加陪玩余额' : '罚款已扣减陪玩余额'}</AlertTitle><AlertDescription>{selectedRecord.disposeType === 'REWARD' ? '此记录对应一笔正向钱包记账。' : '此记录对应一笔负向钱包记账，记账时余额不得为负。'}如需新增奖惩，请返回陪玩档案并单独确认。</AlertDescription></Alert>
          <p className="text-center text-[11px] text-muted-foreground">本地示例记录，不连接真实钱包或资金流水。</p>
        </div>}
      </DialogContent>
    </Dialog>
  </div>
}
