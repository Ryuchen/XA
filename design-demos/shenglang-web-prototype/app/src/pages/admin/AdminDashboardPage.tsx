import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import {
  ArrowRight,
  Banknote,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Clock3,
  Headphones,
  Info,
  RotateCcw,
  UsersRound,
  WalletCards,
  X,
} from 'lucide-react'
import type { AdminDashboardScenario } from '@/components/admin/admin-config'
import { useStore } from '@/components/store/store-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'
import { formatWaveAmount } from '@/lib/format'

interface AdminDashboardPageProps {
  scenario: AdminDashboardScenario
  onScenarioChange: (scenario: AdminDashboardScenario) => void
  onOrders: (queue?: 'all' | 'staff' | 'aftersale') => void
  onOpenOrder: (orderNo: string) => void
}

const trend = [
  { date: '10-29', count: 22, net: 7820 },
  { date: '10-30', count: 28, net: 9360 },
  { date: '10-31', count: 25, net: 8840 },
  { date: '11-01', count: 34, net: 12280 },
  { date: '11-02', count: 31, net: 10960 },
  { date: '11-03', count: 39, net: 14320 },
  { date: '11-04', count: 36, net: 12860 },
]

const chartConfig = { count: { label: '新增订单', color: 'var(--primary)' } } satisfies ChartConfig

const todos = [
  {
    id: 'finance', title: '财务', icon: WalletCards,
    items: [
      { label: '待审核提现', note: '未领取 4 · 我已领取 2', count: 6, unit: '笔' },
      { label: '待审核报单', note: '含订单状态待核对记录', count: 4, unit: '条' },
      { label: '待结算平台补贴', note: '1,860 浪花 · 或在售后期', count: 9, unit: '单' },
      { label: '其他财务复核', note: '当前没有待处理记录', count: 0, unit: '条' },
    ],
  },
  {
    id: 'support', title: '客服', icon: Headphones,
    items: [
      { label: '已支付待接单', note: '客服派单 5 · 公开接单 3', count: 8, unit: '单' },
      { label: '待确认预约', note: '等待陪玩或客服确认', count: 5, unit: '条' },
      { label: '未读客服会话', note: '优先处理进行中订单咨询', count: 3, unit: '条' },
      { label: '已到接单期限', note: '等待系统处理，请核对', count: 0, unit: '单' },
    ],
  },
  {
    id: 'aftersale', title: '售后', icon: ClipboardList,
    items: [
      { label: '处理中售后', note: '查看相关订单与申请说明', count: 2, unit: '单' },
      { label: '未回复评价', note: '陪玩或客服可回复', count: 7, unit: '条' },
      { label: '待处理权益', note: '核对资料并安排交付', count: 4, unit: '条' },
      { label: '待复核 TAG', note: '已发放后暂停或待补审', count: 0, unit: '条' },
    ],
  },
]

const recentOrders = [
  { no: 'SL202611040036', customer: '海边听风', provider: '小鹿', service: '英雄联盟 · 双排 2 局', amount: '136', status: '待接单', pay: '已支付', time: '11-04 16:42' },
  { no: 'SL202611040035', customer: '北纬二十七', provider: '平台安排', service: '无畏契约 · 体验 1 局', amount: '68', status: '待支付', pay: '未支付', time: '11-04 16:35' },
  { no: 'SL202611040034', customer: '一杯美式', provider: '阿澈 / 可可', service: '王者荣耀 · 双陪 2 局', amount: '216', status: '服务中', pay: '已支付', time: '11-04 16:18' },
  { no: 'SL202611040033', customer: '星河入梦', provider: '奈奈', service: '礼物单 · 星光岛屿', amount: '52', status: '已完成', pay: '已支付', time: '11-04 15:56' },
]

const statusRows = [
  { label: '已完成', count: 486, pct: 72, tone: 'success' },
  { label: '服务中', count: 12, pct: 2, tone: 'primary' },
  { label: '待接单', count: 18, pct: 3, tone: 'warning' },
  { label: '已取消', count: 94, pct: 14, tone: 'muted' },
  { label: '其他状态', count: 62, pct: 9, tone: 'muted' },
]

