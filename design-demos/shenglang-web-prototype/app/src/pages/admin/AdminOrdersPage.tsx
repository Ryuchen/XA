import { useMemo, useState, type FormEvent } from 'react'
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Clock3,
  Filter,
  Headphones,
  RotateCcw,
  Search,
  Settings2,
  X,
} from 'lucide-react'
import type { AdminOrderDetailScenario, AdminOrdersScenario } from '@/components/admin/admin-config'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { adminOrders, adminOrderStatusLabel, adminPaymentStatusLabel, type AdminOrder } from '@/pages/admin/admin-order-data'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminOrdersPageProps {
  scenario: AdminOrdersScenario
  initialOrderKeyword?: string
  onScenarioChange: (scenario: AdminOrdersScenario) => void
  onOpen: (order: AdminOrder, scenario: AdminOrderDetailScenario) => void
}

type Queue = 'all' | 'staff' | 'aftersale' | 'gift'

function detailScenario(order: AdminOrder): AdminOrderDetailScenario {
  if (order.aftersale && order.status !== 'CANCELLED') return 'aftersale'
  if (order.paymentStatus === 'REFUNDED' || order.paymentStatus === 'PARTIALLY_REFUNDED') return 'refunded'
  if (order.status === 'PENDING') return 'pending'
  if (order.status === 'IN_SERVICE') return 'in-service'
  return 'default'
}

function StatusBadge({ order }: { order: AdminOrder }) {
  return <Badge variant="outline" className={cn('whitespace-nowrap', order.status === 'PENDING' && 'border-warning/40 bg-warning/10 text-warning-foreground', (order.status === 'GRABBED' || order.status === 'IN_SERVICE') && 'border-primary/30 bg-primary/10 text-primary', order.status === 'COMPLETED' && 'border-success/30 bg-success/10 text-success', order.status === 'CANCELLED' && 'text-muted-foreground')}>{adminOrderStatusLabel[order.status]}</Badge>
}

