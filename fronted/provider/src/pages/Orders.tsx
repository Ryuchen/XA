import { coinText, coinUnit, NegotiationProposalPanel, OrderJourney, OrderSource, OrderSyncBar, orderLifecycle } from "@xa/shared"
import {
  providerOrderActions,
  providerOrderPresentation,
  providerReportDestination,
  mutateAndReload,
  orderBusinessDetails,
  loadProviderApplications,
  providerApplicationPresentation,
  schedulePendingOrderRefresh,
  updateProviderStatus,
  useRealtime,
} from "@xa/shared"
import { useCallback, useEffect, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router"
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Camera,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  CalendarClock,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  Gamepad2,
  RefreshCw,
  ShieldCheck,
  UsersRound,
} from "lucide-react"
import { useSession } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { VoicePicker } from "@/components/VoicePicker"
import { RoomCompletionPanel } from "@/components/RoomCompletionPanel"
import { SearchField } from "@/components/SearchField"
import { Textarea } from "@/components/ui/textarea"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  Status,
  DetailGrid,
  FilterBar,
  Stat,
  Pager,
  Confirm,
  Field,
  UploadField,
  AvailabilityControl,
  PrivateImage,
  selectClass,
} from "@/components/provider-ui"
import {
  api,
  useLoad,
  useRefresh,
  money,
  date,
  message,
  uncertain,
  requestId,
  useViewGuard,
} from "@/lib/provider"

type Application = {
  id: number
  team_size?: number
  order_id?: number
  order_no?: string
  service_name?: string
  created_at?: string
  registration_ended?: boolean
  status: string
  ready: boolean
  is_partner: boolean
  partner_confirmed: boolean
  my_recording_submitted?: boolean
  my_recording_unavailable?: boolean
  members: ({ id: number; nickname: string; level: string } | string)[]
}
type Refund = {
  amount: number
  remaining_amount?: number | null
  negotiated_provider_burden?: number | null
  negotiated_provider_retained?: number | null
  recovered_amount?: number | null
  net_income?: number | null
  referral_recovered_amount?: number | null
  referral_net_income?: number | null
  remaining_frozen_amount?: number | null
  income_breakdown_unavailable?: boolean
}
type SegmentSubmission = {
  version: number
  status: string
  submitted_at: string
  review_remark?: string
  freeze_until?: string | null
  entry_image_url?: string
  completion_image_url?: string
  result_images: { id: number; url: string }[]
}
type SegmentReport = {
  status: string
  version: number
  submission?: SegmentSubmission | null
  history: SegmentSubmission[]
  draft_images: { id: number; url: string }[]
}
type Order = {
  id: number
  order_no: string
  entity_no?: string
  status: string
  amount: number
  order_source?: string
  payment_status?: string
  auto_cancel_at?: string | null
  pending_deadline_expired?: boolean
  cancel_reason?: string
  aftersale_status?: string
  completion_block_reason?: string
  refund_summary?: Refund | null
  settlement_status?: string
  settled_at?: string | null
  fulfillment?: string
  gameplay_snapshot?: { rules?: string }
  reservation_origin?: {
    id: number
    start_time: string
    end_time: string
    can_open: boolean
  }
  my_assignment_released?: boolean
  my_assignment_departure?: string
  my_assignment_settled?: boolean
  my_participation_segments?: {
    id: number
    accepted_at: string
    started_at: string | null
    released_at: string | null
    is_service_transfer: boolean
    entry_image_url?: string
    completion_image_url?: string
    can_upload_completion?: boolean
    segment_report_available?: boolean
    segment_report?: SegmentReport | null
  }[]
  team_progress?: {
    stage: string
    submitted_count: number
    total_count: number
    members: { id: number; nickname: string; submitted: boolean }[]
  }
  participants?: { id: number; nickname: string }[]
  service_name_snapshot?: string
  service?: { name: string; game_category_name?: string }
  expected_income: number | null
  customer?: { nickname: string }
  remark?: string
  game_rounds: number
  quantity_unit?: string
  game_region?: string
  game_nickname?: string
  game_uid?: string
  allowed_actions?: string[]
  can_operate?: boolean
  can_reject?: boolean
  requires_boss_selection?: boolean
  audition_recordings_required?: boolean
  required_team_size?: number
  can_team_apply?: boolean
  dispatch_route?: string
  my_applications?: Application[]
  report_entry?: { available: boolean; reason: string }
  request_replayed?: boolean
  is_gift_snapshot?: boolean
  source_order?: unknown
  created_at?: string
  grabbed_at?: string | null
  in_service_at?: string | null
  completed_at?: string | null
  room_required_snapshot?: boolean | null
  cancelled_at?: string | null
  refunded_at?: string | null
  report_ready_at?: string | null
  consumption_ready_at?: string | null
  consumption_confirmed_at?: string | null
}
const segmentReportStatus: Record<string, string> = {
  DRAFT: "待提交", PENDING: "待审核", APPROVED: "审核通过", REJECTED: "已驳回", WITHDRAWN: "已撤回",
}
type Stats = {
  escort_status: string
  rating_avg: number
  rating_count: number
  today_orders: number
  serving: number
  completion_rate: number
  total_income: number
  active_orders: number
  max_concurrent_orders: number
}
type OrderPage = {
  list: Order[]
  total: number
  page: number
  page_size: number
}
const tabs = [
  { id: "pending", label: "抢单池" },
  { id: "grabbed", label: "已接单" },
  { id: "in_progress", label: "服务中" },
  { id: "completed", label: "已完成" },
  { id: "cancelled", label: "已取消" },
]
const members = (row: Application) =>
  row.members
    .map((member) =>
      typeof member === "string"
        ? member
        : `${member.nickname} · ${member.level}`
    )
    .join(" + ")
const applicationStatus = (row: Application) =>
  row.status !== "PENDING"
    ? { SELECTED: "曾被选中", NOT_SELECTED: "未被选中", WITHDRAWN: "已撤回" }[
        row.status
      ] || row.status
    : row.registration_ended
      ? "报名已结束"
      : row.members.length > 1 && !row.partner_confirmed
        ? row.is_partner
          ? "等待你确认"
          : "等待队友确认"
        : "等待老板选择"
