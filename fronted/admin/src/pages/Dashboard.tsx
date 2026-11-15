import { DataValue, DataRegion } from "@/components/page-data-state"
import { coinText, coinUnit, useCurrentStore } from "@xa/shared"
import { useState } from "react"
import { useNavigate } from "react-router"
import {
  ArrowUpRight,
  ArrowRight,
  Medal,
  Check,
  BanknoteArrowDown, CircleMinus, ClipboardCheck, ClipboardPlus, CreditCard,
  Gamepad2, Gift, HandCoins, Hourglass, LockKeyhole, ReceiptText,
  RotateCcw, ShieldCheck, Wallet, type LucideIcon,
  UsersRound,
  Banknote,
  ChevronRight,
  Clock,
  Headphones,
} from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { BarChart, Bar, CartesianGrid, XAxis, YAxis } from "recharts"
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table"
import {
  DataPageState,
  DataTable,
  Feedback,
  PageHeader,
  RefreshButton,
  Status,
  useLiveReload,
  useLoad,
  type Row,
} from "@/components/business"
import { dashboardApi, playerDashboardApi } from "@/api/modules"
import { useAdminAuth } from "@/lib/auth"
import {
  amountToXaCoin,
  formatDateTime,
  numberText,
  providerNames,
} from "@/lib/format"
import { GENDER, ORDER_STATUS } from "@/lib/dict"

const money = (value: unknown) => value == null ? "—" : amountToXaCoin(value as number)

type Queue = {
  label: string
  field: string
  path: string
  permission: string
  detail?: (data: Row) => string
}
const queues: Array<{ title: string; icon: typeof Banknote; items: Queue[] }> =
  [
    {
      title: "财务",
      icon: Banknote,
      items: [
        {
          label: "待审核提现",
          field: "pending_withdraw_total",
          path: "/withdrawals?status=PENDING",
          permission: "withdraw:view",
          detail: (data) =>
            `未领取 ${numberText(data.pending_withdraw_unclaimed)} · 我已领取 ${numberText(data.pending_withdraw_mine)}`,
        },
        {
          label: "待审核报单",
          field: "pending_report_total",
          path: "/reports?status=PENDING&exclude_direct_test=1",
          permission: "report:view",
          detail: (data) =>
            `订单状态待核对 ${numberText(data.pending_report_order_blocked_total)}`,
        },
        {
          label: "待结算平台补贴",
          field: "pending_subsidy_total",
          path: "/wallets",
          permission: "wallet:view",
          detail: (data) =>
            `${money(data.pending_subsidy_amount)} ${coinUnit()} · 可能仍在售后期`,
        },
      ],
    },
    {
      title: "客服",
      icon: Headphones,
      items: [
        {
          label: "已支付待接单",
          field: "pending_paid_total",
          path: "/orders?queue=paid_pending&exclude_direct_test=1",
          permission: "order:view",
          detail: (data) =>
            `客服派单 ${numberText(data.pending_paid_routes?.staff)} · 公开接单 ${numberText(data.pending_paid_routes?.public)}`,
        },
        {
          label: "待确认预约",
          field: "pending_reservation_total",
          path: "/reservations?status=pending",
          permission: "order:view",
        },
        {
          label: "已确认待转单",
          field: "confirmed_reservation_total",
          path: "/reservations?status=confirmed",
          permission: "order:view",
        },
        {
          label: "未读客服会话",
          field: "unread_chat_session_total",
          path: "/chat?only_unread=1",
          permission: "chat:view",
        },
      ],
    },
    {
      title: "售后",
      icon: Clock,
      items: [
        {
          label: "处理中售后",
          field: "open_aftersale_order_total",
          path: "/orders?aftersale_status=OPEN&exclude_direct_test=1",
          permission: "order:view",
        },
        {
          label: "未回复评价",
          field: "unreplied_evaluation_total",
          path: "/evaluations?has_reply=0",
          permission: "evaluation:view",
        },
        {
          label: "待处理权益",
          field: "pending_claim_total",
          path: "/club-claims?status=PENDING",
          permission: "engagement:fulfill",
        },
        {
          label: "交付中权益",
          field: "fulfilling_claim_total",
          path: "/club-claims?status=FULFILLING",
          permission: "engagement:fulfill",
        },
      ],
    },
  ]
