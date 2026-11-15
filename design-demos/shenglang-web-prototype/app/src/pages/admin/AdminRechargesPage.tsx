import { useMemo, useState } from 'react'
import {
  ArrowLeftRight,
  BadgeCheck,
  CircleAlert,
  Clock3,
  Coins,
  FileImage,
  ImageOff,
  Landmark,
  ReceiptText,
  RotateCcw,
  Search,
  ShieldCheck,
  UserRound,
  WalletCards,
} from 'lucide-react'
import type { AdminRechargesScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { initialAdminRecharges, type AdminRechargeRecord } from '@/pages/admin/admin-recharge-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminRechargesPageProps {
  scenario: AdminRechargesScenario
  initialWalletId?: number
  onScenarioChange: (scenario: AdminRechargesScenario) => void
  onOpenTransactions: (walletId?: number) => void
}

const emptyFilters = { bossNo: '', nickname: '', phone: '', tradeNo: '' }

function coins(amount: number) {
  return formatWaveAmount(amount / 10)
}

function rmb(amount: number) {
  return `¥${(amount / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function AdminRechargesPage({ scenario, initialWalletId, onScenarioChange, onOpenTransactions }: AdminRechargesPageProps) {
  const [walletScope, setWalletScope] = useState<number | null>(initialWalletId ?? (scenario === 'wallet' ? 1001 : null))
  const [filters, setFilters] = useState({ ...emptyFilters, tradeNo: scenario === 'trade' ? 'DEMO-PAY-20261104-5008' : '' })
  const [applied, setApplied] = useState({ ...emptyFilters, tradeNo: scenario === 'trade' ? 'DEMO-PAY-20261104-5008' : '' })
  const [proofRecord, setProofRecord] = useState<AdminRechargeRecord | null>(null)
  const [proofRecovered, setProofRecovered] = useState(false)

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    const includes = (value: string, query: string) => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    return initialAdminRecharges.filter(row =>
      (!walletScope || row.walletId === walletScope) &&
      (!applied.bossNo.trim() || includes(row.bossNo, applied.bossNo)) &&
      (!applied.nickname.trim() || includes(row.nickname, applied.nickname)) &&
      (!applied.phone.trim() || includes(row.phone, applied.phone)) &&
      (!applied.tradeNo.trim() || row.tradeNo.toLocaleLowerCase() === applied.tradeNo.trim().toLocaleLowerCase()),
    ).sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id - a.id)
  }, [applied, scenario, walletScope])
  const totals = rows.reduce((sum, row) => ({ paid: sum.paid + row.amount, gift: sum.gift + row.giftAmount, credited: sum.credited + row.totalAmount }), { paid: 0, gift: 0, credited: 0 })
  const scopedRecord = walletScope ? initialAdminRecharges.find(row => row.walletId === walletScope) : null
  const proofLoading = scenario === 'proof-loading' && !proofRecovered
  const proofFailed = scenario === 'proof-error' && !proofRecovered
  const proofUrl = proofRecord && scenario !== 'no-proof' ? proofRecord.proofImageUrl : null

  function clearFilters() {
    setFilters(emptyFilters)
    setApplied(emptyFilters)
    setWalletScope(null)
    if (scenario === 'empty') onScenarioChange('default')
  }

  function openProof(record: AdminRechargeRecord) {
    setProofRecovered(false)
    setProofRecord(record)
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="充值记录加载中"><Skeleton className="h-32 rounded-xl" /><div className="grid gap-4 md:grid-cols-4"><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /><Skeleton className="h-28 rounded-xl" /></div><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">充值记录加载失败</CardTitle><CardDescription className="mt-2">请检查网络或充值记录查看权限后重试，当前没有展示推测到账记录。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1500px] flex-col gap-4">
    <section className="overflow-hidden rounded-xl border bg-[#10285b] text-white shadow-sm"><div className="grid gap-5 p-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-end md:p-6">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">充值记录</h1><Badge className="border-white/20 bg-white/10 text-[11px] text-white hover:bg-white/10">只读台账 · 示例</Badge></div><p className="mt-2 max-w-2xl text-sm leading-6 text-blue-100/80">按账户或完整收款流水号核对客服确认的到账记录。实收人民币、赠送与合计到账分口径展示，不在此页补录或修改。</p></div>
      <Button variant="outline" className="border-white/25 bg-transparent text-white hover:bg-white/10 hover:text-white" onClick={() => onOpenTransactions(walletScope ?? undefined)}><ArrowLeftRight className="size-4" />核对资金流水</Button>
    </div></section>

    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      { label: '当前充值记录', value: rows.length.toLocaleString('zh-CN'), unit: '笔', note: '按创建时间倒序', icon: ReceiptText, tone: 'text-primary' },
      { label: '实收人民币', value: rmb(totals.paid), unit: '', note: '仅实际收款，不等同到账浪花', icon: Landmark, tone: 'text-foreground' },
      { label: '充值赠送', value: coins(totals.gift), unit: '浪花', note: '独立关联 GIFT 流水', icon: Coins, tone: 'text-primary' },
      { label: '合计到账', value: `+${coins(totals.credited)}`, unit: '浪花', note: '实充与赠送的内部账务合计', icon: WalletCards, tone: 'text-success' },
    ].map(item => <div key={item.label} className="rounded-xl border bg-card p-4 shadow-sm"><div className="flex items-center justify-between"><dt className="text-xs text-muted-foreground">{item.label}</dt><item.icon className={cn('size-4', item.tone)} /></div><dd className={cn('mt-3 text-2xl font-semibold tabular-nums', item.tone)}>{item.value}<span className="ml-1 text-xs font-normal text-muted-foreground">{item.unit}</span></dd><p className="mt-1 text-[11px] text-muted-foreground">{item.note}</p></div>)}</dl>

    <Alert><ShieldCheck className="size-4" /><AlertTitle>当前汇总仅对应筛选结果中的示例记录</AlertTitle><AlertDescription>100 内部账务单位 = 1 元，10 单位 = 1 浪花；合计到账 = 实充 + 赠送。收款凭证可能缺失，不会用其他记录的图片替代。</AlertDescription></Alert>
    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>最新充值记录刷新失败</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">当前仍展示上次成功读取的示例快照。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新刷新</Button></AlertDescription></Alert>}

    <Card className="min-w-0 overflow-hidden"><CardHeader className="gap-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2 text-base"><ReceiptText className="size-4 text-primary" />充值与赠送明细</CardTitle><CardDescription className="mt-1">本页 {rows.length} 笔 · 只读核对 · recharge:view</CardDescription></div><Badge variant="outline" className="gap-1.5 font-normal"><ShieldCheck className="size-3.5" />不连接真实支付或钱包</Badge></div>
      {walletScope && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-primary/5 p-3 text-xs"><span className="flex items-center gap-2"><WalletCards className="size-4 text-primary" />钱包 #{walletScope}{scopedRecord ? ` · ${scopedRecord.nickname} · ${scopedRecord.username}` : ''}</span><Button variant="ghost" size="sm" onClick={() => setWalletScope(null)}>清除钱包筛选</Button></div>}
      <form data-admin-filter className="grid items-end gap-3 md:grid-cols-2 xl:grid-cols-4" onSubmit={event => { event.preventDefault(); setApplied(filters) }}>
        <div className="space-y-2"><Label htmlFor="recharge-boss-no">老板编号</Label><Input id="recharge-boss-no" value={filters.bossNo} onChange={event => setFilters(value => ({ ...value, bossNo: event.target.value }))} placeholder="包含查询，如 B1001" /></div>
        <div className="space-y-2"><Label htmlFor="recharge-nickname">昵称</Label><Input id="recharge-nickname" value={filters.nickname} onChange={event => setFilters(value => ({ ...value, nickname: event.target.value }))} placeholder="按昵称包含查询" /></div>
        <div className="space-y-2"><Label htmlFor="recharge-phone">手机号</Label><Input id="recharge-phone" inputMode="tel" value={filters.phone} onChange={event => setFilters(value => ({ ...value, phone: event.target.value }))} placeholder="按手机号包含查询" /></div>
        <div className="space-y-2"><Label htmlFor="recharge-trade-no">收款流水号</Label><Input id="recharge-trade-no" maxLength={64} value={filters.tradeNo} onChange={event => setFilters(value => ({ ...value, tradeNo: event.target.value }))} placeholder="完整流水号 · 精确匹配" className="font-mono text-xs" /></div>
        <div className="flex gap-2 md:col-span-2 xl:col-span-4"><Button type="submit" variant="outline"><Search className="size-4" />查询</Button><Button type="button" variant="ghost" onClick={clearFilters}>清除筛选</Button><span className="ml-auto hidden self-center text-[11px] text-muted-foreground sm:inline">编号、昵称、手机号可叠加；流水号需完整输入</span></div>
      </form></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1160px]"><TableHeader><TableRow><TableHead className="w-[25%]">收款账户</TableHead><TableHead className="w-[20%]">到账明细</TableHead><TableHead className="w-[27%]">流水与凭证</TableHead><TableHead>经办记录</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => <TableRow key={row.id} className="align-top">
        <TableCell><div className="flex items-center gap-3"><Avatar className="size-9 border"><AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">{row.nickname.slice(0, 1)}</AvatarFallback></Avatar><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><strong className="text-sm font-medium">{row.nickname}</strong><Badge variant="outline" className="text-[10px]">{row.userRole === 'CUSTOMER' ? '老板' : '陪玩'}</Badge></div><p className="mt-1 text-[11px] text-muted-foreground">记录 #{row.id} · 钱包 #{row.walletId}</p></div></div><dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]"><div className="flex gap-2"><dt className="text-muted-foreground">编号</dt><dd>{row.bossNo || '未记录'}</dd></div><div className="flex gap-2"><dt className="text-muted-foreground">账号</dt><dd>{row.username}</dd></div><div className="col-span-2 flex gap-2"><dt className="text-muted-foreground">手机</dt><dd className="tabular-nums">{row.phone || '未填写'}</dd></div>{(row.userRole === 'CUSTOMER' || row.bossTypeName) && <div className="col-span-2 flex items-center gap-2"><dt className="text-muted-foreground">分级</dt><dd className="flex items-center gap-1"><BadgeCheck className="size-3 text-primary" />{row.bossTypeName || '未分级'}</dd></div>}</dl></TableCell>
        <TableCell><span className="text-[11px] text-muted-foreground">合计到账</span><strong className="mt-1 block text-lg font-semibold tabular-nums text-success">+{coins(row.totalAmount)} <span className="text-xs font-normal">浪花</span></strong><dl className="mt-3 space-y-1.5 border-t pt-3 text-xs"><div className="flex justify-between gap-4"><dt className="text-muted-foreground">实收人民币</dt><dd className="font-medium tabular-nums">{rmb(row.amount)}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">实充浪花</dt><dd className="tabular-nums">{coins(row.amount)}</dd></div><div className="flex justify-between gap-4"><dt className="text-muted-foreground">赠送</dt><dd className="tabular-nums">{coins(row.giftAmount)} 浪花</dd></div></dl><div className="mt-3 flex flex-wrap items-center gap-1.5"><Badge variant="outline" className="font-mono text-[9px] font-normal">TOPUP · 实充</Badge>{row.giftAmount > 0 ? <Badge variant="outline" className="font-mono text-[9px] font-normal">GIFT · 赠送</Badge> : <span className="text-[10px] text-muted-foreground">无赠送流水</span>}</div></TableCell>
        <TableCell><span className="text-[11px] text-muted-foreground">收款流水号</span><p className="mt-1 break-all font-mono text-[11px] leading-5">{row.tradeNo || '未记录'}</p><div className="mt-3 flex flex-wrap items-center gap-2"><Button variant="outline" size="sm" aria-label={`核对充值记录 ${row.id} 的收款凭证`} onClick={() => openProof(row)}>{scenario === 'no-proof' || !row.proofImageUrl ? <ImageOff className="size-3.5" /> : <FileImage className="size-3.5" />}{scenario === 'no-proof' || !row.proofImageUrl ? '未上传／待补充' : '核对凭证'}</Button><Button variant="ghost" size="sm" aria-label={`查看钱包 ${row.walletId} 的资金流水`} onClick={() => onOpenTransactions(row.walletId)}><ArrowLeftRight className="size-3.5" />资金流水</Button></div></TableCell>
        <TableCell><time className="flex items-center gap-1.5 text-xs tabular-nums"><Clock3 className="size-3.5 text-muted-foreground" />{row.createdAt}</time><p className="mt-3 flex items-center gap-1.5 text-xs"><UserRound className="size-3.5 text-muted-foreground" />{row.operatorName || '未记录'}</p><p className="mt-2 max-w-sm whitespace-pre-wrap text-[11px] leading-5 text-muted-foreground">{row.remark || '未填写备注'}</p></TableCell>
      </TableRow>)}</TableBody></Table>{rows.length === 0 && <div className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><ReceiptText className="size-7 text-muted-foreground" /><h2 className="mt-4 text-base font-semibold">没有匹配的充值记录</h2><p className="mt-2 text-sm text-muted-foreground">收款流水号需完整精确匹配；也可调整账户条件或清除钱包范围。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>查看全部记录</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/20 px-4 py-3 text-xs text-muted-foreground"><span>当前 {rows.length} 笔 · 所有账号、金额、流水号与凭证均为示例。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={!!proofRecord} onOpenChange={open => { if (!open) setProofRecord(null) }}><DialogContent className="max-h-[92dvh] overflow-y-auto sm:max-w-3xl"><DialogHeader><DialogTitle>到账与凭证核对 · 记录 #{proofRecord?.id}</DialogTitle><DialogDescription>{proofRecord?.nickname} · {proofRecord?.tradeNo || '未记录'} · 本地示例预览</DialogDescription></DialogHeader>
      {proofRecord && <section aria-label="到账核对信息" className="rounded-lg border bg-muted/20 p-3"><dl className="grid grid-cols-2 gap-3 sm:grid-cols-4"><div><dt className="text-[11px] text-muted-foreground">实收人民币</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{rmb(proofRecord.amount)}</dd></div><div><dt className="text-[11px] text-muted-foreground">实充浪花</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{coins(proofRecord.amount)}</dd></div><div><dt className="text-[11px] text-muted-foreground">充值赠送</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{coins(proofRecord.giftAmount)} 浪花</dd></div><div><dt className="text-[11px] text-muted-foreground">合计到账</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-success">+{coins(proofRecord.totalAmount)} 浪花</dd></div></dl><div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-[11px] text-muted-foreground"><span>经办：{proofRecord.operatorName || '未记录'}</span><span>充值时间：{proofRecord.createdAt}</span><span>实充与赠送分流水核对</span></div></section>}
      {proofLoading && <div className="space-y-3" role="status"><p className="text-sm text-muted-foreground">正在读取收款凭证…</p><Skeleton className="aspect-[3/4] max-h-[58dvh] w-full rounded-xl" /></div>}
      {proofFailed && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>凭证读取失败</AlertTitle><AlertDescription>没有展示其他凭证或推测图片，请独立重试。<Button variant="link" className="ml-2 h-auto p-0" onClick={() => setProofRecovered(true)}>重新读取凭证</Button></AlertDescription></Alert>}
      {!proofLoading && !proofFailed && !proofUrl && <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed bg-muted/20 p-8 text-center"><ImageOff className="size-9 text-muted-foreground" /><h3 className="mt-4 text-base font-semibold">收款凭证未上传</h3><p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">此记录当前没有可预览的凭证。请根据经办人与备注核对原始材料，不用其他记录的图片代替。</p></div>}
      {!proofLoading && !proofFailed && proofUrl && <div className="overflow-hidden rounded-xl border bg-muted/30"><img src={proofUrl} alt={`充值记录 ${proofRecord?.id} 的示例收款凭证`} className="mx-auto max-h-[62dvh] w-auto max-w-full object-contain" /></div>}
      <DialogFooter>{proofLoading && <Button variant="outline" onClick={() => setProofRecovered(true)}>完成示例读取</Button>}<Button variant="outline" onClick={() => setProofRecord(null)}>关闭</Button></DialogFooter></DialogContent></Dialog>

  </div>
}