export function RefundSummary({ refund }: { refund: Refund }) {
  return (
    <div className="space-y-1 rounded-lg border bg-muted/30 p-3 text-xs">
      <strong>退款与个人收益核对</strong>
      <p>
        已退老板 {money(refund.amount)} {coinText(" 浪花")}
        {refund.remaining_amount != null &&
          ` · 订单保留 ${money(refund.remaining_amount)} ${coinUnit()}`}
      </p>
      {(
        [
          ["negotiated_provider_burden", "本人协商承担"],
          ["negotiated_provider_retained", "协商时保留收益"],
          ["recovered_amount", "本人服务收益追回"],
          ["net_income", "本人服务收益流水净额"],
          ["referral_recovered_amount", "推荐分佣追回"],
          ["referral_net_income", "推荐分佣流水净额"],
          ["remaining_frozen_amount", "本单剩余冻结"],
        ] as const
      ).map(
        ([key, label]) =>
          refund[key] != null && (
            <p key={key}>
              {label}：{money(refund[key])} {coinText(" 浪花")}
            </p>
          )
      )}
      {refund.income_breakdown_unavailable && (
        <p>收益明细待核对，请查看钱包流水或联系客服。</p>
      )}
    </div>
  )
}
function OrderGamePoster({ order }: { order: Order }) {
  const game = order.service?.game_category_name || "游戏信息待核对"
  const service =
    order.service_name_snapshot || order.service?.name || "陪玩服务"
  const identity =
    order.game_nickname || order.game_uid || "游戏身份未提供或当前不可见"
  const time = order.reservation_origin
    ? `${date(order.reservation_origin.start_time)} — ${date(order.reservation_origin.end_time)}`
    : "未提供预约时段"
  const background = game.includes("三角洲")
    ? "radial-gradient(circle at 88% 20%, rgba(56,189,248,.28), transparent 42%), linear-gradient(120deg, #082f49, #0c4a6e 58%, #155e75)"
    : game.includes("金铲铲")
      ? "radial-gradient(circle at 88% 20%, rgba(125,211,252,.28), transparent 42%), linear-gradient(120deg, #0f172a, #164e63 58%, #0369a1)"
      : "radial-gradient(circle at 88% 20%, rgba(125,211,252,.28), transparent 42%), linear-gradient(120deg, #020617, #1e3a8a 58%, #0369a1)"
  return (
    <section
      data-testid="order-game-poster"
      aria-label={`${game}${service}玩法详情海报`}
      className="relative isolate min-h-44 overflow-hidden rounded-[8px] border border-sky-300/20 p-4 text-white shadow-[0_16px_32px_-22px_rgba(2,6,23,.9)] sm:p-5"
      style={{ background }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-9 -right-7 -z-10 size-40 rounded-full border border-sky-200/15"
      >
        <div className="absolute inset-5 rounded-full border border-sky-200/15" />
        <Gamepad2 className="absolute bottom-9 left-9 size-16 text-sky-100/20" />
      </div>
      <div className="relative max-w-[calc(100%-3rem)] sm:max-w-[calc(100%-6rem)]">
        <h3 className="text-xl leading-7 font-semibold text-balance">
          {service}
        </h3>
        <p className="mt-2 flex items-center gap-2 text-sm font-medium text-sky-100">
          <Gamepad2 className="size-4 shrink-0" />
          {game}
        </p>
        <p className="mt-2 text-xs leading-5 break-words text-sky-100">
          {order.game_region || "区服未提供或当前不可见"} · {identity}
        </p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded-[6px] border border-white/15 bg-slate-950/20 px-2 py-1 text-sky-50">
            <Clock3 className="size-3.5 shrink-0" />
            {time}
          </span>
          <span className="rounded-[6px] border border-white/15 bg-slate-950/20 px-2 py-1 text-sky-50">
            {order.game_rounds} {order.quantity_unit || "份"}
          </span>
        </div>
      </div>
    </section>
  )
}
function SegmentCompletionUpload({orderId, segmentId, onSaved}: {orderId: number; segmentId: number; onSaved: () => Promise<unknown>}) {
  const [file, setFile] = useState<File | null>(null),
    [requestKey, setRequestKey] = useState(""),
    [inputVersion, setInputVersion] = useState(0),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("")
  const guardView = useViewGuard()
  async function save() {
    if (!file || !requestKey || busy) return
    const isCurrent = guardView()
    setBusy(true); setNotice("")
    try {
      const outcome = await mutateAndReload({role:"provider", isCurrent, reload:onSaved, mutate:() => {
        const form = new FormData()
        form.append("segment_id", String(segmentId)); form.append("completion_image", file)
        form.append("client_request_id", requestKey)
        return api(`/orders/orders/${orderId}/participation-evidence/`, {method:"POST", data:form})
      }})
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setFile(null); setRequestKey(""); setInputVersion(version => version + 1)
      setNotice(outcome.reloadError ? "截图已保存，订单刷新失败，请重新加载核对。" : "本次履约截图已保存。")
    } catch (cause) {
      if (isCurrent()) setNotice(uncertain(cause) ? "保存结果待核对，可用同一图片重试或刷新订单。" : message(cause))
    } finally { if (isCurrent()) setBusy(false) }
  }
  return <div className="space-y-2">
    <UploadField key={inputVersion} aria-label="选择本次履约结单截图" accept="image/*" disabled={busy} onChange={event => {
      setFile(event.target.files?.[0] || null); setRequestKey(requestId("segment-evidence")); setNotice("")
    }} />
    <Button type="button" size="sm" variant="outline" disabled={!file || busy} onClick={() => void save()}>
      {busy ? "正在保存" : "保存本次结单截图"}
    </Button>
    {notice && <p role="status" className="text-muted-foreground">{notice}</p>}
  </div>
}

function SegmentReportPanel({ orderId, segmentId, report, onSaved }: {
  orderId: number; segmentId: number; report?: SegmentReport | null; onSaved: () => Promise<unknown>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [inputVersion, setInputVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState("")
  const [keys, setKeys] = useState<Record<string, string>>({})
  const guardView = useViewGuard()
  async function perform(action: string, imageId?: number) {
    if (busy || (action === "upload" && !file)) return
    const identity = `${action}:${imageId || ""}:${report?.version ?? 0}`
    const key = keys[identity] || requestId("segment-report")
    setKeys(previous => ({ ...previous, [identity]: key }))
    const isCurrent = guardView()
    setBusy(true); setNotice("")
    try {
      const outcome = await mutateAndReload({ role: "provider", isCurrent, reload: onSaved, mutate: () => {
        const form = new FormData()
        form.append("segment_id", String(segmentId)); form.append("action", action)
        form.append("client_request_id", key)
        if (action === "submit" || action === "withdraw") form.append("version", String(report?.version ?? 0))
        if (imageId) form.append("image_id", String(imageId))
        if (action === "upload" && file) form.append("image", file)
        return api(`/orders/orders/${orderId}/segment-report/`, { method: "POST", data: form })
      } })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setKeys(previous => { const next = { ...previous }; delete next[identity]; return next })
      if (action === "upload") { setFile(null); setInputVersion(value => value + 1) }
      setNotice(outcome.reloadError ? "操作已保存，刷新失败，请重新加载核对。" : "本次履约报单已更新。")
    } catch (cause) {
      if (isCurrent()) setNotice(uncertain(cause) ? "操作结果待核对，可使用原内容重试或刷新订单。" : message(cause))
    } finally { if (isCurrent()) setBusy(false) }
  }
  const editable = report && ["DRAFT", "REJECTED"].includes(report.status)
  return <div className="space-y-2 rounded-lg border p-3" data-testid={`segment-report-${segmentId}`}>
    <p className="font-medium">本次履约报单 · {report ? segmentReportStatus[report.status] || report.status : "未创建"}</p>
    <p className="text-muted-foreground">审核通过不代表收益已结算。</p>
    {!report && <Button size="sm" variant="outline" disabled={busy} onClick={() => void perform("open")}>创建本次履约报单</Button>}
    {report?.submission?.review_remark && <p>审核意见：{report.submission.review_remark}</p>}
    {report?.submission?.freeze_until && <p>本人的报单冻结参考截止：{date(report.submission.freeze_until)}；仍需满足审核和售后条件。</p>}
    {editable && <>
      <p>本次战绩截图</p>
      {report.draft_images.map(image => <div key={image.id} className="space-y-1">
        <PrivateImage src={image.url} alt="本次战绩截图" />
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void perform("delete", image.id)}>删除战绩截图</Button>
      </div>)}
      <UploadField key={inputVersion} aria-label="选择本次战绩截图" accept="image/*" disabled={busy} onChange={event => {
        setFile(event.target.files?.[0] || null); setKeys({}); setNotice("")
      }} />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={!file || busy} onClick={() => void perform("upload")}>上传本次战绩截图</Button>
        <Button size="sm" disabled={busy || report.draft_images.length === 0} onClick={() => void perform("submit")}>提交本次履约报单</Button>
      </div>
    </>}
    {report?.status === "PENDING" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void perform("withdraw")}>撤回本次报单</Button>}
    {!!report?.history.length && <details><summary>查看提交历史</summary>{report.history.map(item => <div key={item.version} className="space-y-1 border-t py-2">
      <p>第 {item.version} 次 · {segmentReportStatus[item.status] || item.status} · {date(item.submitted_at)}</p>
      {item.review_remark && <p>审核意见：{item.review_remark}</p>}
      {item.entry_image_url && <PrivateImage src={item.entry_image_url} alt="提交时入队截图" />}
      {item.completion_image_url && <PrivateImage src={item.completion_image_url} alt="提交时结单截图" />}
      {item.result_images.map(image => <PrivateImage key={image.id} src={image.url} alt="提交时战绩截图" />)}
    </div>)}</details>}
    {notice && <p role="status">{notice}</p>}
  </div>
}

function ProviderOrderDetail({
  orderId,
  onClose,
}: {
  orderId: number
  onClose: () => void
}) {
  const detail = useLoad(
    () => api<Order>(`/orders/orders/${orderId}/`),
    [orderId]
  )
  const order = detail.data
  const presentation = order ? providerOrderPresentation(order) : null
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="provider-confirm prototype-dialog rounded-[12px] sm:max-w-md">
        <DialogHeader className="flex-row items-center gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-[8px] bg-primary/10 text-primary">
            <ClipboardCheck className="size-5" />
          </div>
          <div className="min-w-0 space-y-2">
            <DialogTitle className="text-left text-xl">订单详情</DialogTitle>
            <DialogDescription className="text-left leading-6">
              {order
                ? `订单 ${order.order_no}`
                : detail.loading
                  ? "正在读取订单详情"
                  : "订单详情暂不可用"}
            </DialogDescription>
          </div>
        </DialogHeader>
        {detail.loading && !order ? (
          <div
            role="status"
            aria-label="正在加载订单详情"
            className="space-y-4"
          >
            <Skeleton className="h-5 w-2/3 rounded-[4px]" />
            <Skeleton className="h-4 w-1/2 rounded-[4px]" />
            <Skeleton className="h-32 w-full rounded-[8px]" />
          </div>
        ) : !order && detail.error ? (
          <div
            role="alert"
            className="rounded-[8px] border border-destructive/25 bg-destructive/5 p-4"
          >
            <p className="flex items-center gap-2 text-sm font-medium">
              <AlertCircle className="size-4 shrink-0 text-destructive" />
              订单详情未能加载
            </p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {detail.error}
            </p>
            <Button
              type="button"
              variant="outline"
              className="mt-4 h-11 rounded-[8px]"
              onClick={() => void detail.reload().catch(() => {})}
            >
              <RefreshCw className="size-4" />
              重新加载
            </Button>
          </div>
        ) : order && presentation ? (
          <>
            {detail.error && (
              <Notice error>
                更新失败：{detail.error}。以下为上次读取的订单详情，请重试核对。
              </Notice>
            )}
            {detail.loading && (
              <p role="status" className="text-xs text-muted-foreground">
                正在更新订单详情…
              </p>
            )}
            <section>
              <OrderSyncBar loading={detail.loading} error={detail.error} snapshot={order} reload={detail.reload} />
              <OrderJourney order={order} audience="provider" />
              <div className="mb-3 flex items-center justify-between gap-3">
                <p className="text-sm text-muted-foreground">
                  {order.customer?.nickname || "老板信息待核对"}
                </p>
                <Status
                  value={order.status}
                  label={orderLifecycle(order).status}
                />
              </div>
              <OrderGamePoster order={order} />
              {(order.required_team_size ?? 1) > 1 && (
                <Badge variant="secondary" className="mt-3">
                  <UsersRound className="size-3" />
                  {order.required_team_size}人组队
                </Badge>
              )}
              <div className="mt-3">
                <DetailGrid
                  items={[
                    {
                      label: "数量",
                      value: `${order.game_rounds} ${order.quantity_unit || "份"}`,
                    },
                    {
                      label: presentation.incomeLabel,
                      value:
                        presentation.displayIncome == null
                          ? "待核对"
                          : `${money(presentation.displayIncome)} ${coinUnit()}`,
                    },
                    {
                      label: "服务时间",
                      value: order.reservation_origin
                        ? `${date(order.reservation_origin.start_time)} — ${date(order.reservation_origin.end_time)}`
                        : "未提供预约时段",
                    },
                    {
                      label: "区服",
                      value: order.game_region || "未提供或当前不可见",
                    },
                    {
                      label: "游戏昵称",
                      value: order.game_nickname || "未提供或当前不可见",
                    },
                    {
                      label: "游戏UID",
                      value: order.game_uid || "未提供或当前不可见",
                    },
                  ]}
                />
              </div>
              {order.fulfillment && (
                <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                  履约目标：{order.fulfillment}
                </p>
              )}
              {order.gameplay_snapshot?.rules && (
                <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                  规则快照：{order.gameplay_snapshot.rules}
                </p>
              )}
              {order.remark && (
                <p className="mt-3 text-sm leading-6 whitespace-pre-wrap text-muted-foreground">
                  {order.remark}
                </p>
              )}
            </section>
            {presentation.zeroIncomeNotice && (
              <Notice>{presentation.zeroIncomeNotice}</Notice>
            )}
            {order.my_assignment_released && (
              <Notice>
                {order.my_assignment_departure === "REJECTED"
                  ? "本人已拒单"
                  : order.my_assignment_departure === "TRANSFERRED"
                    ? "本人已转出本单"
                    : "本人已退出本单"}
                {order.my_assignment_settled
                  ? " · 个人份额已结算，以钱包为准"
                  : " · 个人收益以钱包流水为准"}
              </Notice>
            )}
            {order.team_progress && (
              <section className="rounded-[8px] bg-muted/40 p-3 text-xs leading-5">
                <h3 className="font-medium">
                  {order.team_progress.stage === "ENTRY"
                    ? "入队截图"
                    : "结单截图"}{" "}
                  · {order.team_progress.submitted_count}/
                  {order.team_progress.total_count} 已提交
                </h3>
                {order.team_progress.members.map((row) => (
                  <p key={row.id} className="mt-1 text-muted-foreground">
                    {row.nickname} · {row.submitted ? "已提交" : "等待提交"}
                  </p>
                ))}
              </section>
            )}
            {(order.completion_block_reason || presentation.aftersaleOpen) && (
              <Notice>
                {order.completion_block_reason ||
                  "售后处理中，个人收益结算以订单与钱包记录为准。"}
              </Notice>
            )}
            {presentation.aftersaleOpen && ["IN_SERVICE", "COMPLETED"].includes(order.status) &&
              <NegotiationProposalPanel role="provider" orderId={order.id} />}
            {order.cancel_reason && (
              <p className="rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
                取消/退款原因：{order.cancel_reason}
              </p>
            )}
            {order.refund_summary && (
              <RefundSummary refund={order.refund_summary} />
            )}
            {(order.settlement_status || order.payment_status) && (
              <DetailGrid
                items={[
                  ...(order.payment_status
                    ? [
                        {
                          label: "付款状态",
                          value:
                            {
                              PAID: "已支付",
                              UNPAID: "未支付",
                              REFUNDED: "已退款",
                              PARTIALLY_REFUNDED: "部分退款",
                            }[order.payment_status] || order.payment_status,
                        },
                      ]
                    : []),
                  ...(order.settlement_status
                    ? [
                        {
                          label: "结算状态",
                          value: presentation.settlementLabel,
                        },
                      ]
                    : []),
                ]}
              />
            )}
            {order.report_entry && (
              <p className="text-xs leading-5 text-muted-foreground">
                报单：
                {order.report_entry.available
                  ? "可查看或创建报单"
                  : order.report_entry.reason || "当前不可创建报单"}
              </p>
            )}
            {order.room_required_snapshot === true && <RoomCompletionPanel orderId={order.id} status={order.status} onSaved={detail.reload} />}
            {!!order.my_participation_segments?.length && (
              <section className="space-y-3">
                <h3 className="text-sm font-semibold">我的履约记录</h3>
                <p className="text-xs text-muted-foreground">截图按每次履约保存，报单仍需提交审核。</p>
                {order.my_participation_segments.map((segment, index) => (
                  <article key={segment.id} className="space-y-2 rounded-[8px] border p-3 text-xs">
                    <p className="font-medium">第 {index + 1} 次履约</p>
                    <p className="text-muted-foreground">接入 {date(segment.accepted_at)}{segment.started_at ? ` · 入队 ${date(segment.started_at)}` : " · 尚未入队"}{segment.released_at ? ` · 转出 ${date(segment.released_at)}` : ""}</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1"><p>入队截图</p>{segment.entry_image_url ? <PrivateImage src={segment.entry_image_url} alt={`第 ${index + 1} 次履约入队截图`} /> : <p className="text-muted-foreground">暂无记录</p>}</div>
                      <div className="space-y-1"><p>结单截图</p>{segment.completion_image_url ? <PrivateImage src={segment.completion_image_url} alt={`第 ${index + 1} 次履约结单截图`} /> : <p className="text-muted-foreground">暂无记录</p>}</div>
                    </div>
                    {segment.can_upload_completion && <SegmentCompletionUpload orderId={order.id} segmentId={segment.id} onSaved={detail.reload} />}
                    {segment.segment_report_available && <SegmentReportPanel orderId={order.id} segmentId={segment.id} report={segment.segment_report} onSaved={detail.reload} />}
                  </article>
                ))}
              </section>
            )}
            {!!order.my_applications?.length && (
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">组队报名</h3>
                {order.my_applications.map((row) => (
                  <div
                    key={row.id}
                    className="rounded-[8px] bg-muted/40 p-3 text-xs leading-5"
                  >
                    <p className="font-medium">{members(row)}</p>
                    <p className="mt-1 text-muted-foreground">
                      {applicationStatus(row)}
                    </p>
                  </div>
                ))}
              </section>
            )}
            <DetailGrid
              items={[
                { label: "创建时间", value: date(order.created_at) },
                ...(order.grabbed_at
                  ? [{ label: "接单时间", value: date(order.grabbed_at) }]
                  : []),
                ...(order.in_service_at
                  ? [{ label: "开始服务", value: date(order.in_service_at) }]
                  : []),
                ...(order.completed_at
                  ? [{ label: "完成服务", value: date(order.completed_at) }]
                  : []),
                ...(order.cancelled_at
                  ? [{ label: "取消时间", value: date(order.cancelled_at) }]
                  : []),
                ...(order.refunded_at
                  ? [{ label: "退款时间", value: date(order.refunded_at) }]
                  : []),
                ...(order.settled_at
                  ? [{ label: "结算时间", value: date(order.settled_at) }]
                  : []),
                ...(order.report_ready_at
                  ? [{ label: "报单就绪", value: date(order.report_ready_at) }]
                  : []),
                ...(order.consumption_ready_at
                  ? [
                      {
                        label: "预计可确认消费",
                        value: date(order.consumption_ready_at),
                      },
                    ]
                  : []),
                ...(order.consumption_confirmed_at
                  ? [
                      {
                        label: "已确认消费",
                        value: date(order.consumption_confirmed_at),
                      },
                    ]
                  : []),
              ]}
            />
          </>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            className="mr-auto h-10 rounded-[8px]"
            disabled={detail.loading}
            onClick={() => void detail.reload().catch(() => {})}
          >
            <RefreshCw
              className={`size-4 ${detail.loading ? "animate-spin" : ""}`}
            />
            刷新详情
          </Button>
          <DialogClose asChild>
            <Button
              type="button"
              variant="outline"
              className="h-10 rounded-[8px]"
            >
              返回
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function OrdersPage() {
  const guardView = useViewGuard()
  const session = useSession("provider"),
    [params, setParams] = useSearchParams(),
    [tab, setTab] = useState("pending"),
    [page, setPage] = useState(1),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState(""),
    [pageReady, setPageReady] = useState(false),
    [backgroundLoads, setBackgroundLoads] = useState(0),
    [manualRefreshing, setManualRefreshing] = useState(false),
    [detailId, setDetailId] = useState<number | null>(null),
    focus = params.get("order"),
    [action, setAction] = useState<{
      kind: "grab" | "start" | "transfer_start" | "complete" | "confirm_team" | "withdraw"
      order: Order
      application?: Application
      requestId: string
    } | null>(null),
    [file, setFile] = useState<File | null>(null),
    [recording, setRecording] = useState<number | null>(null),
    [recordingBusy, setRecordingBusy] = useState(false),
    [partner, setPartner] = useState(""),
    [teammates, setTeammates] = useState<
      { id: number; nickname: string; level: string }[]
    >([]),
    [teammateLoading, setTeammateLoading] = useState(false),
    [teammateError, setTeammateError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("")
  const data = useLoad(async (): Promise<OrderPage> => {
    if (focus && /^[1-9]\d*$/.test(focus))
      return {
        list: [await api<Order>(`/orders/orders/${focus}/`)],
        total: 1,
        page: 1,
        page_size: 20,
      }
    if (tab === "pending")
      return {
        list: await api<Order[]>(
          "/orders/orders/?status=pending&role=provider"
        ),
        total: 0,
        page: 1,
        page_size: 20,
      }
    return api(
      `/orders/orders/?status=${tab}&role=provider&page=${page}&page_size=20&q=${encodeURIComponent(query)}`
    )
  }, [focus, tab, page, query])
  useEffect(() => {
    if (data.data != null) setPageReady(true)
  }, [data.data])
  const reloadOrderList = useCallback(async () => {
    setBackgroundLoads((count) => count + 1)
    try {
      await data.reload()
    } finally {
      setBackgroundLoads((count) => Math.max(0, count - 1))
    }
  }, [data.reload])
  const stats = useLoad(() => api<Stats>("/users/provider-stats/"))
  useEffect(() => {
    if (focus || tab === "pending") return
    const timer = window.setTimeout(() => {
      setPage(1)
      setQuery(search.trim())
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search, tab, focus])
  const visibleOrders =
    tab === "pending" && !focus
      ? (data.data?.list || []).filter((order) =>
          [
            order.order_no,
            order.service_name_snapshot,
            order.service?.name,
            order.service?.game_category_name,
            order.customer?.nickname,
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(search.trim().toLocaleLowerCase())
        )
      : data.data?.list || []
  const hasOrderSearch = !focus && Boolean((tab === "pending" ? search : query).trim())
  const inPool = !focus && tab === "pending"
  function selectOrderTab(value: string) {
    setParams({})
    setSearch("")
    setQuery("")
    setPage(1)
    setTab(value)
  }
  useEffect(() => {
    if (!tabs.some((item) => item.id === tab)) {
      setTab("grabbed")
      setPage(1)
    }
  }, [tab])
  const providerProfile = useLoad(() =>
    api<{ is_verified: boolean }>("/users/escorts/me/")
  )
  const [availabilityBusy, setAvailabilityBusy] = useState(false)
  async function setAvailability(next: "AVAILABLE" | "OFFLINE") {
    if (availabilityBusy || busy || stats.loading || stats.error || !stats.data)
      return
    const current = guardView()
    setAvailabilityBusy(true)
    setNotice("")
    try {
      const result = await mutateAndReload({
        role: "provider",
        isCurrent: current,
        mutate: () => updateProviderStatus(next),
        reload: () => Promise.all([stats.reload(), data.reload()]),
      })
      if (result.stale) return
      setNotice(
        result.error
          ? uncertain(result.error)
            ? "接单状态结果未确认，请刷新核对。"
            : message(result.error)
          : result.reloadError
            ? "接单状态已保存，列表未完成刷新，请重试核对。"
            : next === "AVAILABLE"
              ? "已恢复接单"
              : "已暂停接单，已有订单不受影响"
      )
    } catch (cause) {
      if (current()) setNotice(message(cause))
    } finally {
      setAvailabilityBusy(false)
    }
  }
  const upcoming = useLoad(async () => {
    const pages = await Promise.all(
      ["pending", "confirmed"].map((status) =>
        api<{ items: Reservation[] }>(
          `/orders/reservations/?role=provider&status=${status}&sort_start=asc&page_size=2`
        )
      )
    )
    return pages
      .flatMap((page) => page.items)
      .sort(
        (left, right) =>
          new Date(left.start_time).getTime() -
          new Date(right.start_time).getTime()
      )
      .slice(0, 2)
  })
  useRealtime("provider", (event) => {
    if (event.type === "reservation_status_update" && !busy)
      void upcoming.reload().catch(() => {})
  })

  useRefresh(async () => {
    if (!busy) await reloadOrderList()
  }, ["order_status_update", "site_message_update"])
  useEffect(
    () =>
      schedulePendingOrderRefresh(
        "provider",
        data.data?.list || [],
        async () => {
          if (!busy) await reloadOrderList()
        },
        guardView()
      ),
    [data.data, busy, focus, tab, page, query, reloadOrderList, guardView]
  )
  const needsTeam =
    action?.kind === "grab" &&
    action.order.requires_boss_selection &&
    action.order.required_team_size === 2
  function can(order: Order, key: string) {
    return providerOrderActions(order).includes(key)
  }
  function gameVisible(order: Order) {
    return orderBusinessDetails(order, "provider", session.user?.id)
      .showGameAccount
  }
  function reportRelevant(order: Order) {
    return (
      ["GRABBED", "IN_SERVICE", "COMPLETED", "CANCELLED"].includes(
        order.status
      ) &&
      order.my_assignment_departure !== "REJECTED" &&
      !order.is_gift_snapshot &&
      !order.source_order &&
      (order.report_entry?.available ||
        order.my_assignment_released != null ||
        order.can_operate ||
        order.participants?.some(
          (row) => String(row.id) === String(session.user?.id)
        ))
    )
  }
  async function open(
    kind: NonNullable<typeof action>["kind"],
    order: Order,
    application?: Application
  ) {
    if (busy || availabilityBusy || data.loading || data.error) {
      setNotice("请先刷新订单并核对最新状态。")
      return
    }
    setNotice("")
    setFile(null)
    setRecording(null)
    setPartner("")
    setTeammates([])
    setTeammateError("")
    setAction({
      kind,
      order,
      application,
      requestId: requestId(kind === "grab" ? "grab" : "fulfillment"),
    })
    if (
      kind === "grab" &&
      order.requires_boss_selection &&
      order.required_team_size === 2
    ) {
      if (!order.can_team_apply) {
        setNotice("当前订单暂不可组队，请刷新订单或联系客服。")
        setAction(null)
        return
      }
      setTeammateLoading(true)
      try {
        setTeammates(await api(`/orders/orders/${order.id}/teammates/`))
      } catch (cause) {
        setTeammateError(message(cause))
      } finally {
        setTeammateLoading(false)
      }
    }
  }
  async function submit() {
    if (!action || busy || data.loading || data.error) return
    setBusy(true)
    setNotice("")
    const { order, kind } = action
    try {
      let successNotice = ""
      const outcome = await mutateAndReload({
        role: "provider",
        isCurrent: guardView(),
        reload: () => Promise.all([data.reload(), stats.reload()]),
        mutate: async () => {
          if (kind === "grab") {
            await api(`/orders/orders/${order.id}/grab/`, {
              method: "POST",
              data: {
                team_size: needsTeam ? 2 : 1,
                ...(order.audition_recordings_required ? { recording_id: recording } : {}),
                ...(needsTeam ? { partner_id: Number(partner) } : {}),
                client_request_id: action.requestId,
              },
            })
            successNotice = order.requires_boss_selection
              ? "报名已提交，请关注老板选择与队友确认。"
              : "接单请求已处理，请核对最新订单状态。"
          } else if (kind === "confirm_team" || kind === "withdraw") {
            await api(
              `/orders/orders/${order.id}/applications/${action.application?.id}/action/`,
              {
                method: "POST",
                data: {
                  action: kind === "confirm_team" ? "CONFIRM_TEAM" : "WITHDRAW",
                  ...(kind === "confirm_team" && order.audition_recordings_required ? { recording_id: recording } : {}),
                },
              }
            )
            successNotice =
              kind === "confirm_team" ? "组队已确认" : "已撤回报名"
          } else if (kind === "transfer_start") {
            const segments = (order.my_participation_segments || []).filter(
              (row) => row.is_service_transfer && !row.started_at && !row.released_at
            )
            if (segments.length !== 1) throw new Error("转入记录已变化，请刷新订单。")
            const form = new FormData()
            form.append("entry_image", file!)
            form.append("segment_id", String(segments[0].id))
            form.append("client_request_id", action.requestId)
            await api(`/orders/orders/${order.id}/participation-start/`, {
              method: "POST", data: form,
            })
            successNotice = "本次入队已记录。"
          } else {
            const form = new FormData()
            form.append(
              kind === "start" ? "entry_image" : "completion_image",
              file!
            )
            form.append("client_request_id", action.requestId)
            await api(
              `/orders/orders/${order.id}/${kind === "start" ? "start-service" : "complete"}/`,
              { method: "POST", data: form }
            )
            successNotice = "截图已提交，请核对双方进度和订单状态。"
          }
        },
      })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setNotice(successNotice)
      setAction(null)
    } catch (cause) {
      setNotice(
        uncertain(cause)
          ? "操作结果未确认，请刷新订单核对，暂勿重新提交。"
          : message(cause)
      )
      setAction(null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="接单工作台">
        <Button
          variant="outline"
          size="icon"
          className="size-11 rounded-[8px]"
          aria-label="刷新陪玩订单"
          disabled={manualRefreshing || (!pageReady && data.loading)}
          onClick={() => {
            setManualRefreshing(true)
            void Promise.all([
              data.reload(),
              stats.reload(),
              providerProfile.reload(),
            ])
              .catch(() => {})
              .finally(() => setManualRefreshing(false))
          }}
        >
          <RefreshCw
            className={`size-4 ${manualRefreshing || (!pageReady && data.loading) ? "animate-spin" : ""}`}
          />
        </Button>
      </PageHeader>
      <Notice>{notice}</Notice>
      <div className="xa-role-scope"><strong>本人接单与履约</strong><span>客服派单与公开接单统一追踪 · 提交报名后等待分配结果</span></div>
      <OrderSyncBar loading={data.loading} error={data.error} snapshot={data.data} reload={data.reload} />
      <SnapshotGate
        loading={!pageReady && data.loading}
        error={pageReady ? "" : data.error}
        hasData={pageReady || data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        <LoadState
          loading={stats.loading}
          error={stats.error}
          hasData={stats.data != null}
          reload={() => stats.reload().catch(() => {})}
        >
          <section className="provider-workbench-overview overflow-hidden rounded-[12px] border bg-card shadow-sm">
            <div className="flex flex-col gap-5 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
              <div className="flex items-center gap-3">
                <span className="grid size-12 shrink-0 place-items-center rounded-full bg-primary/10 text-lg font-semibold text-primary">
                  {(
                    session.user?.nickname ||
                    session.user?.username ||
                    "陪"
                  ).slice(0, 1)}
                </span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-lg font-semibold">
                      {session.user?.nickname ||
                        session.user?.username ||
                        "陪玩"}
                      ，欢迎回来
                    </h2>
                    {providerProfile.data?.is_verified && (
                      <Badge
                        variant="outline"
                        className="border-success/25 bg-success/10 text-success"
                      >
                        已认证
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    完成率 {stats.data?.completion_rate ?? "—"}% · 评分{" "}
                    {stats.data?.rating_count ? stats.data.rating_avg : "—"}
                    {!!stats.data?.max_concurrent_orders && (
                      <> · 最多同时进行 {stats.data.max_concurrent_orders} 单</>
                    )}
                  </p>
                </div>
              </div>
              <AvailabilityControl
                status={stats.data?.escort_status}
                disabled={busy || stats.loading || !!stats.error}
                busy={availabilityBusy}
                onChange={(next) => void setAvailability(next)}
              />
            </div>
            {stats.data && (
              <section
                className="provider-workbench-metrics grid grid-cols-2 md:grid-cols-4"
                aria-label="接单概况"
              >
                <Stat
                  label="今日接单"
                  value={
                    <>
                      {stats.data.today_orders}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        单
                      </span>
                    </>
                  }
                />
                <Stat
                  label="服务中"
                  value={
                    <>
                      {stats.data.serving}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        单
                      </span>
                    </>
                  }
                />
                <Stat
                  label="完成率"
                  value={
                    <>
                      {stats.data.completion_rate}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        %
                      </span>
                    </>
                  }
                />
                <Stat
                  label="服务及推荐净收益"
                  value={
                    <>
                      {money(stats.data.total_income)}
                      <span className="ml-1 text-xs font-normal text-muted-foreground">
                        {coinUnit()}
                      </span>
                    </>
                  }
                />
              </section>
            )}
          </section>
        </LoadState>
        <section>
          <FilterBar
            ariaLabel="订单状态"
            columns={5}
            items={tabs.map((item) => ({ value: item.id, label: item.label }))}
            value={!focus ? tab : ""}
            onChange={selectOrderTab}
          />
          {!focus && (
            <SearchField
              className="mt-3"
              value={search}
              onValueChange={setSearch}
              placeholder={
                tab === "pending" ? "搜索订单号、服务或老板" : "搜索订单号"
              }
              ariaLabel="搜索陪玩订单"
            />
          )}
        </section>
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          <section aria-label="陪玩订单列表" aria-busy={data.loading}>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">
                  {focus
                    ? "关联订单"
                    : tabs.find((item) => item.id === tab)?.label || "我的订单"}
                </h2>
                {data.data && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {tab === "pending" && !focus
                      ? visibleOrders.length
                      : data.data.total}{" "}
                    笔订单
                  </p>
                )}
              </div>
              <Badge variant="outline">北京时间</Badge>
            </div>
            <LoadState
              hasData={data.data != null}
              loading={
                data.loading && (backgroundLoads === 0 || data.data == null)
              }
              error={data.error}
              reload={() => data.reload().catch(() => {})}
              empty={!visibleOrders.length}
              emptyTitle={hasOrderSearch ? "没有匹配的订单" : inPool ? "暂时没有可接订单" : "当前没有订单"}
              emptyDescription={hasOrderSearch ? "清除搜索词可查看当前状态的订单。" : inPool ? "新派发的订单会出现在这里，也可以先查看已接订单。" : "可以前往抢单池查看可报名或接单的订单。"}
              emptyAction={
                <Button variant="outline" className="h-11" onClick={() => selectOrderTab(hasOrderSearch ? tab : inPool ? "grabbed" : "pending")}>
                  {hasOrderSearch ? "清除搜索词" : inPool ? "查看已接订单" : "前往抢单池"}
                </Button>
              }
            >
              <div className="grid gap-4">
                {visibleOrders.map((order) => (
                  <Panel
                    key={order.id}
                    className="provider-order-card gap-0 shadow-sm"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span
                        className="min-w-0 truncate font-mono text-xs text-muted-foreground"
                        title={order.entity_no || order.order_no}
                      >
                        {order.entity_no || order.order_no}
                      </span>
                      <Status
                        value={order.status}
                        label={orderLifecycle(order).status}
                      />
                    </div>
                    <OrderSource order={order} />
                    <button
                      type="button"
                      className="provider-order-detail-trigger mt-4 flex w-full items-start justify-between gap-4 rounded-[8px] text-left hover:bg-accent/20 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none active:bg-accent/30"
                      onClick={() => setDetailId(order.id)}
                      aria-label={`查看订单 ${order.order_no} 详情`}
                    >
                      <span className="min-w-0">
                        {(order.service?.game_category_name ||
                          (order.required_team_size ?? 1) > 1) && (
                          <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            {order.service?.game_category_name}
                            {(order.required_team_size ?? 1) > 1 && (
                              <Badge variant="secondary">
                                <UsersRound className="size-3" />
                                {order.required_team_size}人组队
                              </Badge>
                            )}
                          </span>
                        )}
                        <span className="mt-1 block text-base leading-6 font-semibold">
                          {order.service_name_snapshot ||
                            order.service?.name ||
                            "陪玩服务"}
                        </span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block text-xs text-muted-foreground">
                          {providerOrderPresentation(order).incomeLabel}
                        </span>
                        <span className="mt-1 block text-xl leading-6 font-semibold tabular-nums">
                          {providerOrderPresentation(order).displayIncome ==
                          null
                            ? "待核对"
                            : money(
                                providerOrderPresentation(order).displayIncome
                              )}
                          <span className="ml-1 text-xs font-normal text-muted-foreground">
                            {providerOrderPresentation(order).displayIncome ==
                            null
                              ? ""
                              : coinText("浪花")}
                          </span>
                        </span>
                      </span>
                    </button>
                    {order.my_assignment_released && (
                      <div className="mt-3">
                        <Notice>
                          {order.my_assignment_departure === "REJECTED"
                            ? "本人已拒单"
                            : order.my_assignment_departure === "TRANSFERRED"
                              ? "本人已转出本单"
                              : "本人已退出本单"}
                          {order.my_assignment_settled
                            ? " · 个人份额已结算，以钱包为准"
                            : " · 个人收益以钱包流水为准"}
                        </Notice>
                      </div>
                    )}
                    <div className="mt-4">
                      <DetailGrid
                        items={[
                          {
                            label: "老板 / 数量",
                            value: `${order.customer?.nickname || "老板"} · ${order.game_rounds} ${order.quantity_unit || "份"}`,
                          },
                          {
                            label: "服务时间",
                            value: order.reservation_origin ? (
                              <>
                                {date(order.reservation_origin.start_time)} —{" "}
                                {date(order.reservation_origin.end_time)}
                                {order.reservation_origin.can_open && (
                                  <Link
                                    className="ml-2 text-primary underline underline-offset-4"
                                    to={
                                      orderBusinessDetails(
                                        order,
                                        "provider",
                                        session.user?.id
                                      ).reservation!.destination
                                    }
                                  >
                                    查看预约
                                  </Link>
                                )}
                              </>
                            ) : (
                              "未提供预约时段"
                            ),
                          },
                          ...((order.status === "PENDING" &&
                            order.game_region) ||
                          gameVisible(order)
                            ? [
                                {
                                  label: "区服",
                                  value:
                                    orderBusinessDetails(
                                      order,
                                      "provider",
                                      session.user?.id
                                    ).gameRegion || "—",
                                },
                              ]
                            : []),
                          ...(gameVisible(order)
                            ? [
                                {
                                  label: "游戏昵称",
                                  value:
                                    orderBusinessDetails(
                                      order,
                                      "provider",
                                      session.user?.id
                                    ).gameNickname || "—",
                                },
                                {
                                  label: "游戏UID",
                                  value:
                                    orderBusinessDetails(
                                      order,
                                      "provider",
                                      session.user?.id
                                    ).gameUid || "—",
                                },
                              ]
                            : []),
                        ]}
                      />
                    </div>
                    {orderBusinessDetails(order, "provider", session.user?.id)
                      .fulfillment && (
                      <p className="mt-3 flex items-start gap-2 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                        <Clock3 className="mt-0.5 size-4 shrink-0" />
                        <span>
                          履约目标：
                          {
                            orderBusinessDetails(
                              order,
                              "provider",
                              session.user?.id
                            ).fulfillment
                          }
                        </span>
                      </p>
                    )}
                    {orderBusinessDetails(order, "provider", session.user?.id)
                      .gameplayRules && (
                      <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
                        规则快照：
                        {
                          orderBusinessDetails(
                            order,
                            "provider",
                            session.user?.id
                          ).gameplayRules
                        }
                      </p>
                    )}
                    {order.team_progress && (
                      <div className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5">
                        <strong>
                          {order.team_progress.stage === "ENTRY"
                            ? "入队截图"
                            : "结单截图"}{" "}
                          · {order.team_progress.submitted_count}/
                          {order.team_progress.total_count} 已提交
                        </strong>
                        {order.team_progress.members.map((row) => (
                          <p
                            key={row.id}
                            className="mt-1 text-muted-foreground"
                          >
                            {row.nickname} ·{" "}
                            {row.submitted ? "已提交" : "等待提交"}
                          </p>
                        ))}
                      </div>
                    )}
                    {(order.completion_block_reason ||
                      providerOrderPresentation(order).aftersaleOpen) && (
                      <div className="mt-3">
                        <Notice>
                          {order.completion_block_reason ||
                            "售后处理中，个人收益结算以订单与钱包记录为准。"}
                        </Notice>
                      </div>
                    )}
                    {orderBusinessDetails(order, "provider", session.user?.id)
                      .cancelReason && (
                      <p className="mt-3 rounded-[8px] bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
                        {
                          orderBusinessDetails(
                            order,
                            "provider",
                            session.user?.id
                          ).cancelReasonLabel
                        }
                        ：
                        {
                          orderBusinessDetails(
                            order,
                            "provider",
                            session.user?.id
                          ).cancelReason
                        }
                      </p>
                    )}
                    {order.refund_summary && (
                      <div className="mt-3">
                        <RefundSummary refund={order.refund_summary} />
                      </div>
                    )}
                    {order.settlement_status && (
                      <p
                        className={`mt-3 rounded-[8px] p-3 text-xs leading-5 ${order.settlement_status === "SETTLED" && order.status !== "CANCELLED" ? "bg-success/5 text-success" : "bg-muted/40 text-muted-foreground"}`}
                      >
                        <CircleDollarSign className="mr-1 inline size-4" />
                        结算状态：
                        {providerOrderPresentation(order).settlementLabel}
                      </p>
                    )}
                    {order.remark && (
                      <p className="mt-3 text-xs leading-5 text-muted-foreground">
                        {order.remark}
                      </p>
                    )}
                    <div
                      className="provider-order-actions mt-4 flex flex-wrap items-center justify-end gap-2 border-t pt-3"
                      data-compact={["PENDING", "GRABBED"].includes(order.status) && !order.my_applications?.some((row) => row.status === "PENDING")}
                      aria-label="订单操作"
                    >
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="provider-order-detail-footer rounded-[6px]"
                        onClick={() => setDetailId(order.id)}
                        aria-label={`订单详情 ${order.order_no}`}
                      >
                        订单详情
                        <ChevronRight className="size-4" />
                      </Button>
                      <Button className="h-11" variant="outline" asChild>
                        <Link to={`/support-chat?order_no=${order.order_no}`}>
                          咨询客服
                        </Link>
                      </Button>
                      {order.report_entry?.available && (
                        <Button className="h-11" variant="outline" asChild>
                          <Link to={providerReportDestination(order)}>
                            查看/创建报单
                          </Link>
                        </Button>
                      )}
                      {order.status === "COMPLETED" && (
                        <Button className="h-11" variant="outline" asChild>
                          <Link to={`/evaluations?order=${order.id}`}>
                            查看评价
                          </Link>
                        </Button>
                      )}
                      {order.my_applications?.some(
                        (row) => row.status === "PENDING"
                      ) ? (
                        order.my_applications
                          .filter((row) => row.status === "PENDING")
                          .map((row) => (
                            <div
                              className="mt-2 w-full space-y-2 border-t pt-3"
                              key={row.id}
                            >
                              <p className="text-sm text-muted-foreground">
                                {members(row)} · {applicationStatus(row)}
                              </p>
                              <div className="flex flex-wrap gap-2">
                                <Button
                                  className="h-11"
                                  variant="outline"
                                  asChild
                                >
                                  <Link
                                    to={`/applications/${order.id}?application=${row.id}`}
                                  >
                                    报名详情
                                  </Link>
                                </Button>
                                {row.is_partner && !row.partner_confirmed && (
                                  <Button
                                    className="h-11"
                                    disabled={busy}
                                    onClick={() =>
                                      void open("confirm_team", order, row)
                                    }
                                  >
                                    确认组队
                                  </Button>
                                )}
                                <Button
                                  className="h-11"
                                  variant="outline"
                                  disabled={busy}
                                  onClick={() =>
                                    void open("withdraw", order, row)
                                  }
                                >
                                  撤回报名
                                </Button>
                              </div>
                            </div>
                          ))
                      ) : (
                        <>
                          {order.status === "PENDING" && can(order, "GRAB") && (
                            <Button
                              className="h-11"
                              disabled={
                                busy ||
                                (!Array.isArray(order.allowed_actions) &&
                                  !!(
                                    stats.data?.max_concurrent_orders &&
                                    stats.data.active_orders >=
                                      stats.data.max_concurrent_orders
                                  ))
                              }
                              onClick={() => void open("grab", order)}
                            >
                              {order.requires_boss_selection
                                ? "报名抢单"
                                : "确认接单"}
                            </Button>
                          )}
                          {order.status === "GRABBED" &&
                            can(order, "START") && (
                              <>
                                <span className="text-xs text-muted-foreground">
                                  无法履约请咨询客服协调
                                </span>
                                <Button
                                  className="h-11"
                                  onClick={() => void open("start", order)}
                                >
                                  开始服务
                                </Button>
                              </>
                            )}
                          {order.status === "IN_SERVICE" &&
                            can(order, "START_PARTICIPATION") && (
                              <Button className="h-11" onClick={() => void open("transfer_start", order)}>
                                确认转入入队
                              </Button>
                            )}
                          {order.status === "IN_SERVICE" &&
                            can(order, "COMPLETE") && (
                              <Button
                                className="h-11"
                                onClick={() => void open("complete", order)}
                              >
                                完成订单
                              </Button>
                            )}
                        </>
                      )}
                    </div>
                    {reportRelevant(order) &&
                      order.report_entry &&
                      !order.report_entry.available && (
                        <p className="mt-3 text-xs leading-5 text-muted-foreground">
                          {order.report_entry.reason}
                        </p>
                      )}
                  </Panel>
                ))}
              </div>
            </LoadState>
            {tab !== "pending" && !focus && (
              <div className="mt-4">
                <Pager
                  page={page}
                  total={data.data?.total || 0}
                  change={setPage}
                  disabled={data.loading}
                />
              </div>
            )}
          </section>
          <aside
            className="space-y-4 lg:sticky lg:top-20"
            aria-label="服务与接单管理"
          >
            <section className="rounded-[12px] border bg-card p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <CalendarClock className="size-4 text-primary" />
                <h2 className="text-sm font-semibold">接下来服务</h2>
              </div>
              <div className="mt-4 space-y-4">
                {upcoming.data && upcoming.error && (
                  <Notice error>
                    {upcoming.error}
                    <Button
                      variant="link"
                      onClick={() => void upcoming.reload().catch(() => {})}
                    >
                      重试预约
                    </Button>
                  </Notice>
                )}
                {upcoming.data && upcoming.loading && (
                  <p role="status" className="text-xs text-muted-foreground">
                    正在更新预约…
                  </p>
                )}

                {upcoming.loading && !upcoming.data ? (
                  <p role="status" className="text-xs text-muted-foreground">
                    正在读取预约…
                  </p>
                ) : upcoming.error && !upcoming.data ? (
                  <Notice error>
                    {upcoming.error}
                    <Button
                      variant="link"
                      onClick={() => void upcoming.reload().catch(() => {})}
                    >
                      重试预约
                    </Button>
                  </Notice>
                ) : !upcoming.data?.length ? (
                  <p className="text-xs leading-5 text-muted-foreground">
                    暂无待服务预约
                  </p>
                ) : (
                  upcoming.data.map((row, index) => (
                    <div key={row.id} className="relative border-l pl-4">
                      <span
                        className={`absolute top-1 -left-[5px] size-2.5 rounded-full border-2 border-card ${index === 0 ? "bg-primary" : "bg-muted-foreground"}`}
                      />
                      <p className="text-xs font-medium text-primary">
                        {date(row.start_time)}
                      </p>
                      <Link
                        to={`/reservations?id=${row.id}`}
                        className="mt-1 block text-sm leading-5 font-medium hover:underline"
                      >
                        {row.service?.name || "预约服务"}
                      </Link>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {row.customer?.name || "老板"} · {row.duration_minutes}{" "}
                        分钟 ·{" "}
                        {row.status === "CONFIRMED" ? "已确认" : "待确认"}
                      </p>
                    </div>
                  ))
                )}
              </div>
              <Button
                variant="ghost"
                className="mt-4 h-11 w-full justify-between rounded-[8px] px-3 text-xs hover:bg-accent hover:text-accent-foreground active:bg-accent/80"
                asChild
              >
                <Link to="/reservations">
                  查看全部档期
                  <ArrowRight className="size-4" />
                </Link>
              </Button>
            </section>
            <section className="rounded-[12px] border bg-card p-4">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-primary" />
                <h2 className="text-sm font-semibold">履约提醒</h2>
              </div>
              <p className="mt-3 text-xs leading-5 text-muted-foreground">
                开始服务前上传入队截图，完成时上传结单截图；战绩凭证在报单中补齐。
              </p>
              <Button variant="outline" className="mt-4 h-11 w-full" asChild>
                <Link to="/reports">
                  <FileCheck2 className="size-4" />
                  核对待提交报单
                </Link>
              </Button>
            </section>
          </aside>
        </div>
      </SnapshotGate>
      {detailId != null && (
        <ProviderOrderDetail
          key={detailId}
          orderId={detailId}
          onClose={() => setDetailId(null)}
        />
      )}
      <Confirm
        open={!!action}
        confirmVariant={
          action?.kind === "withdraw" ? "destructive" : "default"
        }
        icon={
          action?.kind === "complete" ? (
            <CheckCircle2 className="size-5" />
          ) : action?.kind === "start" || action?.kind === "transfer_start" ? (
            <Camera className="size-5" />
          ) : (
            <ClipboardCheck className="size-5" />
          )
        }
        label={
          action?.kind === "grab"
            ? action.order.requires_boss_selection
              ? "提交报名"
              : "确认接单"
            : action?.kind === "transfer_start"
              ? "确认入队"
            : action?.kind === "start"
              ? "确认开始"
              : action?.kind === "complete"
                ? "确认完成"
                : action?.kind === "confirm_team"
                  ? "确认组队"
                  : "确认撤回"
        }
        title={
          action?.kind === "grab"
            ? action.order.requires_boss_selection
              ? "报名抢单"
              : "确认接单"
            : action?.kind === "transfer_start"
              ? "确认转入入队"
            : action?.kind === "start"
              ? "开始服务"
              : action?.kind === "complete"
                ? "完成订单"
                : action?.kind === "confirm_team"
                  ? "确认组队"
                  : "撤回报名"
        }
        description={
          action?.kind === "grab"
            ? action.order.requires_boss_selection
              ? "组队确认且老板选定后才正式接单。"
              : "确认接单后请按要求提交入队凭证。"
            : action?.kind === "transfer_start"
              ? "请上传本次转入后的入队截图，确认本人开始履约。"
              : "请核对订单及本次操作内容。"
        }
        onClose={() => setAction(null)}
        busy={busy}
        disabled={
          teammateLoading ||
          recordingBusy ||
          (!!action?.order.audition_recordings_required && ["grab", "confirm_team"].includes(action.kind) && !recording) ||
          (!!needsTeam && !partner) ||
          (["start", "transfer_start", "complete"].includes(action?.kind || "") && !file)
        }
        onConfirm={() => void submit()}
      >
        {action && (
          <div>
            <h3 className="font-semibold">
              {action.order.service_name_snapshot ||
                action.order.service?.name ||
                "陪玩服务"}
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {action.order.order_no}
            </p>
            <div className="mt-3">
              <DetailGrid
                items={[
                  {
                    label: "数量",
                    value: `${action.order.game_rounds} ${action.order.quantity_unit || "份"}`,
                  },
                  {
                    label: providerOrderPresentation(action.order).incomeLabel,
                    value:
                      providerOrderPresentation(action.order).displayIncome ==
                      null
                        ? "待核对"
                        : `${money(providerOrderPresentation(action.order).displayIncome)} ${coinUnit()}`,
                  },
                ]}
              />
            </div>
          </div>
        )}
        {action && providerOrderPresentation(action.order).zeroIncomeNotice && (
          <Notice>
            {providerOrderPresentation(action.order).zeroIncomeNotice}
          </Notice>
        )}
        {needsTeam && (
          <div className="space-y-2">
            <Field label="选择队友">
              <select
                className={selectClass}
                aria-describedby="provider-order-partner-help"
                value={partner}
                onChange={(e) => setPartner(e.target.value)}
                disabled={teammateLoading}
              >
                <option value="">
                  {teammateLoading ? "正在读取队友…" : "请选择符合资格的队友"}
                </option>
                {teammates.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.nickname} · {row.level}
                  </option>
                ))}
              </select>
            </Field>
            <p id="provider-order-partner-help" role={teammateError ? "alert" : undefined} className="text-xs leading-5 text-muted-foreground">
              {teammateError ? `队友名单读取失败，关闭后重新打开报名可重试。${teammateError}`
                : teammateLoading ? "正在核对本单可选队友。"
                : !teammates.length ? "当前没有符合本单条件的队友，可以返回抢单池查看其他订单。"
                : "选择一位队友。对方确认组队后，等待老板选定。"}
            </p>
          </div>
        )}
        {action?.order.audition_recordings_required && ["grab", "confirm_team"].includes(action.kind) && (
          <VoicePicker key={`${action.order.id}-${action.kind}`} value={recording} onChange={setRecording} onBusyChange={setRecordingBusy} />
        )}
        {["start", "transfer_start", "complete"].includes(action?.kind || "") && (
          <div className="space-y-2">
            <Field label={action?.kind === "complete" ? "结单截图" : "入队截图"}>
              <UploadField
                accept="image/*"
                aria-describedby="provider-order-proof-help"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </Field>
            <p id="provider-order-proof-help" className="text-xs leading-5 text-muted-foreground">
              {action?.kind === "complete" ? "选择本单的结单截图后即可确认完成。" : "选择本次入队的截图后即可确认开始履约。"}
            </p>
          </div>
        )}
      </Confirm>
    </div>
  )
}

export function ApplicationsPage() {
  const [page, setPage] = useState(1),
    data = useLoad(() => loadProviderApplications<Application>(page), [page])
  useRefresh(data.reload, ["order_status_update"])
  return (
    <div className="space-y-6">
      <PageHeader title="我的报名" back="/orders" />
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
        empty={!data.data?.list.length}
        emptyTitle="暂无报名记录"
        emptyDescription="订单报名与队友邀请会在这里显示。"
        emptyAction={<Button variant="outline" className="h-11" asChild><Link to="/orders">前往抢单池</Link></Button>}
      >
        <div className="grid items-start gap-4 md:grid-cols-2">
          {data.data?.list.map((row) => (
            <Panel key={row.id} className="gap-0 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="font-semibold">
                    {row.service_name || "陪玩服务"}
                  </h2>
                  <p className="mt-1 font-mono text-xs break-words text-muted-foreground">
                    {row.order_no}
                  </p>
                </div>
                <Status value={row.status} label={applicationStatus(row)} />
              </div>
              <div className="mt-4">
                <DetailGrid
                  items={[
                    { label: "队伍成员", value: members(row) },
                    { label: "报名时间", value: date(row.created_at) },
                  ]}
                />
              </div>
              <Button
                asChild
                variant="ghost"
                className="mt-4 h-11 w-full justify-end border-t px-3 pt-3 text-xs text-primary active:bg-accent/80"
              >
                <Link
                  to={`/applications/${row.order_id}?application=${row.id}&from=history`}
                >
                  查看报名
                  <ChevronRight className="size-4" />
                </Link>
              </Button>
            </Panel>
          ))}
        </div>
      </LoadState>
      <Pager
        page={page}
        total={data.data?.total || 0}
        size={data.data?.page_size || 20}
        change={setPage}
      />
    </div>
  )
}
type Detail = {
  order: {
    audition_recordings_required?: boolean
    order_no: string
    entity_no?: string
    service_name_snapshot?: string
    service?: { name: string }
    status: string
    reservation_origin?: { start_time: string; end_time: string }
  }
  application: Application
  can_confirm: boolean
  closed_reason?: string
  confirm_reason: string
  selectable?: boolean
  selection_reason?: string
}
export function ApplicationDetailPage() {
  const guardView = useViewGuard()
  const { orderId } = useParams(),
    [params, setParams] = useSearchParams(),
    id = Number(params.get("application")),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [confirm, setConfirm] = useState(false),
    [recording, setRecording] = useState<number | null>(null),
    [recordingBusy, setRecordingBusy] = useState(false),
    data = useLoad(
      async () =>
        id
          ? {
              detail: await api<Detail>(
                `/orders/orders/${orderId}/applications/${id}/mine/`
              ),
              applications: null,
            }
          : {
              detail: null,
              applications: (
                await api<{ applications: Application[] }>(
                  `/orders/orders/${orderId}/applications/mine/`
                )
              ).applications,
            },
      [orderId, id]
    )
  useRefresh(data.reload, ["order_status_update"])
  useEffect(() => {
    setNotice("")
    setConfirm(false)
    setRecording(null)
  }, [orderId, id])
  async function act(action: string) {
    if (busy || data.loading || data.error) {
      setNotice("请先重新读取报名状态。")
      return
    }
    setBusy(true)
    setConfirm(false)
    try {
      const outcome = await mutateAndReload({
        role: "provider",
        isCurrent: guardView(),
        mutate: () =>
          api(`/orders/orders/${orderId}/applications/${id}/action/`, {
            method: "POST",
            data: { action, ...(["CONFIRM_TEAM", "SUBMIT_VOICE"].includes(action) && data.data?.detail?.order.audition_recordings_required ? { recording_id: recording } : {}) },
          }),
        reload: data.reload,
      })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setNotice(action === "CONFIRM_TEAM" ? "组队已确认" : action === "SUBMIT_VOICE" ? "试音录音已提交" : "报名已撤回")
    } catch (cause) {
      setNotice(
        uncertain(cause)
          ? "操作结果未确认，请核对最新报名状态。"
          : message(cause)
      )
    } finally {
      setBusy(false)
    }
  }
  const detail = data.data?.detail
  return (
    <div className="space-y-6">
      <PageHeader
        title="报名详情"
        back={params.get("from") === "history" ? "/applications" : "/messages"}
      />
      <Notice>{notice}</Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
        empty={!detail && !data.data?.applications?.length}
        emptyTitle="未找到该报名"
        emptyDescription="请返回报名列表或消息，重新核对邀请信息。"
        emptyAction={<Button variant="outline" className="h-11" asChild><Link to="/applications">查看我的报名</Link></Button>}
      >
        {data.data?.applications && (
          <div className="mx-auto max-w-2xl space-y-2">
            {data.data.applications.map((row) => (
              <button
                key={row.id}
                type="button"
                className="flex min-h-16 w-full items-center gap-3 rounded-[8px] border bg-card p-3 text-left"
                onClick={() =>
                  setParams((old) => {
                    old.set("application", String(row.id))
                    return old
                  })
                }
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">
                    {members(row)}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {date(row.created_at)}
                  </span>
                </span>
                <Status value={row.status} label={applicationStatus(row)} />
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              </button>
            ))}
          </div>
        )}
        {detail && (
          <Panel className="mx-auto w-full max-w-2xl gap-0">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold">
                  {detail.order.service_name_snapshot ||
                    detail.order.service?.name ||
                    "陪玩服务"}
                </h2>
                <p className="mt-1 font-mono text-xs break-words text-muted-foreground">
                  {detail.order.entity_no || detail.order.order_no}
                </p>
              </div>
              <Status
                value={detail.application.status}
                label={applicationStatus(detail.application)}
              />
            </div>
            <div className="mt-4">
              <DetailGrid
                items={[
                  { label: "队伍成员", value: members(detail.application) },
                  {
                    label: "报名时间",
                    value: date(detail.application.created_at),
                  },
                ]}
              />
            </div>
            <div className="mt-3">
              <Notice>{providerApplicationPresentation(detail).notice}</Notice>
            </div>
            {detail.order.audition_recordings_required && detail.order.status === "PENDING" &&
              detail.application.status === "PENDING" && detail.application.my_recording_unavailable && (
              <p role="alert" className="mt-3 text-sm text-destructive">本人报名录音暂不可用，请撤回报名后选择其他录音或重新上传。</p>
            )}
            {detail.order.reservation_origin && (
              <p className="mt-4 flex items-start gap-2 rounded-[8px] bg-primary/5 p-4 text-sm">
                <CalendarClock className="size-4 shrink-0 text-primary" />
                <span>
                  预约 {date(detail.order.reservation_origin.start_time)} —{" "}
                  {date(detail.order.reservation_origin.end_time)}
                </span>
              </p>
            )}
            {detail.order.audition_recordings_required && detail.order.status === "PENDING" &&
              detail.application.status === "PENDING" && !detail.application.my_recording_submitted &&
              (!detail.application.is_partner || detail.application.partner_confirmed) && (
              <div className="mt-4 space-y-3">
                <p className="text-sm">请补充本人的试音录音，老板试听后才能选择。</p>
                <VoicePicker key={`supplement-${orderId}-${id}`} value={recording} onChange={setRecording} onBusyChange={setRecordingBusy} />
                <Button disabled={busy || recordingBusy || !recording} onClick={() => void act("SUBMIT_VOICE")}>提交试音录音</Button>
              </div>
            )}
            <div className="mt-5 flex flex-wrap justify-end gap-2 border-t pt-4">
              {providerApplicationPresentation(detail).canConfirm && detail.order.audition_recordings_required && (
                <VoicePicker key={`${orderId}-${id}`} value={recording} onChange={setRecording} onBusyChange={setRecordingBusy} />
              )}
              {providerApplicationPresentation(detail).canConfirm && (
                <Button
                  disabled={busy || recordingBusy || (!!detail.order.audition_recordings_required && !recording)}
                  onClick={() => void act("CONFIRM_TEAM")}
                >
                  确认组队
                </Button>
              )}
              {providerApplicationPresentation(detail).canWithdraw && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => setConfirm(true)}
                >
                  {detail.application.is_partner &&
                  !detail.application.partner_confirmed
                    ? "拒绝邀请"
                    : "撤回报名"}
                </Button>
              )}
            </div>
          </Panel>
        )}
      </LoadState>
      <Confirm
        open={confirm}
        title="撤回报名"
        confirmVariant="destructive"
        label="确认撤回"
        description="撤回后不再参与本次报名；请核对订单与队友信息。"
        onClose={() => setConfirm(false)}
        onConfirm={() => void act("WITHDRAW")}
        busy={busy}
      >
        {detail && (
          <DetailGrid
            items={[
              { label: "订单", value: detail.order.entity_no || detail.order.order_no },
              { label: "队伍成员", value: members(detail.application) },
            ]}
          />
        )}
      </Confirm>
    </div>
  )
}

type Reservation = {
  id: number
  reservation_no: string
  status: string
  start_time: string
  end_time: string
  duration_minutes: number
  game_rounds: number
  game_region: string
  game_nickname: string
  game_uid: string
  remark: string
  cancel_reason: string
  reject_reason: string
  customer: { name: string }
  service: { name: string }
  order: number | null
  order_no: string
  allowed_actions: string[]
}
type Cursor = { start_time: string; id: number }
export function ReservationsPage() {
  const guardView = useViewGuard()
  const [params, setParams] = useSearchParams(),
    id = Number(params.get("id")),
    [cursor, setCursor] = useState<Cursor | null>(null),
    [rows, setRows] = useState<Reservation[]>([]),
    [action, setAction] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    data = useLoad(async () => {
      if (id)
        return {
          detail: await api<Reservation>(`/orders/reservations/${id}/`),
          page: null,
        }
      const page = await api<{
        items: Reservation[]
        next_cursor: Cursor | null
      }>(
        `/orders/reservations/?role=provider&cursor=1&page_size=20${cursor ? `&before_time=${encodeURIComponent(cursor.start_time)}&before_id=${cursor.id}` : ""}`
      )
      return { detail: null, page }
    }, [id, cursor])
  useEffect(() => {
    setAction("")
    setReason("")
    setNotice("")
    setCursor(null)
  }, [id])
  useEffect(() => {
    if (data.data?.page)
      setRows((old) =>
        cursor
          ? [
              ...new Map(
                [...old, ...data.data!.page!.items].map((row) => [row.id, row])
              ).values(),
            ]
          : data.data!.page!.items
      )
  }, [data.data])
  useRefresh(async () => {
    if (!busy) {
      setCursor(null)
      await data.reload()
    }
  }, ["reservation_status_update"])
  const detail = data.data?.detail
  async function act() {
    if (
      !detail ||
      busy ||
      data.loading ||
      data.error ||
      !detail.allowed_actions.includes(action)
    )
      return
    setBusy(true)
    try {
      const outcome = await mutateAndReload({
        role: "provider",
        isCurrent: guardView(),
        mutate: () =>
          api(`/orders/reservations/${id}/${action}/`, {
            method: "POST",
            data: { reason: action === "confirm" ? "" : reason.trim() },
          }),
        reload: data.reload,
      })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setNotice("预约已更新")
      setAction("")
    } catch (cause) {
      setNotice(
        uncertain(cause) ? "预约结果未确认，请核对最新状态。" : message(cause)
      )
      setAction("")
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title={id ? "预约详情" : "我的预约"}>
        {id > 0 ? (
          <Button
            variant="outline"
            className="h-11"
            onClick={() => {
              setParams({})
              setCursor(null)
            }}
          >
            <ArrowLeft className="size-4" />
            全部预约
          </Button>
        ) : (
          <Badge variant="outline" className="mt-1">
            北京时间
          </Badge>
        )}
      </PageHeader>
      <Notice>{notice}</Notice>
      <LoadState
        hasData={data.data != null}
        loading={data.loading}
        error={data.error}
        reload={() => data.reload().catch(() => {})}
        empty={!detail && !rows.length}
        emptyTitle="暂无预约"
        emptyDescription="老板预约后，档期与游戏要求会显示在这里。"
        emptyAction={<Button variant="outline" className="h-11" asChild><Link to={id ? "/reservations" : "/orders"}>{id ? "查看我的预约" : "返回接单工作台"}</Link></Button>}
      >
        <div
          className={
            detail
              ? "mx-auto max-w-2xl"
              : "grid items-start gap-4 md:grid-cols-2"
          }
        >
          {(detail ? [detail] : rows).map((row) => {
            const content = (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2
                      className={
                        detail ? "text-lg font-semibold" : "font-semibold"
                      }
                    >
                      {row.service?.name}
                    </h2>
                    <p className="mt-1 font-mono text-xs break-words text-muted-foreground">
                      {row.reservation_no}
                    </p>
                  </div>
                  <Status
                    value={row.status}
                    label={row.status === "PENDING" ? "待确认" : undefined}
                  />
                </div>
                {detail ? (
                  <div className="mt-5 rounded-[8px] bg-primary/5 p-4">
                    <p className="flex items-center gap-2 text-sm font-medium">
                      <CalendarClock className="size-4 shrink-0 text-primary" />
                      {date(row.start_time)} — {date(row.end_time)}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {row.duration_minutes} 分钟 · {row.game_rounds} 份 ·
                      北京时间
                    </p>
                  </div>
                ) : (
                  <>
                    <p className="mt-4 flex items-center gap-2 text-sm">
                      <CalendarClock className="size-4 shrink-0 text-primary" />
                      {date(row.start_time)} — {date(row.end_time)}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {row.duration_minutes} 分钟 · {row.game_rounds} 份 ·{" "}
                      {row.customer?.name}
                    </p>
                  </>
                )}
                {detail ? (
                  <>
                    <div className="mt-4">
                      <DetailGrid
                        items={[
                          { label: "老板", value: row.customer?.name || "—" },
                          { label: "区服", value: row.game_region || "—" },
                          {
                            label: "游戏昵称",
                            value: row.game_nickname || "—",
                          },
                          { label: "游戏UID", value: row.game_uid || "—" },
                        ]}
                      />
                    </div>
                    <p className="mt-4 text-sm leading-6 text-muted-foreground">
                      {row.remark || "无备注"}
                    </p>
                    {(row.cancel_reason || row.reject_reason) && (
                      <Notice>{row.cancel_reason || row.reject_reason}</Notice>
                    )}
                    <div className="mt-5 flex flex-wrap justify-end gap-2 border-t pt-4">
                      {row.allowed_actions
                        .filter((value) =>
                          ["confirm", "reject", "cancel"].includes(value)
                        )
                        .map((value) => (
                          <Button
                            key={value}
                            disabled={busy}
                            variant={
                              value === "confirm" ? "default" : "outline"
                            }
                            onClick={() => {
                              setAction(value)
                              setReason("")
                            }}
                          >
                            {
                              {
                                confirm: "确认预约",
                                reject: "拒绝预约",
                                cancel: "取消预约",
                              }[value]
                            }
                          </Button>
                        ))}
                      {row.order && (
                        <Button variant="outline" asChild>
                          <Link to={`/orders?order=${row.order}`}>
                            查看订单 {row.order_no}
                          </Link>
                        </Button>
                      )}
                    </div>
                  </>
                ) : (
                  <span className="mt-4 flex items-center justify-end border-t pt-3 text-xs font-medium text-primary">
                    查看预约
                    <ChevronRight className="size-4" />
                  </span>
                )}
              </>
            )
            return detail ? (
              <Panel key={row.id} className="gap-0">
                {content}
              </Panel>
            ) : (
              <button
                key={row.id}
                type="button"
                className="w-full rounded-[12px] border bg-card p-4 text-left shadow-sm hover:border-primary/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none active:bg-accent/30 md:p-5"
                onClick={() => setParams({ id: String(row.id) })}
              >
                {content}
              </button>
            )
          })}
        </div>
      </LoadState>
      {!id && data.data?.page?.next_cursor && (
        <Button
          variant="outline"
          disabled={data.loading}
          onClick={() => setCursor(data.data!.page!.next_cursor)}
        >
          加载更多
        </Button>
      )}
      <Confirm
        open={!!action}
        title={
          { confirm: "确认预约", reject: "拒绝预约", cancel: "取消预约" }[
            action
          ] || "处理预约"
        }
        description="请核对预约时段与服务安排。"
        confirmVariant={action === "confirm" ? "default" : "destructive"}
        label={
          action === "confirm"
            ? "确认档期"
            : action === "reject"
              ? "确认拒绝"
              : "确认取消"
        }
        onClose={() => setAction("")}
        onConfirm={() => void act()}
        busy={busy}
        disabled={action !== "confirm" && !reason.trim()}
      >
        {detail && (
          <p className="text-sm">
            {detail.reservation_no} · {date(detail.start_time)}
          </p>
        )}
        {action !== "confirm" && (
          <Field label="原因">
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={200}
            />
          </Field>
        )}
      </Confirm>
    </div>
  )
}