export default function DashboardPage() {
  const store = useCurrentStore()
  const { hasPerm, profile } = useAdminAuth()
  const navigate = useNavigate()
  const result = useLoad(async () => (await dashboardApi.stats(true)).data)
  const [pendingOnly, setPendingOnly] = useState(true)
  const [view, setView] = useState<"all" | "support" | "finance">("all")
  useLiveReload(result.reload)
  const data = result.data || {}
  const recentOrders = (data.recent_orders || []).slice(0, 4)
  const today = data.daily_new_orders?.at(-1)?.date
  const roles = (profile?.roles || []).map((role: Row) => role.code)
  const supportView =
    profile?.is_superuser ||
    roles.includes("support") ||
    roles.includes("super_admin")
  const financeView =
    profile?.is_superuser ||
    roles.includes("finance") ||
    roles.includes("super_admin")
  const paid = { label: "已支付待接单", icon: CreditCard, value: numberText(data.pending_paid_total), unit: "单", note: "客服与公开接单队列", path: "/orders?queue=paid_pending&exclude_direct_test=1", permission: "order:view" }
  const unpaid = { label: "未支付待接单", icon: Clock, value: numberText(data.pending_unpaid_total), unit: "单", note: "支付完成后才可派单", path: "/orders?queue=unpaid_pending&exclude_direct_test=1", permission: "order:view" }
  const inService = { label: "服务中", icon: Headphones, value: numberText(data.order_status?.IN_SERVICE ?? (data.order_status ? 0 : null)), unit: "单", note: "当前履约中的服务订单", path: "/orders?order_status=IN_SERVICE&exclude_direct_test=1", permission: "order:view" }
  const metricGroups = {
    all: [
      { label: "今日新增订单", icon: ClipboardPlus, value: numberText(data.today_order_total), unit: "单", note: `服务 ${numberText(data.today_service_order_total)} · 礼物 ${numberText(data.today_gift_order_total)} · 含未支付及取消`, path: today ? `/orders?created_day=${today}&exclude_direct_test=1` : "", permission: "order:view" },
      paid, inService,
      { label: "累计订单净额", icon: ReceiptText, value: money(data.order_amount_total), unit: coinUnit(), note: "已扣退款 · 非平台利润", path: "/orders?exclude_direct_test=1", permission: "order:view" },
    ],
    support: [paid, unpaid, inService, { label: "待审核报单", icon: ClipboardCheck, value: numberText(data.pending_report_total), unit: "条", note: "包含订单状态待核对记录", path: "/reports?status=PENDING&exclude_direct_test=1", permission: "report:view" }],
    finance: [
      { label: "待审核提现", icon: Wallet, value: numberText(data.pending_withdraw_total), unit: "笔", note: `未领取 ${numberText(data.pending_withdraw_unclaimed)} · 我已领取 ${numberText(data.pending_withdraw_mine)}`, path: "/withdrawals?status=PENDING", permission: "withdraw:view" },
      { label: "今日订单退款", icon: RotateCcw, value: money(data.today_refund_amount), unit: coinUnit(), note: `${numberText(data.today_refund_orders)} 单 / ${numberText(data.today_refund_operations)} 次 · 退回老板余额`, path: today ? `/orders?refund_day=${today}&exclude_direct_test=1` : "", permission: "order:view" },
      { label: "平台补贴净支出", icon: HandCoins, value: Number.isFinite(data.today_subsidy_paid) && Number.isFinite(data.today_subsidy_recovered) ? amountToXaCoin(data.today_subsidy_paid - data.today_subsidy_recovered) : "—", unit: coinUnit(), note: "已支付减已追回 · 非平台利润", path: "/wallets", permission: "wallet:view" },
      { label: "待结算平台补贴", icon: Hourglass, value: numberText(data.pending_subsidy_total), unit: "单", note: `${money(data.pending_subsidy_amount)} ${coinUnit()} · 等待审核或售后期`, path: "/wallets", permission: "wallet:view" },
    ],
  }
  const metrics = metricGroups[view]
  const days = data.daily_new_orders || []
  const statusTotal = Object.values(data.order_status || {}).reduce<number>(
    (sum, value) => sum + Number(value || 0),
    0
  )
  return <DataPageState loading={result.loading} hasSnapshot={!!result.data} error={result.error} retry={result.reload} skeleton="dashboard">
    <div className="admin-dashboard mx-auto flex max-w-[1600px] flex-col gap-4">
      <PageHeader
        title="运营工作台"
        tag={store.name}
        description={`查看本店待办事项和运营数据 · 开始读取：${formatDateTime(data.snapshot_at)}（北京时间）`}
      >
        {hasPerm("roi:view") && <Button variant="outline" onClick={() => navigate("/operating-roi")}>经营 ROI<ArrowUpRight className="size-4" /></Button>}
        <RefreshButton loading={result.loading} reload={result.reload} />
        {hasPerm("order:view") && <Button onClick={() => navigate("/orders?queue=paid_pending&exclude_direct_test=1")}><Headphones className="size-4" />客服待派<Badge variant="secondary" className="ml-1 px-2">{numberText(data.pending_paid_total)}</Badge></Button>}
      </PageHeader>
      <Feedback variant="inline" message={result.error} retry={result.reload} />
      {<>
        <Card className="overflow-hidden">
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-3 space-y-0 border-b pb-4"><div><CardTitle className="text-base">待处理事项</CardTitle><CardDescription className="mt-1">操作前请核对最新状态</CardDescription></div><label htmlFor="admin-pending-only" className="flex min-h-11 items-center gap-2 text-sm">仅看有待办<Switch id="admin-pending-only" checked={pendingOnly} onCheckedChange={setPendingOnly} /></label></CardHeader>
          <div className="grid grid-cols-3 divide-x">{queues.map(group => {
            const permitted = group.items.filter(item => hasPerm(item.permission)), known = permitted.filter(item => typeof data[item.field] === "number"), items = known.filter(item => !pendingOnly || data[item.field] > 0)
            return <section key={group.title} className="min-w-0 p-4"><div className="mb-3 flex items-center justify-between"><h2 className="flex items-center gap-2 text-sm font-semibold"><span className="grid size-8 place-items-center rounded-lg border bg-muted/50"><group.icon className="size-4 text-muted-foreground" /></span>{group.title}</h2><Badge variant="outline" className={items.length ? "border-primary/20 bg-primary/5 text-primary" : "text-muted-foreground"}>{items.length ? "有待办" : !permitted.length ? "暂无权限" : known.length ? "暂无待办" : "待核对"}</Badge></div>
              <div className="space-y-2">{items.map(item => <button type="button" key={item.field} className="group flex min-h-[60px] w-full items-center gap-2 rounded-lg border bg-card px-3 py-2 text-left transition-colors hover:border-primary/40 hover:bg-accent/50" onClick={() => navigate(item.path)}><span className="min-w-0 flex-1"><span className="block text-[13px] font-medium">{item.label}</span>{item.detail && <span className="mt-1 block truncate text-xs text-muted-foreground" title={item.detail(data)}>{item.detail(data)}</span>}</span><strong className={"whitespace-nowrap text-base tabular-nums " + (data[item.field] > 0 ? "text-primary" : "text-muted-foreground")}>{numberText(data[item.field])}<small className="ml-1 text-xs font-normal">{item.field.includes("withdraw") ? "笔" : /report|claim|evaluation|chat/.test(item.field) ? "条" : "单"}</small></strong><ChevronRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></button>)}</div>
              {!items.length && <div className="flex min-h-[172px] flex-col items-center justify-center rounded-lg border border-dashed text-center"><span className="grid size-8 place-items-center rounded-full bg-muted text-muted-foreground"><Check className="size-4" /></span><p className="mt-3 text-sm font-medium">{!permitted.length ? "暂无查看权限" : known.length ? "暂无待处理事项" : "待办暂未获取"}</p><p className="mt-1 text-xs text-muted-foreground">{known.length ? "可关闭筛选查看全部队列" : "请刷新数据核对"}</p></div>}
            </section>
          })}</div>
          <div className="border-t px-4 py-2 text-xs text-muted-foreground">各队列独立展示，数量不可直接相加</div>
        </Card>
        <section aria-label="经营指标"><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold">经营指标</h2><p className="mt-1 text-xs text-muted-foreground">订单统计不含直接支付测试单</p></div><div className="flex rounded-lg border bg-card p-1">{([["all","管理员视角"],...(supportView ? [["support","客服视角"]] : []),...(financeView ? [["finance","财务视角"]] : [])] as Array<[typeof view,string]>).map(([value,label]) => <Button key={value} variant={view === value ? "secondary" : "ghost"} size="sm" className="h-8 px-3 text-xs" aria-pressed={view === value} onClick={() => setView(value)}>{label}</Button>)}</div></div>
          <div className="grid grid-cols-4 gap-3">{metrics.map((metric,index) => {const Icon=metric.icon;return <Card key={metric.label} className={"min-w-0 gap-3 py-4 " + (index === 0 ? "border-[#10285b] bg-[#10285b] text-white" : "")}><CardContent className="flex h-full flex-col"><div className="flex items-center justify-between gap-2"><span className={"text-xs font-medium " + (index === 0 ? "text-blue-100" : "text-muted-foreground")}>{metric.label}</span><Icon className={"size-4 " + (index === 0 ? "text-sky-300" : "text-muted-foreground")} /></div><p className="mt-4 text-[26px] leading-8 font-semibold tracking-[-0.03em] tabular-nums"><DataValue>{metric.value}</DataValue><small className={"ml-2 text-xs font-normal " + (index === 0 ? "text-blue-100" : "text-muted-foreground")}>{metric.unit}</small></p><p className={"mt-2 text-xs leading-5 " + (index === 0 ? "text-blue-100/75" : "text-muted-foreground")}>{metric.note}</p>{metric.path && hasPerm(metric.permission) && <button type="button" className={"mt-auto flex min-h-8 items-center gap-1 self-start pt-2 text-xs font-medium " + (index === 0 ? "text-sky-200" : "text-primary")} onClick={() => navigate(metric.path)}>查看明细<ArrowRight className="size-3" /></button>}</CardContent></Card>})}</div>
        </section>
        <Card><CardContent className="flex flex-wrap items-center gap-4 py-4"><div className="min-w-40 flex-1"><h2 className="flex items-center gap-2 text-base font-semibold"><UsersRound className="size-4 text-primary" />账号规模</h2><p className="mt-1 text-xs text-muted-foreground">仅{store.name}账号数量</p></div><dl className="grid min-w-[320px] flex-[3] grid-cols-3 gap-4">{[["user_total","全部账号"],["customer_total","老板"],["provider_total","陪玩"]].map(([field,label]) => <div key={field} className="border-l pl-4"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-xl font-semibold tabular-nums"><DataValue>{numberText(data[field])}</DataValue></dd></div>)}</dl></CardContent></Card>
        <div className="grid min-w-0 grid-cols-[1.45fr_1fr] gap-4">
          <Card className="min-w-0"><CardHeader><CardTitle className="text-base">近七日下单趋势</CardTitle><CardDescription>订单数含未支付和取消 · 净额已扣当前退款，非当日收入</CardDescription></CardHeader><CardContent>{days.length ? <><ChartContainer config={{count:{label:"新增订单",color:"var(--primary)"}}} className="h-[220px] w-full"><BarChart data={days} margin={{top:12,right:8,left:-16,bottom:0}}><CartesianGrid vertical={false} strokeDasharray="4 4" /><XAxis dataKey="date" tickFormatter={value => String(value).slice(5)} tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><ChartTooltip content={<ChartTooltipContent />} /><Bar dataKey="count" fill="var(--color-count)" radius={[5,5,0,0]} maxBarSize={34} /></BarChart></ChartContainer><div className="mt-2 grid grid-cols-7 gap-1">{days.map((day:Row) => <button type="button" key={day.date} disabled={!hasPerm("order:view")} onClick={() => navigate(`/orders?created_day=${day.date}&exclude_direct_test=1`)} className="min-w-0 rounded-md border px-1 py-2 text-center hover:border-primary/40 disabled:opacity-50"><span className="block text-xs text-muted-foreground">{String(day.date).slice(5)}</span><strong className="mt-1 block text-xs">{day.count} 单</strong><span className="mt-1 block truncate text-xs text-muted-foreground" title={`${money(day.net_amount)} ${coinUnit()}`}>{money(day.net_amount)} {coinUnit()}</span></button>)}</div></> : <div className="grid h-[220px] place-items-center text-sm text-muted-foreground">{result.data ? "暂无趋势数据" : <Status readState={result.loading ? "loading" : result.error ? "error" : "unread"} />}</div>}</CardContent></Card>
          <Card><CardHeader><CardTitle className="text-base">当前订单状态</CardTitle><CardDescription>全部历史订单的当前状态快照</CardDescription></CardHeader><CardContent className="space-y-4">{Object.entries(ORDER_STATUS).map(([status,item]) => {const known=!!data.order_status,count=known ? Number(data.order_status[status] ?? 0) : null,pct=statusTotal && count != null ? count/statusTotal*100 : 0;return <button type="button" key={status} disabled={!hasPerm("order:view")} onClick={() => navigate(`/orders?order_status=${status}&exclude_direct_test=1`)} className="grid min-h-8 w-full grid-cols-[72px_minmax(60px,1fr)_36px_42px] items-center gap-2 text-left disabled:opacity-50"><Status value={status} label={item.label} /><span className="h-2 overflow-hidden rounded-full bg-muted"><span className={"block h-full rounded-full " + (status === "COMPLETED" ? "bg-success" : status === "PENDING" ? "bg-warning" : status === "IN_SERVICE" ? "bg-primary" : "bg-muted-foreground/40")} style={{width:`${pct}%`}} /></span><strong className="text-right text-xs tabular-nums">{numberText(count)}</strong><span className="text-right text-xs text-muted-foreground"><DataValue known={known}>{Math.round(pct)}%</DataValue></span></button>})}</CardContent></Card>
        </div>
        <Card className="min-w-0 overflow-hidden"><CardHeader className="flex-row items-center justify-between space-y-0"><div><CardTitle className="text-base">最近创建的订单</CardTitle><CardDescription className="mt-1">最近 <DataValue known={Array.isArray(data.recent_orders)}>{Math.min(data.recent_orders?.length || 0,4)}</DataValue> 笔 · 订单金额已计优惠，未扣退款</CardDescription></div>{hasPerm("order:view") && <Button variant="outline" size="sm" onClick={() => navigate("/orders")}>查看全部<ChevronRight className="size-3.5" /></Button>}</CardHeader>
          <div className="overflow-x-auto border-t"><Table className="min-w-[900px]"><TableHeader><TableRow><TableHead>订单号</TableHead><TableHead>老板</TableHead><TableHead>陪玩</TableHead><TableHead>服务</TableHead><TableHead className="text-right">订单金额</TableHead><TableHead>订单状态</TableHead><TableHead>支付状态</TableHead><TableHead>下单时间</TableHead></TableRow></TableHeader><TableBody>{recentOrders.map((row:Row) => <TableRow key={row.id}><TableCell className="min-w-60"><button type="button" disabled={!hasPerm("order:view")} className="text-xs whitespace-nowrap text-primary hover:underline disabled:text-muted-foreground" onClick={() => navigate(`/orders?detail=${row.id}`)}>{row.entity_no || row.order_no}</button></TableCell><TableCell>{row.customer_name}</TableCell><TableCell>{providerNames(row)}</TableCell><TableCell className="max-w-52 truncate" title={row.service_name_snapshot}>{row.service_name_snapshot}</TableCell><TableCell className="whitespace-nowrap text-right font-medium tabular-nums">{amountToXaCoin(row.amount)} {coinUnit()}</TableCell><TableCell><Status value={row.status} label={row.status_display} /></TableCell><TableCell><Status value={row.payment_status} /></TableCell><TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(row.created_at)}</TableCell></TableRow>)}{!recentOrders.length && <TableRow><TableCell colSpan={8} className="h-40 text-center text-muted-foreground"><DataRegion label="最近订单" loading={result.loading} hasSnapshot={!!result.data} error={result.error} retry={result.reload}><span>暂无最近订单</span></DataRegion></TableCell></TableRow>}</TableBody></Table></div>
        </Card>
      </>}
    </div>
  </DataPageState>
}

export function PlayerDashboardPage() {
  const [dates, setDates] = useState({ start_date: "", end_date: "" })
  const [query, setQuery] = useState({ start_date: "", end_date: "" })
  const [error, setError] = useState("")
  const result = useLoad(
    async () => (await playerDashboardApi.stats(query, true)).data,
    [JSON.stringify(query)]
  )
  useLiveReload(result.reload)
  const data = result.data || {}
  const metric = (pairs: [string, string, LucideIcon][], source: Row) => (
    <div className="grid grid-cols-4 gap-3">
      {pairs.map(([key, label, Icon]) => (
        <div key={key} className="rounded-xl border bg-card p-4">
          <p className="flex items-center justify-between gap-2 text-xs text-muted-foreground">{label}<Icon size={16} className="shrink-0 text-primary" /></p>
          <p className="mt-3 text-xl font-semibold tabular-nums">
            <DataValue known={source?.[key] != null} loading={result.loading}>{amountToXaCoin(source?.[key])}</DataValue>
            <small className="ml-1 text-xs font-normal">{coinUnit()}</small>
          </p>
        </div>
      ))}
    </div>
  )
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="player-dashboard"
    >
      {
        <div className="space-y-6">
          <PageHeader
            title="陪玩业绩"
            description="订单按创建日期统计，退款与净额以当前快照为准。"
            filterDescription="日期作用于订单净额、分账占比和排行；账户与提现仍为全量快照。"
            filters={<form
            className="admin-filter-form flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (
                dates.start_date &&
                dates.end_date &&
                dates.start_date > dates.end_date
              ) {
                setError("开始日期不能晚于结束日期。")
                return
              }
              setError("")
              setQuery({ ...dates })
            }}
          >
            <div className="space-y-2">
              <Label htmlFor="start-date">开始日期</Label>
              <Input
                id="start-date"
                type="date"
                value={dates.start_date}
                onChange={(event) =>
                  setDates({ ...dates, start_date: event.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="end-date">结束日期</Label>
              <Input
                id="end-date"
                type="date"
                value={dates.end_date}
                onChange={(event) =>
                  setDates({ ...dates, end_date: event.target.value })
                }
              />
            </div>
            <Button type="submit">查询</Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setDates({ start_date: "", end_date: "" })
                setQuery({ start_date: "", end_date: "" })
                setError("")
              }}
            >
              全部时间
            </Button>
          </form>}
          >
            <RefreshButton loading={result.loading} reload={result.reload} />
          </PageHeader>

          <Feedback message={error || result.error} retry={result.reload} />
          {(
              <>
                <p className="text-xs text-muted-foreground">
                  开始读取：{formatDateTime(data.snapshot_at)} ·
                  订单净额按筛选日期统计，账户与提现为全量快照。
                </p>
                {metric(
                  [
                    ["total_amount", "订单净额", ReceiptText],
                    ["game_amount", "游戏单", Gamepad2],
                    ["gift_amount", "礼物单", Gift],
                    ["male_game", "男陪游戏单", Gamepad2],
                    ["female_game", "女陪游戏单", Gamepad2],
                    ["male_gift", "男陪礼物单", Gift],
                    ["female_gift", "女陪礼物单", Gift],
                  ],
                  data.kpi
                )}
                <div className="grid grid-cols-3 gap-4">
                  <ShareChart
                    title="订单净额占比" known={!!data.kpi} loading={result.loading} error={result.error} retry={result.reload}
                    first={data.kpi?.game_amount}
                    second={data.kpi?.gift_amount}
                    labels={["游戏单", "礼物单"]}
                  />
                  <ShareChart
                    title="游戏单分账占比" known={!!data.kpi} loading={result.loading} error={result.error} retry={result.reload}
                    first={data.kpi?.male_game}
                    second={data.kpi?.female_game}
                    labels={["男陪", "女陪"]}
                    hint="仅含已标注性别的陪玩分账"
                  />
                  <ShareChart
                    title="礼物单分账占比" known={!!data.kpi} loading={result.loading} error={result.error} retry={result.reload}
                    first={data.kpi?.male_gift}
                    second={data.kpi?.female_gift}
                    labels={["男陪", "女陪"]}
                    hint="仅含已标注性别的陪玩分账"
                  />
                </div>
                {data.income_review?.order_count > 0 && (
                  <Feedback
                    message={`有 ${data.income_review.order_count} 单、${data.income_review.provider_count} 位陪玩的分账收益待核对${data.income_review.unidentified_count ? `，另有 ${data.income_review.unidentified_count} 条归属不明` : ""}。请核对退款与钱包流水，暂不计入收益排行。`}
                  />
                )}
                <div className="grid grid-cols-3 gap-4">
                  {[
                    ["amount", "接单额榜", "接单额"],
                    ["income", "分账收益榜", "分账收益"],
                    ["count", "接单量榜", "接单量"],
                  ].map(([key, title, amount]) => (
                    <div key={key} className="min-w-0">
                      <DataTable
                        title={`${title} Top 10`}
                        titleIcon={Medal}
                        snapshotAvailable={Array.isArray(data.rank?.[key])} loading={result.loading} error={result.error} retry={result.reload}
                        rows={(data.rank?.[key] || []).map(
                          (row: Row, index: number) => ({
                            ...row,
                            rank: index + 1,
                          })
                        )}
                        rowKey="escort_no"
                        minWidth={560}
                        columnWidths={{ rank: 48, escort_no: 128, gender: 64, value: 104 }}
                        columns={[
                          { key: "rank", label: "#" },
                          { key: "nickname", label: "昵称" },
                          { key: "escort_no", label: "编号" },
                          {
                            key: "gender",
                            label: "性别",
                            render: (row) =>
                              GENDER[row.gender]?.label || "未知",
                          },
                          {
                            align: "right",
                            key: "value",
                            label: amount,
                            render: (row) =>
                              key === "count"
                                ? `${numberText(row.value)} 单`
                                : `${amountToXaCoin(row.value)} ${coinUnit()}`,
                          },
                        ]}
                      />
                    </div>
                  ))}
                </div>
                <h2 className="text-lg font-semibold">账户与提现</h2>
                {metric(
                  [
                    ["total_deposit", "押金总数", ShieldCheck],
                    ["total_penalty", "罚款总数", CircleMinus],
                    ["total_reward", "奖励总数", Medal],
                    ["total_wallet", "钱包总额", Wallet],
                    ["frozen_amount", "冻结金额", LockKeyhole],
                    ["settled_salary", "已通过提现申请额", BanknoteArrowDown],
                  ],
                  data.secondary
                )}
              </>
          )}
        </div>
      }
    </DataPageState>
  )
}
function ShareChart({
  title,
  first,
  second,
  labels,
  hint,
  known,
  loading,
  error,
  retry,
}: {
  known: boolean
  loading: boolean
  error?: string | null
  retry: () => void
  title: string
  first: unknown
  second: unknown
  labels: [string, string]
  hint?: string
}) {
  const a = Number(first),
    b = Number(second),
    total = a + b
  const hasData = Number.isFinite(total) && a >= 0 && b >= 0 && total > 0
  const ratio = hasData ? (a / total) * 100 : 0
  return (
    <section className="space-y-4 rounded-xl border bg-card p-4">
      <h2 className="font-semibold">{title}</h2>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      <DataRegion label={title} loading={loading} hasSnapshot={known} error={error} retry={retry}>{hasData ? (
        <>
          <div
            className="mx-auto grid size-36 place-items-center rounded-full"
            role="img"
            aria-label={`${labels[0]} ${ratio.toFixed(1)}%，${labels[1]} ${(100 - ratio).toFixed(1)}%`}
            style={{
              background: `conic-gradient(#2563eb ${ratio}%,#7dd3fc ${ratio}% 100%)`,
            }}
          >
            <div className="grid size-24 place-items-center rounded-full bg-card text-center">
              <div>
                <p className="text-xs text-muted-foreground">{coinText("合计浪花")}</p>
                <p className="mt-1 text-sm font-semibold">
                  {amountToXaCoin(total)}
                </p>
              </div>
            </div>
          </div>
          <div className="space-y-2">
            {[
              [labels[0], a, ratio],
              [labels[1], b, 100 - ratio],
            ].map(([label, value, percent], index) => (
              <div
                key={label}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="flex items-center gap-2">
                  <i
                    className={`size-2.5 rounded-full ${index === 0 ? "bg-blue-600" : "bg-sky-300"}`}
                  />
                  {label}
                </span>
                <span>
                  {amountToXaCoin(Number(value))} {coinText(" 浪花 ·")}{" "}
                  {Number(percent).toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="grid min-h-40 place-items-center text-sm text-muted-foreground">
          暂无可展示数据
        </p>
      )}</DataRegion>
    </section>
  )
}
