import { useMemo, useState } from 'react'
import {
  ArrowDownLeft,
  ArrowLeftRight,
  ArrowUpRight,
  BookOpenCheck,
  CircleAlert,
  Clock3,
  FileSearch,
  Info,
  Link2,
  LockKeyhole,
  ReceiptText,
  RotateCcw,
  Search,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import type { AdminTransactionsScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { adminTransactionStatusLabel, adminTransactionTypeLabel, initialAdminTransactions, type AdminTransaction, type AdminTransactionType } from '@/pages/admin/admin-transaction-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminTransactionsPageProps {
  scenario: AdminTransactionsScenario
  initialWalletId?: number
  onScenarioChange: (scenario: AdminTransactionsScenario) => void
  onOpenOrder: (orderNo: string) => void
}

function coins(amount: number) {
  return formatWaveAmount(amount / 10)
}

function signedCoins(amount: number) {
  return `${amount >= 0 ? '+' : ''}${coins(amount)}`
}

function typeLabel(row: AdminTransaction) {
  return row.type === 'SUBSIDY_EXPENSE' && row.amount > 0 ? '平台补贴追回' : adminTransactionTypeLabel[row.type]
}

function isIncomeHold(row: AdminTransaction) {
  return row.type === 'EARNINGS_FREEZE' || row.type === 'EARNINGS_RELEASE'
}

export function AdminTransactionsPage({ scenario, initialWalletId, onScenarioChange, onOpenOrder }: AdminTransactionsPageProps) {
  const [keyword, setKeyword] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [type, setType] = useState<'ALL' | AdminTransactionType>('ALL')
  const [walletScope, setWalletScope] = useState<number | null>(initialWalletId ?? (scenario === 'wallet' ? 1001 : null))
  const [orderScope, setOrderScope] = useState<string | null>(scenario === 'order' ? 'SL202611040031' : null)
  const [reversalOnly, setReversalOnly] = useState(scenario === 'reversals')
  const [originalId, setOriginalId] = useState<number | null>(null)
  const [originalRecovered, setOriginalRecovered] = useState(false)

  const canOpenOrder = scenario !== 'no-order-permission'
  const original = initialAdminTransactions.find(row => row.id === originalId) || null
  const originalFailed = scenario === 'original-error' && !originalRecovered
  const originalLoading = scenario === 'original-loading' && !originalRecovered
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const query = appliedKeyword.trim().toLocaleLowerCase()
    return initialAdminTransactions.filter(row =>
      (!query || row.txNo.toLocaleLowerCase().includes(query) || row.username.toLocaleLowerCase().includes(query)) &&
      (type === 'ALL' || row.type === type) &&
      (!walletScope || row.walletId === walletScope) &&
      (!orderScope || row.orderNo === orderScope) &&
      (!reversalOnly || !!row.reversalOf),
    ).sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id - a.id)
  }, [appliedKeyword, orderScope, reversalOnly, scenario, type, walletScope])
  const successfulRows = rows.filter(row => row.status === 'SUCCESS')
  const income = successfulRows.filter(row => row.amount > 0).reduce((sum, row) => sum + row.amount, 0)
  const expense = successfulRows.filter(row => row.amount < 0).reduce((sum, row) => sum + Math.abs(row.amount), 0)
  const scopedWallet = walletScope ? initialAdminTransactions.find(row => row.walletId === walletScope) : null

  function clearFilters() {
    setKeyword('')
    setAppliedKeyword('')
    setType('ALL')
    setWalletScope(null)
    setOrderScope(null)
    setReversalOnly(false)
    if (scenario === 'empty') onScenarioChange('default')
  }

  function openOriginal(id: number) {
    setOriginalRecovered(false)
    setOriginalId(id)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="资金流水加载中"><Skeleton className="h-32 rounded-xl" /><div className="grid gap-4 md:grid-cols-4"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /></div><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">资金流水加载失败</CardTitle><CardDescription className="mt-2">请检查网络或流水查看权限后重试，当前没有展示推测记录。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">资金流水</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">只读审计 · 示例</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">按流水号、账号或类型核对资金变化，结合变动后余额、关联订单与冲正记录追溯。金额单位为浪花。</p></div>
      <div className="flex items-center gap-3 rounded-xl border border-white/15 bg-white/5 px-4 py-3"><span className="grid size-10 place-items-center rounded-lg bg-white/10"><ShieldCheck className="size-5 text-sky-300" /></span><div><p className="text-sm font-medium">账务留痕</p><p className="mt-1 text-[11px] text-blue-100/65">只读记录 · 不在此页修改资金</p></div></div>
    </div></section>

    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      { label: '当前记录', value: rows.length.toLocaleString('zh-CN'), unit: '笔', note: '按创建时间倒序', icon: ReceiptText, tone: 'text-primary' },
      { label: '成功流入', value: `+${coins(income)}`, unit: '浪花', note: '仅当前筛选的成功记录', icon: ArrowUpRight, tone: 'text-success' },
      { label: '成功流出', value: `−${coins(expense)}`, unit: '浪花', note: '按金额绝对值汇总', icon: ArrowDownLeft, tone: 'text-foreground' },
      { label: '示例净变动', value: `${income - expense >= 0 ? '+' : '−'}${coins(Math.abs(income - expense))}`, unit: '浪花', note: '不等于平台经营收入', icon: ArrowLeftRight, tone: 'text-primary' },
    ].map(item => <div key={item.label} className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">{item.label}</dt><item.icon className={cn('size-4', item.tone)} /></div><dd className={cn('mt-3 text-2xl font-semibold tabular-nums', item.tone)}>{item.value}<span className="ml-1 text-xs font-normal text-muted-foreground">{item.unit}</span></dd><p className="mt-1 text-[11px] text-muted-foreground">{item.note}</p></div>)}</dl>

    <Alert><BookOpenCheck className="size-4" /><AlertTitle>冻结与解冻不是普通收支</AlertTitle><AlertDescription>收益冻结将余额转入冻结资金，解冻则转回；冲正记录必须结合原流水核对。失败记录不计入上方成功收支汇总。</AlertDescription></Alert>
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>最新流水刷新失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">当前仍展示上次成功读取的示例快照。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新刷新</Button></AlertDescription></Alert>}
    {!canOpenOrder && <Alert><LockKeyhole className="size-4" /><AlertTitle>当前无订单查看权限</AlertTitle><AlertDescription>仍可查看资金流水和关联订单号，但不能打开订单详情。</AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><ArrowLeftRight className="size-4 text-primary" />资金变动明细</CardTitle><CardDescription className="mt-1">本页 {rows.length} 笔 · 金额方向与变动后余额同时核对</CardDescription></div><Badge variant="outline" className="gap-1.5 font-normal"><ShieldCheck className="size-3.5" />transaction:view</Badge></div>
      {(walletScope || orderScope || reversalOnly) && <div className="flex flex-wrap gap-2 rounded-lg border bg-muted/30 p-3 text-xs">{walletScope && <span className="flex min-h-8 items-center gap-2 rounded-md border bg-card px-2.5"><WalletCards className="size-3.5 text-primary" />钱包 #{walletScope}{scopedWallet ? ` · ${scopedWallet.nickname}` : ''}<Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => setWalletScope(null)}>清除钱包筛选</Button></span>}{orderScope && <span className="flex min-h-8 items-center gap-2 rounded-md border bg-card px-2.5"><Link2 className="size-3.5 text-primary" />订单 {orderScope}<Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => setOrderScope(null)}>清除订单筛选</Button></span>}{reversalOnly && <span className="flex min-h-8 items-center gap-2 rounded-md border bg-card px-2.5"><ArrowLeftRight className="size-3.5 text-primary" />仅看冲正<Button variant="ghost" size="sm" className="h-6 px-1.5" onClick={() => setReversalOnly(false)}>清除冲正筛选</Button></span>}</div>}
      <form data-admin-filter className="grid items-end gap-3 md:grid-cols-[minmax(240px,1fr)_190px_auto]" onSubmit={event => { event.preventDefault(); setAppliedKeyword(keyword) }}><div className="space-y-2"><Label htmlFor="transaction-keyword">查找流水</Label><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input id="transaction-keyword" value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="流水号或账号" className="h-10 pl-9" /></div></div><div className="space-y-2"><Label htmlFor="transaction-type">流水类型</Label><Select value={type} onValueChange={value => setType(value as 'ALL' | AdminTransactionType)}><SelectTrigger id="transaction-type" className="h-10 w-full" aria-label="资金流水类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部类型</SelectItem>{Object.entries(adminTransactionTypeLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div><div className="flex items-center gap-2"><Button type="submit" variant="outline" className="h-10 min-w-20 whitespace-nowrap"><Search className="size-4" />查询</Button><Button type="button" variant="ghost" className="h-10 min-w-20 whitespace-nowrap" onClick={clearFilters}>清除筛选</Button></div></form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1080px]"><TableHeader><TableRow><TableHead className="w-[27%]">流水 / 账户</TableHead><TableHead className="w-[18%]">金额 / 余额</TableHead><TableHead className="w-[22%]">关联订单与冲正</TableHead><TableHead>经办记录</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id} className="align-top">
        <TableCell><div className="flex flex-wrap items-center gap-2"><strong className="text-sm font-medium">{typeLabel(row)}</strong><Badge variant="outline" className={cn(row.status === 'SUCCESS' ? 'border-success/30 bg-success/10 text-success' : row.status === 'PENDING' ? 'border-warning/30 bg-warning/10 text-warning' : 'border-destructive/30 bg-destructive/10 text-destructive')}>{adminTransactionStatusLabel[row.status]}</Badge></div><p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">{row.txNo}</p><p className="mt-1 text-[11px] text-muted-foreground">{row.nickname} · {row.username} · 钱包 #{row.walletId}</p>{isIncomeHold(row) && <p className="mt-2 flex items-start gap-1.5 rounded-md bg-primary/5 p-2 text-[11px] leading-5 text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0 text-primary" />{row.type === 'EARNINGS_FREEZE' ? '余额转入冻结资金' : '冻结资金转回余额'}</p>}</TableCell>
        <TableCell><strong className={cn('text-base font-semibold tabular-nums', row.amount > 0 && row.status === 'SUCCESS' && 'text-success')}>{signedCoins(row.amount)} <span className="text-xs font-normal">浪花</span></strong><p className="mt-2 text-[11px] text-muted-foreground">变动前 {coins(row.balanceBefore)}</p><p className="mt-1 text-xs tabular-nums">变动后 {coins(row.balanceAfter)} 浪花</p></TableCell>
        <TableCell>{row.orderNo ? <div className="flex items-start gap-1.5"><Link2 className="mt-0.5 size-3.5 shrink-0 text-primary" />{canOpenOrder ? <button type="button" className="break-all text-left font-mono text-[11px] text-primary underline underline-offset-4" onClick={() => onOpenOrder(row.orderNo!)}>{row.orderNo}</button> : <span className="break-all font-mono text-[11px]">{row.orderNo}</span>}</div> : <span className="text-xs text-muted-foreground">无关联订单</span>}{row.reversalOf && <div className="mt-3 flex flex-wrap items-center gap-2"><Badge variant="secondary">冲正</Badge><Button variant="outline" size="sm" onClick={() => openOriginal(row.reversalOf!)}><FileSearch className="size-3.5" />查看原流水 #{row.reversalOf}</Button></div>}</TableCell>
        <TableCell><time className="flex items-center gap-1.5 text-xs tabular-nums"><Clock3 className="size-3.5 text-muted-foreground" />{row.createdAt}</time><p className="mt-2 text-[11px] text-muted-foreground">操作人 {row.operatorName || '系统'}</p><p className="mt-1 max-w-md whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground">备注 {row.remark || '—'}</p></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ArrowLeftRight className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的资金流水</h2><p className="mt-2 text-sm text-muted-foreground">调整流水号、账号、类型或清除钱包／订单范围后重新查询。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>查看全部流水</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/20 px-4 py-3 text-xs text-muted-foreground"><span>当前 {rows.length} 笔 · 所有记录均为示例，不连接真实钱包。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!originalId} onOpenChange={open => { if (!open) setOriginalId(null) }}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>原流水 #{originalId}</DialogTitle><DialogDescription>以下为读取时的独立记录，用于核对冲正来源。</DialogDescription></DialogHeader>{originalLoading && <div className="space-y-3" role="status"><p className="text-sm text-muted-foreground">正在读取原流水…</p><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}{originalFailed && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>原流水读取失败</AlertTitle><AlertDescription>当前没有展示推测内容，请重试。<Button variant="link" className="h-auto p-0" onClick={() => setOriginalRecovered(true)}>重新读取</Button></AlertDescription></Alert>}{!originalLoading && !originalFailed && original && <><section className="rounded-xl border bg-primary/5 p-4"><div className="flex items-center justify-between gap-3"><div><p className="text-xs text-muted-foreground">原流水金额</p><p className="mt-1 text-2xl font-semibold tabular-nums">{signedCoins(original.amount)} 浪花</p></div><Badge variant="outline">{typeLabel(original)}</Badge></div><p className="mt-3 break-all border-t pt-3 font-mono text-[11px] text-muted-foreground">{original.txNo}</p></section><dl className="grid gap-x-6 rounded-xl border p-4 sm:grid-cols-2">{[
      ['账号', `${original.nickname} · ${original.username}`], ['钱包', `#${original.walletId}`], ['关联订单', original.orderNo || '—'], ['状态', adminTransactionStatusLabel[original.status]], ['变动前余额', `${coins(original.balanceBefore)} 浪花`], ['变动后余额', `${coins(original.balanceAfter)} 浪花`], ['操作人', original.operatorName || '系统'], ['时间', original.createdAt], ['备注', original.remark || '—'],
    ].map(([label, value]) => <div key={label} className="border-b py-3 last:border-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{value}</dd></div>)}</dl>{original.orderNo && canOpenOrder && <Button variant="outline" onClick={() => onOpenOrder(original.orderNo!)}><Link2 className="size-4" />打开关联订单</Button>}{isIncomeHold(original) && <Alert><Info className="size-4" /><AlertDescription>冻结将余额转入冻结资金，解冻则转回；请结合关联流水核对。</AlertDescription></Alert>}</>}<DialogFooter>{originalLoading && <Button variant="outline" onClick={() => setOriginalRecovered(true)}>完成示例读取</Button>}<Button variant="outline" onClick={() => setOriginalId(null)}>关闭</Button></DialogFooter></DialogContent></Dialog>

  </div>
}