export function AdminOrdersPage({ scenario, initialOrderKeyword = '', onScenarioChange, onOpen }: AdminOrdersPageProps) {
  const [queue, setQueue] = useState<Queue>(scenario === 'staff' ? 'staff' : scenario === 'aftersale' ? 'aftersale' : 'all')
  const [orderInput, setOrderInput] = useState(initialOrderKeyword)
  const [bossInput, setBossInput] = useState('')
  const [applied, setApplied] = useState({ order: initialOrderKeyword, boss: '' })
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [status, setStatus] = useState('ALL')
  const [payment, setPayment] = useState('ALL')
  const [route, setRoute] = useState('ALL')
  const [selected, setSelected] = useState<number[]>([])
  const [quickOpen, setQuickOpen] = useState(false)
  const [quickReview, setQuickReview] = useState(false)
  const [timeoutOpen, setTimeoutOpen] = useState(false)
  const [deadline, setDeadline] = useState('30')
  const [feedback, setFeedback] = useState('')

  const rows = useMemo(() => {
    if (scenario === 'empty') return []
    return adminOrders.filter(order => {
      if (queue === 'staff' && !(order.status === 'PENDING' && order.paymentStatus === 'PAID' && order.route === 'STAFF')) return false
      if (queue === 'aftersale' && !order.aftersale) return false
      if (queue === 'gift' && !order.serviceName.startsWith('礼物单')) return false
      if (applied.order && !order.orderNo.toLowerCase().includes(applied.order.toLowerCase())) return false
      if (applied.boss && !`${order.customerName} ${order.customerUsername}`.toLowerCase().includes(applied.boss.toLowerCase())) return false
      if (status !== 'ALL' && order.status !== status) return false
      if (payment !== 'ALL' && order.paymentStatus !== payment) return false
      if (route !== 'ALL' && order.route !== route) return false
      return true
    })
  }, [applied, payment, queue, route, scenario, status])

  const filterCount = [status !== 'ALL', payment !== 'ALL', route !== 'ALL'].filter(Boolean).length
  const allSelected = rows.length > 0 && rows.every(order => selected.includes(order.id))

  function changeQueue(next: Queue) {
    setQueue(next)
    setSelected([])
    onScenarioChange(next === 'staff' ? 'staff' : next === 'aftersale' ? 'aftersale' : 'default')
  }

  function search(event: FormEvent) {
    event.preventDefault()
    setApplied({ order: orderInput.trim(), boss: bossInput.trim() })
    setSelected([])
  }

  function clearFilters() {
    setOrderInput(''); setBossInput(''); setApplied({ order: '', boss: '' })
    setStatus('ALL'); setPayment('ALL'); setRoute('ALL'); setQueue('all'); setSelected([])
    onScenarioChange('default')
  }

  if (scenario === 'loading') return <div className="space-y-4" aria-label="订单列表加载中"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-40 rounded-xl" /><Skeleton className="h-[520px] rounded-xl" /></div>
  if (scenario === 'error') return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><CircleAlert className="size-10 text-destructive" /><CardTitle className="mt-5 text-xl">订单列表加载失败</CardTitle><CardDescription className="mt-2">请检查网络和访问权限后重试，当前没有展示推测订单。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="flex flex-wrap items-start gap-3">
      <div className="min-w-[260px] flex-1"><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">订单与派单</h1><Badge variant="outline" className="border-warning/40 bg-warning/10 text-[11px]">示例快照</Badge></div><p className="mt-1 text-sm text-muted-foreground">管理平台全部陪玩订单与售后，操作前以详情复核结果为准。</p></div>
      <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => setTimeoutOpen(true)}><Clock3 className="size-4" />待接单时限</Button><Button onClick={() => { setQuickReview(false); setQuickOpen(true) }}><Headphones className="size-4" />快捷派单</Button></div>
    </section>

    {scenario === 'refresh-error' && <Alert variant="destructive"><CircleAlert className="size-4" /><AlertTitle>订单更新失败，当前显示上次成功读取的列表</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请核对网络后重试，现有筛选和选择已保留。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}
    {queue === 'staff' && <Alert><Headphones className="size-4" /><AlertTitle>客服待派 · 截止时间从早到晚</AlertTitle><AlertDescription>仅显示已支付且符合待派筛选的客服订单，不代表均可派单；具体资格以订单详情和提交时校验为准。</AlertDescription></Alert>}

    <Card>
      <CardContent className="py-4">
        <div className="mb-4 flex flex-wrap items-center gap-1 border-b pb-3">{([['all', '全部订单'], ['staff', '客服待派 1'], ['aftersale', '处理中售后 1'], ['gift', '礼物单']] as const).map(([value, label]) => <Button key={value} variant={queue === value ? 'secondary' : 'ghost'} size="sm" aria-pressed={queue === value} onClick={() => changeQueue(value)}>{label}</Button>)}</div>
        <form data-admin-filter onSubmit={search} className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(220px,1fr)_minmax(220px,1fr)_auto]">
          <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="按订单号搜索" value={orderInput} onChange={event => setOrderInput(event.target.value)} placeholder="订单号" className="h-11 pl-9" /></div>
          <Input aria-label="按老板账号搜索" value={bossInput} onChange={event => setBossInput(event.target.value)} placeholder="老板账号或昵称" className="h-11" />
          <div className="flex flex-wrap items-center gap-2 md:col-span-2 xl:col-span-1">
            <Button type="submit" className="h-11 min-w-20 whitespace-nowrap">查询</Button>
            <Button type="button" variant="outline" className="h-11 whitespace-nowrap" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen(value => !value)}><Filter className="size-4" />更多筛选{filterCount > 0 && <Badge className="px-1.5">{filterCount}</Badge>}<ChevronDown className="size-3.5" /></Button>
            <Button type="button" variant="outline" className="h-11 min-w-24 whitespace-nowrap" onClick={clearFilters}>清除条件</Button>
          </div>
        </form>
        {(orderInput !== applied.order || bossInput !== applied.boss) && <p role="status" className="mt-2 text-xs text-warning-foreground">筛选输入已更改，列表尚未更新；请点击查询。</p>}
        {advancedOpen && <div data-admin-filter-advanced className="mt-4 grid gap-3 rounded-xl border bg-muted/30 p-4 md:grid-cols-3">
          <div className="space-y-2"><Label>订单状态</Label><Select value={status} onValueChange={setStatus}><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部订单状态</SelectItem>{Object.entries(adminOrderStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
          <div className="space-y-2"><Label>支付状态</Label><Select value={payment} onValueChange={setPayment}><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部支付状态</SelectItem>{Object.entries(adminPaymentStatusLabel).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div>
          <div className="space-y-2"><Label>接单路径</Label><Select value={route} onValueChange={setRoute}><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ALL">全部接单路径</SelectItem><SelectItem value="STAFF">客服派单</SelectItem><SelectItem value="PUBLIC">公开接单</SelectItem></SelectContent></Select></div>
          <p className="text-xs text-muted-foreground md:col-span-3">老板资料按当前账号查询；旧订单可能保留历史资料。服务端日期筛选不在本示例中模拟。</p>
        </div>}
      </CardContent>
    </Card>

    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0">
        <div><CardTitle className="text-base">订单列表</CardTitle><CardDescription className="mt-1">本页 {rows.length} 笔 · 金额已计优惠，未扣退款</CardDescription></div>
        {selected.length > 0 && <div className="flex items-center gap-2 rounded-lg bg-accent px-3 py-2"><span className="text-xs font-medium">已选 {selected.length} 单</span><Button size="sm" variant="outline" onClick={() => { setFeedback(`已核对本页选中的 ${selected.length} 笔示例订单，未执行批量状态变更。`); setSelected([]) }}>完成核对</Button></div>}
      </CardHeader>
      <div className="overflow-x-auto border-t">
        <Table className="min-w-[1080px]">
          <TableHeader><TableRow><TableHead className="w-10"><Checkbox aria-label="选择本页订单" checked={allSelected} onCheckedChange={value => setSelected(value === true ? rows.map(order => order.id) : [])} /></TableHead><TableHead>订单号 / 下单时间</TableHead><TableHead>老板</TableHead><TableHead>服务 / 陪玩</TableHead><TableHead>订单状态</TableHead><TableHead>支付状态</TableHead><TableHead className="text-right">金额</TableHead><TableHead>接单路径</TableHead><TableHead>负责客服</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(order => <TableRow key={order.id} data-state={selected.includes(order.id) ? 'selected' : undefined}>
            <TableCell><Checkbox aria-label={`选择订单 ${order.orderNo}`} checked={selected.includes(order.id)} onCheckedChange={value => setSelected(ids => value === true ? [...ids, order.id] : ids.filter(id => id !== order.id))} /></TableCell>
            <TableCell><button type="button" onClick={() => onOpen(order, detailScenario(order))} className="block font-mono text-xs font-medium text-primary hover:underline">{order.orderNo}</button><span className="mt-1 block whitespace-nowrap text-[11px] text-muted-foreground">{order.createdAt.slice(5)}</span></TableCell>
            <TableCell><span className="block text-xs font-medium">{order.customerName}</span><span className="mt-1 block text-[11px] text-muted-foreground">{order.customerUsername}</span></TableCell>
            <TableCell className="max-w-56"><span className="block truncate text-xs">{order.serviceName}</span><span className="mt-1 block truncate text-[11px] text-muted-foreground">{order.providerNames.length ? order.providerNames.join(' / ') : '等待安排'}{order.teamSize === 2 ? ' · 双陪' : ''}</span></TableCell>
            <TableCell><StatusBadge order={order} />{order.aftersale && <span className="mt-1 block text-[10px] text-warning-foreground">售后处理中</span>}</TableCell>
            <TableCell><Badge variant="outline" className={cn('whitespace-nowrap', order.paymentStatus === 'PAID' && 'border-success/30 text-success', order.paymentStatus.includes('REFUND') && 'border-warning/30 text-warning-foreground')}>{adminPaymentStatusLabel[order.paymentStatus]}</Badge></TableCell>
            <TableCell className="whitespace-nowrap text-right text-xs font-medium tabular-nums">{formatWaveAmount(order.amount)} 浪花{order.refundedAmount ? <span className="mt-1 block text-[10px] font-normal text-muted-foreground">已退 {formatWaveAmount(order.refundedAmount)}</span> : null}</TableCell>
            <TableCell><span className="block whitespace-nowrap text-xs">{order.route === 'STAFF' ? '客服派单' : '公开接单'}</span>{order.deadline && order.status === 'PENDING' && <span className="mt-1 block whitespace-nowrap text-[10px] text-warning-foreground">截止 {order.deadline.slice(5)}</span>}</TableCell>
            <TableCell className="whitespace-nowrap text-xs">{order.support}</TableCell>
            <TableCell><div className="flex min-h-11 items-center justify-end gap-1"><Button variant="ghost" size="sm" onClick={() => onOpen(order, detailScenario(order))}>详情<ChevronRight className="size-3.5" /></Button>{order.status === 'PENDING' && order.paymentStatus === 'PAID' && <Button variant="outline" size="sm" onClick={() => onOpen(order, 'pending')}>派单</Button>}{order.status === 'PENDING' && order.paymentStatus === 'UNPAID' && <span className="text-[10px] text-muted-foreground">支付后可派</span>}</div></TableCell>
          </TableRow>)}</TableBody>
        </Table>
        {rows.length === 0 && <div className="flex min-h-72 flex-col items-center justify-center p-8 text-center"><span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground"><ClipboardList className="size-5" /></span><h2 className="mt-4 text-base font-semibold">没有匹配的订单</h2><p className="mt-2 text-sm text-muted-foreground">调整关键词或清除筛选后再查看。</p><Button variant="outline" className="mt-5" onClick={clearFilters}>清除全部条件</Button></div>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>订单统计与操作均为原型演示，不连接真实资金或派单。</span><span>第 1 页 / 共 1 页</span></div>
    </Card>

    <Dialog open={quickOpen} onOpenChange={setQuickOpen}>
      <DialogContent className="sm:max-w-2xl"><DialogHeader><DialogTitle>客服快捷派单</DialogTitle><DialogDescription>核对老板、服务、陪玩和预计扣款后派单。</DialogDescription></DialogHeader>
        {!quickReview ? <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2"><Label>下单老板</Label><Select defaultValue="boss_sea"><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="boss_sea">海边听风 · 余额 860 浪花</SelectItem><SelectItem value="coffee_daily">一杯美式 · 余额 420 浪花</SelectItem></SelectContent></Select></div>
          <div className="space-y-2"><Label>服务</Label><Select defaultValue="lol"><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="lol">英雄联盟 · 双排 2 局</SelectItem><SelectItem value="valorant">无畏契约 · 体验 1 局</SelectItem></SelectContent></Select></div>
          <div className="space-y-2"><Label>指派陪玩</Label><Select defaultValue="deer"><SelectTrigger className="h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="deer">小鹿 · 资格已核验</SelectItem><SelectItem value="nana">奈奈 · 资格已核验</SelectItem></SelectContent></Select></div>
          <div className="space-y-2"><Label>局数</Label><Input type="number" min="1" defaultValue="2" className="h-11" /></div>
          <div className="rounded-xl border bg-muted/40 p-4 md:col-span-2"><div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">老板余额</span><strong>860 浪花</strong></div><div className="mt-3 flex items-center justify-between text-sm"><span className="text-muted-foreground">折后预计扣款</span><strong className="text-primary">136 浪花</strong></div><p className="mt-3 text-xs leading-5 text-muted-foreground">金额来自示例试算，已考虑适用折扣；这不是平台利润，提交时仍需重新核对。</p></div>
        </div> : <div className="space-y-4"><Alert><CheckCircle2 className="size-4" /><AlertTitle>派单信息已核对</AlertTitle><AlertDescription>海边听风 · 英雄联盟双排 2 局 · 小鹿 · 预计扣款 136 浪花。</AlertDescription></Alert><div className="rounded-xl border p-4 text-sm leading-7"><p>老板余额：860 浪花</p><p>陪玩预计收益：109 浪花</p><p>店铺留存快照：27 浪花</p><p className="text-xs text-muted-foreground">确认仅生成原型成功反馈，不创建真实订单或扣款。</p></div></div>}
        <DialogFooter><Button variant="outline" onClick={() => quickReview ? setQuickReview(false) : setQuickOpen(false)}>{quickReview ? '返回修改' : '取消'}</Button><Button onClick={() => { if (!quickReview) setQuickReview(true); else { setQuickOpen(false); setFeedback('快捷派单原型流程已完成：未创建真实订单，也未产生扣款。') } }}>{quickReview ? '确认派单' : '核对派单'}<ArrowRight className="size-4" /></Button></DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={timeoutOpen} onOpenChange={setTimeoutOpen}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>待接单时限设置</DialogTitle><DialogDescription>设置已支付待接单订单的处理时限。</DialogDescription></DialogHeader><div className="space-y-2"><Label htmlFor="order-deadline">处理时限（分钟）</Label><Input id="order-deadline" type="number" min="1" value={deadline} onChange={event => setDeadline(event.target.value)} className="h-11" /><p className="text-xs text-muted-foreground">到期后不能继续派单，需核对系统处理结果。</p></div><DialogFooter><Button variant="outline" onClick={() => setTimeoutOpen(false)}>取消</Button><Button disabled={!Number(deadline)} onClick={() => { setTimeoutOpen(false); setFeedback(`待接单时限已在原型中更新为 ${deadline} 分钟，未修改真实配置。`) }}><Settings2 className="size-4" />保存设置</Button></DialogFooter></DialogContent></Dialog>

    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