function DashboardSkeleton() {
  return <div className="space-y-4" aria-label="工作台数据加载中"><div className="grid gap-3 md:grid-cols-3">{[0, 1, 2].map(item => <Skeleton key={item} className="h-56 rounded-xl" />)}</div><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map(item => <Skeleton key={item} className="h-36 rounded-xl" />)}</div><div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]"><Skeleton className="h-80 rounded-xl" /><Skeleton className="h-80 rounded-xl" /></div><Skeleton className="h-72 rounded-xl" /></div>
}

export function AdminDashboardPage({ scenario, onScenarioChange, onOrders, onOpenOrder }: AdminDashboardPageProps) {
  const { store } = useStore()
  const [pendingOnly, setPendingOnly] = useState(true)
  const [feedback, setFeedback] = useState('')
  const loading = scenario === 'loading'
  const firstError = scenario === 'error'
  const noTodos = scenario === 'no-todos'
  const role = scenario === 'support' ? 'support' : scenario === 'finance' ? 'finance' : 'admin'

  const metrics = role === 'support' ? [
    { label: '已支付待接单', value: store.metrics.waiting, unit: '单', note: '不代表均可派单', icon: ClipboardList, featured: true },
    { label: '未支付待接单', value: '5', unit: '单', note: '支付完成后才可派单', icon: Clock3 },
    { label: '服务中', value: store.metrics.inService, unit: '单', note: '当前处于服务中状态', icon: Headphones },
    { label: '待审核报单', value: '4', unit: '条', note: '包含订单状态暂不能通过的记录', icon: CheckCircle2 },
  ] : role === 'finance' ? [
    { label: '待审核提现', value: '6', unit: '笔', note: '未领取 4 · 我已领取 2', icon: WalletCards, featured: true },
    { label: '今日订单退款', value: '386', unit: '浪花', note: '3 单 / 4 次 · 按退款发生时间', icon: Banknote },
    { label: '平台补贴净支出', value: '720', unit: '浪花', note: '已支付 980 · 已追回 260 · 非利润', icon: Banknote },
    { label: '待结算平台补贴', value: '9', unit: '单', note: '1,860 浪花 · 可能等待审核或售后期', icon: Clock3 },
  ] : [
    { label: '今日新增订单', value: store.metrics.todayOrders, unit: '单', note: '仅当前店 · 含未支付及取消', icon: ClipboardList, featured: true },
    { label: '已支付待接单', value: store.metrics.waiting, unit: '单', note: '仅当前店客服与公开接单队列', icon: Clock3 },
    { label: '服务中', value: store.metrics.inService, unit: '单', note: '当前店履约中的服务订单', icon: Headphones },
    { label: '累计订单净额', value: store.metrics.net, unit: '浪花', note: '仅当前店 · 已扣退款 · 非平台利润', icon: Banknote },
  ]

  function showQueue(label: string) {
    if (label.startsWith('SL2026')) { onOpenOrder(label); return }
    if (label === '客服待派' || label === '已支付待接单') { onOrders('staff'); return }
    if (label === '处理中售后') { onOrders('aftersale'); return }
    if (['全部订单', '今日新增订单', '未支付待接单', '服务中', '已完成', '待接单', '已取消', '其他状态', '累计订单净额', '今日订单退款'].includes(label) || label.endsWith('新增订单')) { onOrders('all'); return }
    setFeedback(`已记录「${label}」筛选。对应业务列表将在该模块页面中继续展开，本次操作未读取真实数据。`)
  }

  if (loading) return <DashboardSkeleton />
  if (firstError) return <Card className="mx-auto mt-16 max-w-xl py-8 text-center"><CardContent className="flex flex-col items-center"><span className="grid size-12 place-items-center rounded-full bg-destructive/10 text-destructive"><CircleAlert className="size-6" /></span><CardTitle className="mt-5 text-xl">工作台数据加载失败</CardTitle><CardDescription className="mt-2 max-w-sm leading-6">请检查网络和访问权限后重试。当前没有可用快照，不会展示推测数据。</CardDescription><Button className="mt-6" onClick={() => onScenarioChange('default')}><RotateCcw className="size-4" />重新加载</Button></CardContent></Card>

  return <div className="mx-auto flex max-w-[1600px] flex-col gap-4">
    <section className="flex flex-wrap items-start gap-3">
      <div className="min-w-[260px] flex-1"><div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-[-0.03em]">运营工作台</h1><Badge variant="outline" className="border-primary/25 bg-primary/5 text-primary">{store.shortName}</Badge><Badge variant="outline" className="border-warning/40 bg-warning/10 text-xs">示例快照</Badge></div><p className="mt-1 text-sm text-muted-foreground">仅查看{store.name}待办和经营数据，不汇总其他门店。</p><p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground"><Clock3 className="size-3.5" />开始读取：2026-11-04 16:48 · Asia/Shanghai</p></div>
      <Button onClick={() => showQueue('客服待派')}><Headphones className="size-4" />客服待派<Badge variant="secondary" className="ml-1 px-1.5">{store.metrics.waiting}</Badge></Button>
    </section>

    {scenario === 'refresh-error' && <Alert variant="destructive" role="alert"><CircleAlert className="size-4" /><AlertTitle>刷新失败，当前显示上次成功获取的数据</AlertTitle><AlertDescription className="flex flex-wrap items-center gap-2">请检查网络和访问权限后重试，现有快照未被清空。<Button variant="link" className="h-auto p-0" onClick={() => onScenarioChange('default')}>重新加载</Button></AlertDescription></Alert>}

    <Card className="overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0 border-b pb-4">
        <div><CardTitle className="text-base">待处理事项</CardTitle><CardDescription className="mt-1">数量为读取时快照，操作前请核对最新状态</CardDescription></div>
        <div className="flex items-center gap-3"><span className="hidden items-center gap-1 text-xs text-muted-foreground xl:flex"><Info className="size-3.5" />各队列可能包含同一权益记录，不可直接相加</span><label htmlFor="admin-pending-only" className="flex min-h-11 items-center gap-2 text-sm">仅看有待办<Switch id="admin-pending-only" checked={pendingOnly} onCheckedChange={setPendingOnly} /></label></div>
      </CardHeader>
      <div className="grid divide-y md:grid-cols-3 md:divide-x md:divide-y-0">
        {todos.map(group => {
          const visibleItems = group.items.filter(item => !pendingOnly && !noTodos || !noTodos && item.count > 0)
          return <section key={group.id} className="min-w-0 p-4">
            <div className="mb-3 flex items-center justify-between"><h2 className="flex items-center gap-2 text-sm font-semibold"><span className="grid size-8 place-items-center rounded-lg border bg-muted/50"><group.icon className="size-4 text-muted-foreground" /></span>{group.title}</h2><Badge variant={noTodos ? 'secondary' : 'outline'} className={cn('text-[11px]', !noTodos && 'border-primary/20 bg-primary/5 text-primary')}>{noTodos ? '暂无待办' : '有待办'}</Badge></div>
            <div className="space-y-2">
              {visibleItems.map(item => <button key={item.label} type="button" onClick={() => showQueue(item.label)} className="group flex min-h-[60px] w-full items-center gap-2 rounded-lg border bg-card px-3 py-2 text-left transition-colors hover:border-primary/40 hover:bg-accent/50"><span className="min-w-0 flex-1"><span className="block text-[13px] font-medium">{item.label}</span><span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{item.note}</span></span><strong className={cn('whitespace-nowrap text-base tabular-nums', item.count > 0 && !noTodos ? 'text-primary' : 'text-muted-foreground')}>{noTodos ? 0 : item.count}<small className="ml-1 text-[10px] font-normal">{item.unit}</small></strong><ChevronRight className="size-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></button>)}
              {visibleItems.length === 0 && <div className="flex min-h-[172px] flex-col items-center justify-center rounded-lg border border-dashed text-center"><span className="grid size-8 place-items-center rounded-full bg-success/10 text-success"><Check className="size-4" /></span><p className="mt-3 text-sm font-medium">暂无待处理事项</p><p className="mt-1 text-xs text-muted-foreground">当前队列已处理完毕</p></div>}
            </div>
          </section>
        })}
      </div>
      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">各队列独立展示，数量不可直接相加</div>
    </Card>

    <section aria-label="经营指标">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold">经营指标</h2><p className="mt-1 text-xs text-muted-foreground">按工作关注点查看，数据以当前快照为准</p></div><div className="flex rounded-lg border bg-card p-1">{([['admin', '管理员'], ['support', '客服'], ['finance', '财务']] as const).map(([value, label]) => <Button key={value} variant={role === value ? 'secondary' : 'ghost'} size="sm" aria-pressed={role === value} onClick={() => onScenarioChange(value === 'admin' ? 'default' : value)} className="h-8 px-3 text-xs">{label}视角</Button>)}</div></div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{metrics.map(metric => <Card key={metric.label} className={cn('min-w-0 gap-3 py-4', metric.featured && 'border-[#10285b] bg-[#10285b] text-white')}><CardContent className="flex h-full flex-col"><div className="flex items-center justify-between gap-2"><span className={cn('text-xs font-medium', metric.featured ? 'text-blue-100' : 'text-muted-foreground')}>{metric.label}</span><metric.icon className={cn('size-4', metric.featured ? 'text-sky-300' : 'text-muted-foreground')} /></div><p className="mt-4 whitespace-nowrap text-[26px] font-semibold leading-8 tracking-[-0.03em] tabular-nums">{metric.value}<small className={cn('ml-1.5 text-xs font-normal', metric.featured ? 'text-blue-100' : 'text-muted-foreground')}>{metric.unit}</small></p><p className={cn('mt-2 text-[11px] leading-5', metric.featured ? 'text-blue-100/75' : 'text-muted-foreground')}>{metric.note}</p><button type="button" onClick={() => showQueue(metric.label)} className={cn('mt-auto flex min-h-8 items-center gap-1 self-start pt-2 text-xs font-medium', metric.featured ? 'text-sky-200' : 'text-primary')}>查看明细<ArrowRight className="size-3" /></button></CardContent></Card>)}</div>
      {role === 'admin' && <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-dashed bg-card/60 px-4 py-3 text-xs text-muted-foreground"><span className="flex items-center gap-1.5 font-medium text-foreground"><Info className="size-3.5" />盈利与投入 · 待接入</span><span>净利润 —</span><span>毛利润 —</span><span>人力成本 —</span><span>项目 / 渠道 ROI —</span><span>不从订单净额或补贴支出推算利润</span></div>}
    </section>

    <Card><CardContent className="flex flex-wrap items-center gap-4 py-4"><div className="min-w-40 flex-1"><h2 className="flex items-center gap-2 text-base font-semibold"><UsersRound className="size-4 text-primary" />账号规模</h2><p className="mt-1 text-xs text-muted-foreground">仅{store.shortName}账号数量</p></div><dl className="grid min-w-[320px] flex-[3] grid-cols-3 gap-4">{[['全部账号', store.metrics.accounts], ['老板', store.metrics.bosses], ['陪玩', store.metrics.providers]].map(([label, value]) => <div key={label} className="border-l pl-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{value}</dd></div>)}</dl></CardContent></Card>

    <div className="grid min-w-0 gap-4 xl:grid-cols-[1.45fr_1fr]">
      <Card className="min-w-0"><CardHeader><CardTitle className="text-base">近七日下单趋势</CardTitle><CardDescription>订单数含未支付和取消 · 净额已扣当前退款，非当日收入</CardDescription></CardHeader><CardContent><ChartContainer config={chartConfig} className="h-[220px] w-full"><BarChart data={trend} margin={{ top: 12, right: 8, left: -16, bottom: 0 }}><CartesianGrid vertical={false} strokeDasharray="4 4" /><XAxis dataKey="date" tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Bar dataKey="count" fill="var(--color-count)" radius={[5, 5, 0, 0]} maxBarSize={34} /></BarChart></ChartContainer><div className="mt-2 grid grid-cols-7 gap-1">{trend.map(day => <button key={day.date} type="button" onClick={() => showQueue(`${day.date} 新增订单`)} className="min-w-0 rounded-md border px-1 py-2 text-center hover:border-primary/40"><span className="block text-[10px] text-muted-foreground">{day.date}</span><strong className="mt-1 block text-xs">{day.count} 单</strong><span className="mt-0.5 block truncate text-[9px] text-muted-foreground">{formatWaveAmount(day.net)} 浪花</span></button>)}</div></CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">当前订单状态</CardTitle><CardDescription>全部历史订单的当前状态快照</CardDescription></CardHeader><CardContent className="space-y-4">{statusRows.map(row => <button key={row.label} type="button" onClick={() => showQueue(row.label)} className="grid min-h-8 w-full grid-cols-[64px_minmax(60px,1fr)_36px_36px] items-center gap-2 text-left"><Badge variant="secondary" className={cn('justify-center text-[10px]', row.tone === 'success' && 'bg-success/10 text-success', row.tone === 'warning' && 'bg-warning/10 text-warning-foreground', row.tone === 'primary' && 'bg-primary/10 text-primary')}>{row.label}</Badge><span className="h-2 overflow-hidden rounded-full bg-muted"><span className={cn('block h-full rounded-full bg-primary', row.tone === 'muted' && 'bg-muted-foreground/40', row.tone === 'success' && 'bg-success', row.tone === 'warning' && 'bg-warning')} style={{ width: `${Math.max(row.pct, 3)}%` }} /></span><strong className="text-right text-xs tabular-nums">{row.count}</strong><span className="text-right text-[10px] text-muted-foreground">{row.pct}%</span></button>)}</CardContent></Card>
    </div>

    <Card className="min-w-0 overflow-hidden"><CardHeader className="flex-row items-center justify-between space-y-0"><div><CardTitle className="text-base">最近创建的订单</CardTitle><CardDescription className="mt-1">最近 4 笔 · 订单金额已计优惠，未扣退款</CardDescription></div><Button variant="outline" size="sm" onClick={() => showQueue('全部订单')}>查看全部<ChevronRight className="size-3.5" /></Button></CardHeader><div className="overflow-x-auto border-t"><Table className="min-w-[900px]"><TableHeader><TableRow><TableHead>订单号</TableHead><TableHead>老板</TableHead><TableHead>陪玩</TableHead><TableHead>服务</TableHead><TableHead className="text-right">订单金额</TableHead><TableHead>订单状态</TableHead><TableHead>支付状态</TableHead><TableHead>下单时间</TableHead></TableRow></TableHeader><TableBody>{recentOrders.map(order => <TableRow key={order.no}><TableCell><button type="button" onClick={() => showQueue(order.no)} className="font-mono text-xs text-primary hover:underline">{order.no}</button></TableCell><TableCell>{order.customer}</TableCell><TableCell>{order.provider}</TableCell><TableCell className="max-w-52 truncate">{order.service}</TableCell><TableCell className="whitespace-nowrap text-right font-medium tabular-nums">{formatWaveAmount(order.amount)} 浪花</TableCell><TableCell><Badge variant="secondary" className={cn(order.status === '服务中' && 'bg-primary/10 text-primary', order.status === '已完成' && 'bg-success/10 text-success', order.status === '待接单' && 'bg-warning/10 text-warning-foreground')}>{order.status}</Badge></TableCell><TableCell><Badge variant="outline" className={cn(order.pay === '已支付' && 'border-success/30 text-success')}>{order.pay}</Badge></TableCell><TableCell className="whitespace-nowrap text-muted-foreground">{order.time}</TableCell></TableRow>)}</TableBody></Table></div></Card>

    <p className="pb-2 text-center text-[11px] text-muted-foreground">以上为高保真示例快照，不代表真实经营结果，也未连接生产数据。</p>
    {feedback && <div role="status" className="fixed bottom-5 right-5 z-50 flex max-w-md items-start gap-3 rounded-xl border bg-card p-4 text-sm shadow-2xl"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /><span className="leading-6">{feedback}</span><Button variant="ghost" size="icon" className="-mr-2 -mt-2 size-8" onClick={() => setFeedback('')} aria-label="关闭反馈"><X className="size-4" /></Button></div>}
  </div>
}
