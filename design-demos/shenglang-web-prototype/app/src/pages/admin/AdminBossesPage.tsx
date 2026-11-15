import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ContactRound,
  Crown,
  LockKeyhole,
  RotateCcw,
  Search,
  ShieldCheck,
  WalletCards,
} from 'lucide-react'
import type { AdminBossesScenario } from '@/components/admin/admin-config'
import { useStore } from '@/components/store/store-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { adminBossesForStore, adminBossStatusLabel, type AdminBoss } from '@/pages/admin/admin-boss-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminBossesPageProps {
  scenario: AdminBossesScenario
  onScenarioChange: (scenario: AdminBossesScenario) => void
}

export function AdminBossesPage({ scenario, onScenarioChange }: AdminBossesPageProps) {
  const { store } = useStore()
  const allBosses = useMemo(() => adminBossesForStore(store), [store])
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('ALL')
  const [member, setMember] = useState('ALL')
  const [selectedBoss, setSelectedBoss] = useState<AdminBoss | null>(null)
  const members = useMemo(() => [...new Set(allBosses.map(boss => boss.memberName))], [allBosses])
  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return allBosses.filter(boss => {
      if (keyword && !`${boss.nickname} ${boss.username} ${boss.id} ${boss.phone}`.toLowerCase().includes(keyword.toLowerCase())) return false
      if (status !== 'ALL' && boss.status !== status) return false
      if (member !== 'ALL' && boss.memberName !== member) return false
      return true
    })
  }, [allBosses, keyword, member, scenario, status])

  function search(event: FormEvent) {
    event.preventDefault()
    setKeyword(keywordInput.trim())
  }

  function clearFilters() {
    setKeywordInput('')
    setKeyword('')
    setStatus('ALL')
    setMember('ALL')
    onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="老板档案加载中"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-32 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">老板档案加载失败</CardTitle><CardDescription className="mt-2">请检查网络和当前门店的数据访问权限后重试。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  const boundWechat = allBosses.filter(boss => boss.wechatBound).length
  const boundOtp = allBosses.filter(boss => boss.otpBound).length
  const totalSpend = allBosses.reduce((total, boss) => total + boss.totalSpend, 0)

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="flex flex-wrap items-start gap-3">
      <div className="min-w-[260px] flex-1"><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">老板档案</h1><Badge variant="outline" className="border-warning/40 bg-warning/10 text-[11px]">示例快照</Badge></div><p className="mt-1 text-sm text-muted-foreground">管理{store.shortName}的老板账号、会员权益与本店消费关系。</p></div>
      <Badge variant="outline" className="h-9 gap-2 px-3 text-xs"><LockKeyhole className="size-3.5 text-primary" />{store.name} · 独立数据</Badge>
    </section>

    <section aria-label="老板账号摘要" className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border md:grid-cols-4">
      <SummaryItem label="本店老板总数" value={store.metrics.bosses} note="仅当前门店" />
      <SummaryItem label="微信已绑定" value={`${boundWechat} / ${allBosses.length}`} note="当前示例档案" />
      <SummaryItem label="OTP 已绑定" value={`${boundOtp} / ${allBosses.length}`} note="账号安全关系" />
      <SummaryItem label="示例累计消费" value={`¥ ${totalSpend.toLocaleString('zh-CN')}`} note="不跨店汇总" />
    </section>

    <Alert><ShieldCheck className="size-4" /><AlertTitle>老板账号按门店独立</AlertTitle><AlertDescription>同一微信可在不同门店拥有独立账号、会员、钱包与订单。本页不提供跨店合并，也不允许后台代设老板密码。</AlertDescription></Alert>

    <Card><CardContent className="py-4">
      <form data-admin-filter onSubmit={search} className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(260px,1fr)_180px_180px_auto]">
        <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="搜索老板" value={keywordInput} onChange={event => setKeywordInput(event.target.value)} placeholder="昵称 / 账号 / 编号 / 手机号" className="h-11 pl-9" /></div>
        <Select value={member} onValueChange={setMember}><SelectTrigger className="h-11 w-full" aria-label="会员等级"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部会员</SelectItem>{members.map(item => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
        <Select value={status} onValueChange={setStatus}><SelectTrigger className="h-11 w-full" aria-label="老板账号状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部状态</SelectItem>{Object.entries(adminBossStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-1"><Button type="submit" className="h-11 min-w-20 whitespace-nowrap">查询</Button><Button type="button" variant="outline" className="h-11 min-w-24 whitespace-nowrap" onClick={clearFilters}>清除条件</Button></div>
      </form>
      {keywordInput.trim() !== keyword && <p role="status" className="mt-2 text-xs text-warning-foreground">搜索内容已更改，列表尚未更新；请点击查询。</p>}
    </CardContent></Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0"><div><CardTitle className="text-base">本店老板列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 人 · 账号与消费均为示例</CardDescription></div><div className="flex items-center gap-2 text-xs text-muted-foreground"><ContactRound className="size-4" />正常 {allBosses.filter(boss => boss.status === 'ACTIVE').length} / {allBosses.length}</div></CardHeader>
      <div className="overflow-x-auto border-t"><Table className="min-w-[1180px]">
        <TableHeader><TableRow><TableHead>老板 / 账号</TableHead><TableHead>微信与安全</TableHead><TableHead>会员等级</TableHead><TableHead className="text-right">本店消费</TableHead><TableHead className="text-right">钱包 / 积分</TableHead><TableHead>最近活跃</TableHead><TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(boss => <TableRow key={boss.id}>
          <TableCell><div className="flex items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary">{boss.nickname.slice(0, 1)}</span><div><button type="button" onClick={() => setSelectedBoss(boss)} className="text-sm font-medium text-primary hover:underline">{boss.nickname}</button><p className="mt-1 font-mono text-[11px] text-muted-foreground">#{boss.id} · {boss.username}</p></div></div></TableCell>
          <TableCell><span className={cn('flex items-center gap-1.5 text-xs', boss.wechatBound ? 'text-success' : 'text-warning-foreground')}><CheckCircle2 className="size-3.5" />{boss.wechatBound ? '微信已绑定' : '微信未绑定'}</span><span className="mt-1.5 block text-[11px] text-muted-foreground">{boss.otpBound ? 'OTP 已绑定' : 'OTP 未绑定'} · {boss.phone}</span></TableCell>
          <TableCell><Badge variant="outline" className="gap-1.5 whitespace-nowrap text-[11px]"><Crown className="size-3 text-warning" />{boss.memberName}</Badge><span className="mt-1.5 block text-[11px] text-muted-foreground">{boss.joinedAt.slice(0, 10)} 加入本店</span></TableCell>
          <TableCell className="whitespace-nowrap text-right"><span className="text-sm font-semibold tabular-nums">¥ {boss.totalSpend.toLocaleString('zh-CN')}</span><span className="mt-1 block text-[11px] text-muted-foreground">{boss.orderCount} 笔订单</span></TableCell>
          <TableCell className="whitespace-nowrap text-right"><span className="text-xs font-medium tabular-nums">{formatWaveAmount(boss.balance)} 浪花</span><span className="mt-1 block text-[11px] text-muted-foreground">{boss.points.toLocaleString('zh-CN')} 积分</span></TableCell>
          <TableCell><span className="whitespace-nowrap text-xs">{boss.lastActive}</span><span className="mt-1 block text-[11px] text-muted-foreground">{store.shortName}老板端</span></TableCell>
          <TableCell><Badge variant="outline" className={cn('whitespace-nowrap', boss.status === 'ACTIVE' ? 'border-success/30 bg-success/10 text-success' : 'text-muted-foreground')}>{adminBossStatusLabel[boss.status]}</Badge></TableCell>
          <TableCell className="text-right"><Button variant="ghost" size="sm" aria-label={`查看${boss.nickname}档案`} onClick={() => setSelectedBoss(boss)}>查看档案<ChevronRight className="size-3.5" /></Button></TableCell>
        </TableRow>)}</TableBody>
      </Table>{rows.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><ContactRound className="size-5" /></span><h2 className="mt-4 text-base font-semibold">没有匹配的老板</h2><p className="mt-2 text-sm text-muted-foreground">调整关键词或清除筛选后再查看。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除全部条件</Button></div>}</div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>不连接真实老板账号、会员或消费记录。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Sheet open={Boolean(selectedBoss)} onOpenChange={open => { if (!open) setSelectedBoss(null) }}>
      <SheetContent className="w-full sm:max-w-lg" aria-label="老板档案详情">
        {selectedBoss && <>
          <SheetHeader className="border-b"><div className="flex items-center gap-3 pr-8"><span className="grid size-11 shrink-0 place-items-center rounded-full bg-primary/10 text-base font-semibold text-primary">{selectedBoss.nickname.slice(0, 1)}</span><div><SheetTitle className="text-lg">{selectedBoss.nickname}</SheetTitle><SheetDescription className="mt-1">#{selectedBoss.id} · {store.name}</SheetDescription></div></div></SheetHeader>
          <div className="flex-1 overflow-y-auto px-6 py-5 phone-scrollbar">
            <section aria-labelledby="boss-account-detail"><h2 id="boss-account-detail" className="text-sm font-semibold">账号与绑定</h2><dl className="mt-3 divide-y border-y"><DetailRow label="登录账号" value={selectedBoss.username} /><DetailRow label="手机号" value={selectedBoss.phone} /><DetailRow label="微信绑定" value={selectedBoss.wechatBound ? '已绑定' : '未绑定'} /><DetailRow label="OTP 动态验证" value={selectedBoss.otpBound ? '已绑定' : '未绑定'} /><DetailRow label="账号状态" value={adminBossStatusLabel[selectedBoss.status]} /></dl></section>
            <section className="mt-6" aria-labelledby="boss-member-detail"><h2 id="boss-member-detail" className="flex items-center gap-2 text-sm font-semibold"><Crown className="size-4 text-warning" />会员与本店资产</h2><dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-4 rounded-xl bg-muted/40 p-4"><DetailValue label="会员等级" value={selectedBoss.memberName} /><DetailValue label="本店积分" value={`${selectedBoss.points.toLocaleString('zh-CN')} 积分`} /><DetailValue label="钱包余额" value={`${formatWaveAmount(selectedBoss.balance)} 浪花`} /><DetailValue label="累计消费" value={`¥ ${selectedBoss.totalSpend.toLocaleString('zh-CN')}`} /></dl><p className="mt-2 flex items-start gap-2 text-xs leading-5 text-muted-foreground"><WalletCards className="mt-0.5 size-3.5 shrink-0" />资产只在当前店内核对，资金调整请进入独立老板钱包。</p></section>
            <section className="mt-6" aria-labelledby="boss-dynamic-detail"><h2 id="boss-dynamic-detail" className="text-sm font-semibold">最近本店动态</h2><ol className="mt-3 space-y-3 border-l pl-4">{selectedBoss.recentDynamics.map((item, index) => <li key={item} className="relative text-sm leading-6 text-muted-foreground"><span className="absolute -left-[21px] top-2 size-2 rounded-full bg-primary" /><span className="mr-2 text-xs text-muted-foreground">{index + 1}.</span>{item}</li>)}</ol></section>
            <p className="mt-6 rounded-xl border border-dashed p-3 text-xs leading-5 text-muted-foreground">加入本店：{selectedBoss.joinedAt}<br />最近活跃：{selectedBoss.lastActive}<br />此详情为只读原型，不修改真实账号、绑定关系或资产。</p>
          </div>
          <SheetFooter className="border-t"><SheetClose asChild><Button variant="outline" className="h-11 w-full">关闭档案</Button></SheetClose></SheetFooter>
        </>}
      </SheetContent>
    </Sheet>
  </div>
}

function SummaryItem({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="bg-card p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 text-xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-[11px] text-muted-foreground">{note}</p></div>
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return <div className="flex min-h-11 items-center justify-between gap-4 py-2 text-sm"><dt className="text-muted-foreground">{label}</dt><dd className="text-right font-medium">{value}</dd></div>
}

function DetailValue({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-sm font-medium">{value}</dd></div>
}
