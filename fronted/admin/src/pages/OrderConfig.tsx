import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ChartLine } from "@/components/ui/icons"
import { Skeleton } from "@/components/ui/skeleton"
import { Status } from "@/components/Status"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataTable,
  Feedback,
  Field,
  MutationDialog,
  Pagination,
  RefreshButton,
  useList,
  useLoad,
  type Row,
} from "@/components/business"
import { request } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { formatDateTime } from "@/lib/format"

export default function OrderConfig({
  kind,
  onClose,
}: {
  kind: "timeout" | "traffic" | "capacity" | "grab"
  onClose: () => void
}) {
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onClose()
      }}
    >
      <DialogContent
        className={`admin-order-config-dialog max-h-[90vh] overflow-y-auto ${kind === "traffic" ? "sm:max-w-6xl" : "sm:max-w-xl"}`}
      >
        <DialogHeader>
          <DialogTitle>
            {kind === "timeout" ? "待接单时限" : kind === "capacity" ? "陪玩接单名额" : kind === "grab" ? "抢单窗口" : "峰谷限流与监控"}
          </DialogTitle>
          <DialogDescription>
            {kind === "timeout"
              ? "仅影响新订单；余额支付订单到期未接单将取消并退款，已有订单截止时间不变。"
              : kind === "capacity" ? "本店所有陪玩使用同一上限；已确认预约开始时可暂时超出。" : kind === "grab" ? "配置本店抢单等待时间，已入池订单保留原配置。" : "北京时间，按时段与操作调整配额；保存后保留当前窗口计数。"}
          </DialogDescription>
        </DialogHeader>
        {kind === "timeout" ? <TimeoutConfig /> : kind === "capacity" ? <CapacityConfig /> : kind === "grab" ? <GrabWindowConfig /> : <TrafficConfig />}
      </DialogContent>
    </Dialog>
  )
}
function GrabWindowConfig() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: "/config/grab-window/" })).data)
  const [seconds, setSeconds] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  useEffect(() => { if (result.data) { setSeconds(result.data.window_seconds == null ? "" : String(result.data.window_seconds)); setBlocked("") } }, [result.data])
  const valid = /^[1-9]\d*$/.test(seconds) && Number(seconds) <= 86400
  const locked = result.loading || !!result.error || !!blocked || !auth.hasPerm("dictionary:edit")
  return <div className="space-y-4">
    <Feedback message={result.error || blocked} retry={result.reload} />
    {result.data && <>
      <p className="text-sm text-muted-foreground">自动抢单尚未启用，当前订单仍按现有方式分配。</p>
      <Field field={{ key: "grab-window", label: "抢单窗口（秒）", type: "number", min: 1, max: 86400, step: 1 }} value={seconds} onChange={value => setSeconds(String(value))} disabled={locked} />
      <RefreshButton loading={result.loading} reload={result.reload} />
      {auth.hasPerm("dictionary:edit") && <Button size="sm" disabled={locked || !valid || Number(seconds) === result.data.window_seconds} onClick={() => setConfirm(true)}>保存窗口</Button>}
    </>}
    {confirm && result.data && <MutationDialog title="确认抢单窗口" description={`本店抢单窗口由 ${result.data.window_seconds === undefined ? "未读取" : result.data.window_seconds === null ? "未配置" : `${result.data.window_seconds} 秒`}调整为 ${seconds} 秒。`} onClose={() => setConfirm(false)} onSubmit={async () => {
      try { const data = (await request({ url: "/config/grab-window/", method: "PUT", data: { window_seconds: Number(seconds), version: result.data.version } })).data; result.setData(data); toast.success("抢单窗口已保存") }
      catch (cause: any) { setBlocked(cause.status === 409 ? "配置已变化，请重新读取后核对。" : "保存结果未确认，请先刷新并核对当前配置。"); throw cause }
    }} />}
  </div>
}
function CapacityConfig() {
  const auth = useAdminAuth()
  const result = useLoad(async () => (await request({ url: "/config/provider-capacity/" })).data)
  const [limit, setLimit] = useState("")
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  useEffect(() => {
    if (result.data) { setLimit(String(result.data.max_concurrent_orders)); setBlocked("") }
  }, [result.data])
  const valid = /^[1-9]\d*$/.test(limit) && Number(limit) <= 2147483647
  const locked = result.loading || !!result.error || !!blocked || !auth.hasPerm("dictionary:edit")
  return <div className="space-y-4">
    <Feedback message={result.error || blocked} retry={result.reload} />
    {result.data && <>
      <Field field={{ key: "provider-capacity", label: "每位陪玩同时接单上限", type: "number", min: 1, max: 2147483647, step: 1 }}
        value={limit} onChange={value => setLimit(String(value))} disabled={locked} />
      <p className="text-xs text-muted-foreground">修改后用于后续接单资格判断，已接订单继续履约；尚未开始的已付款预约不占名额。</p>
      <RefreshButton loading={result.loading} reload={result.reload} />
      {auth.hasPerm("dictionary:edit") && <Button size="sm" disabled={locked || !valid || (result.data.version > 0 && Number(limit) === result.data.max_concurrent_orders)}
        onClick={() => setConfirm(true)}>保存名额</Button>}
    </>}
    {confirm && result.data && <MutationDialog title="确认修改接单名额"
      description={`本店每位陪玩的上限由 ${result.data.max_concurrent_orders} 单调整为 ${limit} 单。`}
      onClose={() => setConfirm(false)} onSubmit={async () => {
        try {
          const data = (await request({ url: "/config/provider-capacity/", method: "PUT",
            data: { max_concurrent_orders: Number(limit), version: result.data.version } })).data
          result.setData(data)
          toast.success("接单名额已保存")
        } catch (cause: any) {
          setBlocked(cause.status === 409 ? "配置已变化，请重新读取后核对。" : "保存结果未确认，请先刷新并核对当前配置。")
          throw cause
        }
      }} />}
  </div>
}
function TimeoutConfig() {
  const auth = useAdminAuth()
  const result = useLoad(
    async () => (await request({ url: "/config/order-timeout/" })).data
  )
  const [minutes, setMinutes] = useState(30)
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  const history = useList("config/order-timeout/history")
  useEffect(() => {
    if (result.data) {
      setMinutes(result.data.minutes)
      setBlocked("")
    }
  }, [result.data])
  if (!result.data)
    return (
      <div className="space-y-4">
        <Feedback message={result.error} retry={result.reload} />
        {result.loading && (
          <div
            aria-busy="true"
            aria-label="正在读取待接单时限"
            className="space-y-3"
          >
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        )}
      </div>
    )
  return (
    <div className="space-y-4">
      <Feedback message={result.error} retry={result.reload} />
      <Feedback message={blocked} retry={result.reload} />
      <Field
        field={{
          key: "timeout-minutes",
          label: "待接单时限（分钟）",
          type: "number",
          min: 1,
          max: 1440,
          step: 1,
        }}
        value={minutes}
        onChange={setMinutes}
        disabled={!auth.hasPerm("order:timeout") || !!blocked || result.loading}
      />
      <p className="text-xs text-muted-foreground">
        指定陪玩和预约订单按原流程处理；短时限可能受通行证可见延迟影响。微信支付退款以订单状态为准，部分情况需人工核对。
      </p>
      <div className="flex gap-2">
        <RefreshButton loading={result.loading} reload={result.reload} />
        {auth.hasPerm("order:timeout") && (
          <Button
            size="sm"
            disabled={
              !result.data ||
              !!result.error ||
              !!blocked ||
              result.loading ||
              !Number.isInteger(minutes) ||
              minutes < 1 ||
              minutes > 1440 ||
              minutes === result.data.minutes
            }
            onClick={() => setConfirm(true)}
          >
            保存时限
          </Button>
        )}
      </div>
      {auth.hasPerm("audit:view") && (
        <details className="admin-order-config-history">
          <summary>变更历史</summary>
          <Feedback message={history.error} retry={history.reload} />
          <DataTable
            rows={history.rows}
            loading={history.loading}
            columns={[
              { key: "revision", label: "版本" },
              { key: "previous_minutes", label: "原时限（分钟）" },
              { key: "minutes", label: "新时限（分钟）" },
              { key: "operator_name", label: "操作人" },
              {
                key: "created_at",
                label: "时间",
                render: (row) => formatDateTime(row.created_at),
              },
            ]}
          />
          <Pagination
            page={history.page}
            total={history.total}
            loading={history.loading}
            onChange={history.setPage}
            pageSize={history.pageSize}
            onPageSizeChange={history.setPageSize}
          />
        </details>
      )}
      {confirm && (
        <MutationDialog
          title="保存待接单时限"
          description={`新订单的待接单时限改为 ${minutes} 分钟；已创建订单保持原截止时间。`}
          onClose={() => setConfirm(false)}
          onSubmit={async () => {
            try {
              const data = (
                await request({
                  url: "/config/order-timeout/",
                  method: "PUT",
                  data: { minutes, revision: result.data.revision },
                })
              ).data
              result.setData(data)
              toast.success("已保存，后续新订单生效")
              void history.reload()
            } catch (cause: any) {
              setBlocked(
                cause.status === 409
                  ? "配置已变化，请重新读取后核对。"
                  : "保存结果未确认，请先核对请求状态；刷新显示当前配置。"
              )
              throw cause
            }
          }}
        />
      )}
    </div>
  )
}
const modes: Row = { peak: "波峰", normal: "常规", valley: "波谷" }
const actions: Row = {
  create: "自主下单",
  grab: "抢单 / 申请",
  pool: "抢单池刷新",
  quote: "报价",
}
const limits = [
  { key: "account_per_minute", label: "每账号 / 分钟", max: 10000 },
  { key: "global_per_minute", label: "全站 / 分钟", max: 1000000 },
  { key: "global_per_second", label: "全站 / 秒", max: 100000 },
]
function TrafficConfig() {
  const auth = useAdminAuth()
  const result = useLoad(
    async () => (await request({ url: "/order-traffic/" })).data
  )
  const [form, setForm] = useState<Row | null>(null)
  const [mode, setMode] = useState("peak")
  const [confirm, setConfirm] = useState(false)
  const [blocked, setBlocked] = useState("")
  useEffect(() => {
    if (result.data) {
      setForm(structuredClone(result.data))
      setBlocked("")
      if (modes[result.data.mode]) setMode(result.data.mode)
    }
  }, [result.data])
  const editable =
    auth.hasPerm("order:traffic") &&
    !result.loading &&
    !result.error &&
    !blocked
  function change(key: string, value: any) {
    setForm((old) => (old ? { ...old, [key]: value } : old))
  }
  let error = ""
  if (form) {
    const hourIn = (hour: number, start: number, end: number) =>
      start < end ? start <= hour && hour < end : hour >= start || hour < end
    for (const prefix of ["peak", "valley"]) {
      const start = form[`${prefix}_start`],
        end = form[`${prefix}_end`]
      if (
        !Number.isInteger(start) ||
        start < 0 ||
        start > 23 ||
        !Number.isInteger(end) ||
        end < 0 ||
        end > 24 ||
        start === end
      )
        error = "时段须使用 0–24 整点，起点不能为 24，起止不能相同。"
    }
    if (
      Array.from({ length: 24 }, (_, h) => h).some(
        (h) =>
          hourIn(h, form.peak_start, form.peak_end) &&
          hourIn(h, form.valley_start, form.valley_end)
      )
    )
      error = "波峰和波谷时段不能重叠。"
    for (const m of Object.keys(modes))
      for (const a of Object.keys(actions))
        for (const field of limits) {
          const value = form.limits?.[m]?.[a]?.[field.key]
          if (!Number.isInteger(value) || value < 1 || value > field.max)
            error = `${modes[m]} · ${actions[a]}：${field.label}须为 1–${field.max} 的整数。`
        }
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2"><strong>当前时段</strong><Status label={modes[result.data?.mode]} readState={!result.data ? result.loading ? "loading" : result.error ? "error" : "unread" : result.data.mode == null ? "unread" : undefined} /></div>
        <RefreshButton loading={result.loading} reload={result.reload} />
      </div>
      <Feedback message={result.error || blocked} retry={result.reload} />
      <div className="flex flex-wrap items-center gap-2 text-sm"><span>流量控制</span><Status value={result.data?.runtime_enabled} label={typeof result.data?.runtime_enabled === "boolean" ? result.data.runtime_enabled ? "已启用" : "未启用" : undefined} readState={!result.data ? result.loading ? "loading" : result.error ? "error" : "unread" : undefined} /></div>
      {result.data?.runtime_enabled !== true && (
        <Feedback
          message={
            result.data?.runtime_enabled === false
              ? "当前服务实例未启用流量控制，计数不能代表全部请求。"
              : "流量控制状态暂不可用，请核对配置。"
          }
        />
      )}
      {form && (
        <>
          <fieldset disabled={!editable} className="grid grid-cols-2 gap-4">
            {[
              { key: "enabled", label: "启用峰谷限流", type: "boolean" },
              {
                key: "self_order_enabled",
                label: "允许老板自主下单",
                type: "boolean",
              },
              ...["peak", "valley"].flatMap((prefix) => [
                {
                  key: `${prefix}_start`,
                  label: `${modes[prefix]}开始小时`,
                  type: "number",
                  min: 0,
                  max: 23,
                  step: 1,
                },
                {
                  key: `${prefix}_end`,
                  label: `${modes[prefix]}结束小时`,
                  type: "number",
                  min: 0,
                  max: 24,
                  step: 1,
                },
              ]),
            ].map((field) => (
              <Field
                key={field.key}
                field={field as any}
                value={form[field.key]}
                onChange={(value) => change(field.key, value)}
              />
            ))}
          </fieldset>
          <div className="flex gap-2">
            {Object.entries(modes).map(([key, label]) => (
              <Button
                key={key}
                size="sm"
                variant={mode === key ? "secondary" : "ghost"}
                onClick={() => setMode(key)}
              >
                {String(label)}
              </Button>
            ))}
          </div>
          <DataTable
            rows={Object.entries(actions).map(([key, label]) => ({
              id: key,
              label,
            }))}
            columns={[
              { key: "label", label: "操作" },
              ...limits.map((field) => ({
                key: field.key,
                label: field.label,
                render: (row: Row) => (
                  <Input
                    type="number"
                    min="1"
                    max={field.max}
                    step="1"
                    aria-label={`${modes[mode]} ${row.label} ${field.label}`}
                    value={form.limits?.[mode]?.[row.id]?.[field.key] ?? ""}
                    disabled={!editable}
                    onChange={(event) => {
                      const next = structuredClone(form.limits)
                      next[mode][row.id][field.key] = Number(event.target.value)
                      change("limits", next)
                    }}
                  />
                ),
              })),
            ]}
          />
          <Feedback message={error} />
          <DataTable
            title={`最近 ${result.data?.metrics?.window_minutes ?? "—"} 分钟流量`}
            titleIcon={ChartLine}
            rows={Object.entries(actions).map(([key, label]) => ({
              id: key,
              label,
              admitted: result.data?.metrics?.[`${key}_admitted`] ?? "—",
              limited: result.data?.metrics?.[`${key}_limited`] ?? "—",
            }))}
            columns={[
              { key: "label", label: "操作" },
              { key: "admitted", label: "放行请求" },
              { key: "limited", label: "限流请求" },
            ]}
          />
          <p className="text-xs text-muted-foreground">
            客户端上报 {result.data?.metrics?.network_reports ?? "—"} 次 ·
            平均耗时 {result.data?.metrics?.average_latency_ms ?? "—"} ms ·
            网络失败 {result.data?.metrics?.network_failure_percent ?? "—"}%
          </p>
          {auth.hasPerm("order:traffic") && (
            <Button
              size="sm"
              disabled={
                !editable ||
                !!error ||
                JSON.stringify(form) === JSON.stringify(result.data)
              }
              onClick={() => setConfirm(true)}
            >
              保存流量配置
            </Button>
          )}
        </>
      )}
      {confirm && (
        <MutationDialog
          title="保存峰谷流量配置"
          description={`波峰 ${form?.peak_start}:00–${form?.peak_end}:00，波谷 ${form?.valley_start}:00–${form?.valley_end}:00。限流${form?.enabled ? "开启" : "关闭"}；自主下单${form?.self_order_enabled ? "允许" : "关闭"}。配额按当前三个时段生效。`}
          onClose={() => setConfirm(false)}
          onSubmit={async () => {
            const keys = [
              "revision",
              "enabled",
              "self_order_enabled",
              "peak_start",
              "peak_end",
              "valley_start",
              "valley_end",
              "limits",
            ]
            try {
              const data = (
                await request({
                  url: "/order-traffic/",
                  method: "PUT",
                  data: Object.fromEntries(
                    keys.map((key) => [key, form?.[key]])
                  ),
                })
              ).data
              result.setData(data)
              toast.success("流量配置已保存")
            } catch (cause: any) {
              setBlocked(
                cause.status === 409
                  ? "配置已变化，请重新读取后核对。"
                  : "保存结果未确认，请先核对请求状态。"
              )
              throw cause
            }
          }}
        />
      )}
    </div>
  )
}
