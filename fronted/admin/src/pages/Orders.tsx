import { AdminFilters } from "@/components/AdminFilters"
import {
  RecordSection as FormSection,
  RecordHeading as PageHeader,
} from "./OrderRecordVisual"
import { coinText, coinUnit, OrderJourney, OrderSource, OrderSyncBar, orderLifecycle } from "@xa/shared"
import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import PrivateImage from "@/components/PrivateImage"
import RoomCompletionRequests from "@/components/RoomCompletionRequests"
import AddonFirstProposal from "@/components/AddonFirstProposal"
import { useEffect, useRef, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"
import {
  Search,
  Clock3,
  Headphones,
  Images,
  UsersRound,
  ChevronRight,
  Copy,
  RefreshCw,
} from "@/components/ui/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DataPageState,
  DataTable,
  Details,
  Feedback,
  Field,
  MutationDialog,
  OptionPicker,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLiveReload,
  useLoad,
  type Row,
} from "@/components/business"
import { errorMessage, request, requestId } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import {
  amountToXaCoin,
  formatDateTime,
  providerNames,
  xaCoinToAmount,
} from "@/lib/format"
import DispatchDialog from "@/pages/OrderDispatch"
import OrderMessages from "@/pages/OrderMessages"
import OrderConfig from "@/pages/OrderConfig"
import RefundDialog from "@/pages/OrderRefund"
import { NegotiationProposalEditor, NegotiationProposalEntry } from "@/pages/NegotiationProposal"
import { NegotiationExecutionDialog, NegotiationExecutionEntry } from "@/pages/NegotiationExecution"
import { ORDER_STATUS, PAYMENT_STATUS } from "@/lib/dict"
import { enumLabel } from "@/lib/labels"

function orderQuery(input: Row) {
  const query = { ...input }
  if (query.order_status) query.status = query.order_status
  if (query.find_order) query.order_no_exact = query.find_order
  if (query.aftersale_status === "OPEN") query.aftersale_open = "1"
  if (query.queue === "paid_pending")
    Object.assign(query, { status: "PENDING", payment_status: "PAID" })
  if (query.queue === "unpaid_pending")
    Object.assign(query, { status: "PENDING", payment_status: "UNPAID" })
  if (query.queue === "staff_dispatch") query.actionable_staff = "1"
  delete query.order_status
  delete query.find_order
  delete query.aftersale_status
  delete query.queue
  delete query.detail
  delete query.dispatch
  return query
}
export default function OrdersPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = useState<Row>(() =>
    Object.fromEntries(searchParams.entries())
  )
  const [draft, setDraft] = useState<Row>(query)
  const filters = orderQuery(query)
  const list = useList("orders", filters)
  const auth = useAdminAuth()
  const listScope = JSON.stringify([auth.revision, filters, list.page, list.pageSize])
  const previousScope = useRef(listScope)
  // useList clears its previous response in an effect. Hide it on the filter-change render too.
  const result = previousScope.current === listScope
    ? list
    : { ...list, data: null, rows: [], loading: true, error: "" }
  useEffect(() => { previousScope.current = listScope }, [listScope])
  const capacity = useLoad(async () => (await request({ url: "/config/provider-capacity/" })).data)
  const grabWindow = useLoad(async () => (await request({ url: "/config/grab-window/" })).data)
  const [detailId, setDetailId] = useState<number | null>(
    Number(searchParams.get("detail")) || null
  )
  const [operation, setOperation] = useState<{
    kind: string
    row?: Row
  } | null>(null)
  useEffect(() => {
    const next = Object.fromEntries(searchParams.entries())
    setQuery(next)
    setDraft(next)
    if (next.detail) setDetailId(Number(next.detail))
  }, [searchParams])
  const quickDispatchRequested = searchParams.get("dispatch") === "quick"
  const canDispatch = auth.hasPerm("order:dispatch")
  useEffect(() => {
    if (quickDispatchRequested && canDispatch)
      setOperation({ kind: "dispatch" })
  }, [quickDispatchRequested, canDispatch])
  useLiveReload(result.reload)
  const closeOperation = () => {
    setOperation(null)
    if (searchParams.has("dispatch")) {
      const next = new URLSearchParams(searchParams)
      next.delete("dispatch")
      setSearchParams(next, { replace: true })
    }
  }
  const afterMutation = () => {
    void result.reload()
    closeOperation()
  }
  const filterCount = Object.entries(draft).filter(
    ([key, value]) =>
      !["keyword", "order_status", "status", "queue", "actionable_staff", "detail", "dispatch", "page", "page_size"].includes(
        key
      ) &&
      value != null &&
      value !== "" &&
      value !== false
  ).length
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="orders"
    >
      {
        <div className="admin-orders-page space-y-4">
          <PageHeader
            title="订单与派单"
            description="客服代下单与老板 H5 下单统一追踪，按本店权限处理派单、履约和售后。"
          >
            <RefreshButton loading={result.loading} reload={result.reload} />
            {capacity.data && <Button size="sm" variant="outline" onClick={() => setOperation({ kind: "capacity" })}>
              接单名额
            </Button>}
            {grabWindow.data && <Button size="sm" variant="outline" onClick={() => setOperation({ kind: "grab" })}>抢单窗口</Button>}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOperation({ kind: "timeout" })}
            >
              <Clock3 className="size-4" />
              待接单时限
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOperation({ kind: "traffic" })}
            >
              峰谷限流
            </Button>
            {auth.hasPerm("order:dispatch") && (
              <Button
                size="sm"
                onClick={() => setOperation({ kind: "dispatch" })}
              >
                <Headphones className="size-4" />
                快捷派单
              </Button>
            )}
          </PageHeader>
          <div className="xa-role-scope"><strong>本店订单统一工作区</strong><span>客服代下单与老板 H5 下单共用订单状态 · 报名不等于接单</span></div>
          {auth.hasPerm("order:view") && auth.hasPerm("support:edit") && <RoomCompletionRequests key={auth.revision} onReviewed={() => void result.reload()} />}
          <OrderSyncBar loading={result.loading} error={result.error} snapshot={result.data} reload={result.reload} />
          <Feedback message={result.error} retry={result.reload} />
          <DataTable snapshotAvailable={!!result.data}
            toolbar={<form onSubmit={(event) => { event.preventDefault(); setQuery({ ...draft }) }}>
              <AdminFilters title="订单筛选" activeCount={filterCount} description={["paid_pending", "unpaid_pending"].includes(draft.queue) ? "当前队列固定订单与支付状态，选择全部订单可解除。" : undefined}
                primary={<>
                  <div className="space-y-2">
                    <Label htmlFor="orders-keyword">订单 / 老板 / 陪玩</Label>
                    <div className="admin-record-search"><Search size={16} /><Input id="orders-keyword" aria-label="搜索订单、老板或陪玩" placeholder="订单号、昵称、账号或手机号" value={draft.keyword || ""} onChange={event => setDraft({ ...draft, keyword: event.target.value })} /></div>
                  </div>
                  <Field disabled={["paid_pending", "unpaid_pending"].includes(draft.queue)} field={{ key: "order_status", label: "订单状态", type: "select", options: [["", "全部状态"], ...Object.entries(ORDER_STATUS).map(([key, value]) => [key, value.label] as [string, string])] }} value={["paid_pending", "unpaid_pending"].includes(draft.queue) ? "PENDING" : draft.order_status || draft.status || ""}
                    onChange={value => setDraft(old => ({ ...old, order_status: value, status: "" }))} />
                  <Field field={{ key: "queue", label: "工作队列", type: "select", options: [["", "全部订单"], ["paid_pending", "已支付待接单"], ["unpaid_pending", "未支付待接单"], ["staff_dispatch", "客服待派"]] }} value={draft.queue || (draft.actionable_staff === "1" ? "staff_dispatch" : "")}
                    onChange={value => setDraft(old => ({ ...old, queue: value, actionable_staff: "" }))} />
                </>}
                actions={<><Button type="submit">查询</Button><Button type="button" variant="ghost" onClick={() => { setDraft({}); setQuery({}) }}>清除条件</Button></>}
              >
                <div className="admin-filter-group">
                  <h3>交易与履约</h3>
                  <div className="admin-filter-fields">
                    <Field disabled={["paid_pending", "unpaid_pending"].includes(draft.queue)} field={{ key: "payment_status", label: "支付状态", type: "select", options: [["", "全部支付状态"], ...Object.entries(PAYMENT_STATUS).map(([key, value]) => [key, value.label] as [string, string])] }} value={draft.queue === "paid_pending" ? "PAID" : draft.queue === "unpaid_pending" ? "UNPAID" : draft.payment_status} onChange={value => setDraft({ ...draft, payment_status: value })} />
                    <Field field={{ key: "dispatch_route", label: "接单路径", type: "select", options: [["", "全部路径"], ["STAFF", "客服派单"], ["PUBLIC", "公开接单"]] }} value={draft.dispatch_route} onChange={value => setDraft({ ...draft, dispatch_route: value })} />
                    <Field field={{ key: "order_kind", label: "订单类型", type: "select", options: [["", "全部类型"], ["SERVICE", "服务单"], ["GIFT", "礼物单"]] }} value={draft.order_kind} onChange={value => setDraft({ ...draft, order_kind: value })} />
                    <Field field={{ key: "aftersale_status", label: "售后状态", type: "select", options: [["", "全部售后状态"], ["OPEN", "处理中"]] }} value={draft.aftersale_status || (draft.aftersale_open === "1" ? "OPEN" : "")} onChange={value => setDraft(old => ({ ...old, aftersale_status: value, aftersale_open: "" }))} />
                    <Field field={{ key: "created_day", label: "下单日期", type: "date" }} value={draft.created_day} onChange={value => setDraft({ ...draft, created_day: value })} />
                    <Field field={{ key: "refund_day", label: "退款日期", type: "date" }} value={draft.refund_day} onChange={value => setDraft({ ...draft, refund_day: value })} />
                  </div>
                </div>
                <div className="admin-filter-group">
                  <h3>精确查找</h3>
                  <div className="admin-filter-fields">
                    {[["order_no", "订单号包含"], ["order_no_exact", "完整订单号"], ["customer_username", "老板账号"], ["customer_nickname", "老板昵称"], ["customer_phone", "老板手机号"], ["customer_account_id", "老板业务账号 ID"]].map(([key, label]) => <Field key={key} field={{ key: `order-filter-${key}`, label }} value={draft[key]} onChange={value => setDraft(old => ({ ...old, [key]: value }))} />)}
                  </div>
                </div>
                <div className="admin-filter-group">
                  <h3>核对标记</h3>
                  <div className="admin-filter-fields">
                    {[["overdue_pending", "已超时待接单"], ["legacy_team", "旧双陪履约待核对"], ["exclude_direct_test", "排除直接支付测试单"]].map(([key, label]) => <label key={key} className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" className="size-4 accent-blue-600" checked={draft[key] === "1"} onChange={event => setDraft(old => ({ ...old, [key]: event.target.checked ? "1" : "" }))} />{label}</label>)}
                  </div>
                </div>
              </AdminFilters>
              {JSON.stringify(draft) !== JSON.stringify(query) && <p role="status" className="admin-record-draft-note">筛选输入已更改，请点击查询更新列表。</p>}
            </form>}
            title="订单列表"
            description={`本页 ${result.rows.length} 笔 · 金额已计优惠，未扣退款`}
            empty="没有匹配的订单"
            emptyDescription="调整关键词或清除筛选后再查看。"
            emptyAction={
              <Button
                variant="outline"
                onClick={() => {
                  setDraft({})
                  setQuery({})
                }}
              >
                清除全部条件
              </Button>
            }
            minWidth={1080}
            rows={result.rows}
            error={result.error}
            retry={result.reload}
            loading={result.loading}
            columns={[
              {
                key: "order_no",
                label: "订单号 / 下单时间",
                className: "min-w-40",
                render: (row) => (
                  <>
                    <button
                      className="font-mono text-xs font-medium text-primary hover:underline"
                      onClick={() => setDetailId(row.id)}
                    >
                      {row.entity_no || row.order_no}
                    </button>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDateTime(row.created_at)}
                    </p>
                  </>
                ),
              },
              {
                key: "customer_name",
                label: "老板",
                className: "min-w-40",
                render: (row) => (
                  <>
                    <p className="whitespace-nowrap">{row.customer_name}</p>
                    <p className="whitespace-nowrap text-xs text-muted-foreground">
                      {row.customer_entity_no || row.customer_username}
                    </p>
                  </>
                ),
              },
              {
                key: "service_name_snapshot",
                label: "服务 / 陪玩",
                className: "min-w-56",
                render: (row) => (
                  <>
                    <p>{row.service_name_snapshot}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {providerNames(row)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {row.game_rounds} 份 · {row.escort_mode_display}
                    </p>
                  </>
                ),
              },
              {
                key: "amount",
                label: "订单金额",
                render: (row) => (
                  <span className="whitespace-nowrap tabular-nums">
                    {amountToXaCoin(row.amount)} {coinText(" 浪花")}
                  </span>
                ),
              },
              {
                key: "status",
                label: "订单状态",
                render: (row) => (
                  <div className="flex flex-col items-start gap-1">
                    <Status value={row.status} label={orderLifecycle(row).status} readState={row.status == null ? "unread" : undefined} />
                  </div>
                ),
              },
              {
                key: "payment_status",
                label: "支付状态",
                render: (row) => (
                  <Status
                    value={row.payment_status}
                    label={row.payment_status_display}
                  />
                ),
              },
              {
                key: "dispatch_route",
                label: "接单路径",
                render: (row) => (
                  <>
                    <OrderSource order={row} />
                    <p className="mt-1 text-xs text-muted-foreground">{orderLifecycle(row).dispatch}</p>
                    {row.auto_cancel_at && row.status === "PENDING" && (
                      <p className="mt-1 text-xs text-warning-foreground dark:text-warning">
                        截止 {formatDateTime(row.auto_cancel_at)}
                      </p>
                    )}
                  </>
                ),
              },
              {
                key: "support_contact_name_snapshot",
                label: "负责客服",
                render: (row) =>
                  row.support_contact_name_snapshot ||
                  row.support_contact?.name ||
                  "—",
              },
              {
                align: "center",
                key: "settlement_status",
                label: "结算 / 售后",
                render: (row) => (
                  <>
                    <Status value={row.settlement_status} />
                    {row.aftersale_status && (
                      <Status value={row.aftersale_status} />
                    )}
                  </>
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) => (
                  <div className="flex max-w-64 flex-wrap gap-1">
                    <Button
                      actionTone="read"
                      size="sm"
                      variant="ghost"
                      onClick={() => setDetailId(row.id)}
                    >
                      详情
                      <ChevronRight className="size-3.5" />
                    </Button>
                    {auth.hasPerm("order:dispatch") &&
                      row.dispatch_assignment_allowed && (
                        <Button
                          actionTone="edit"
                          size="sm"
                          variant="outline"
                          onClick={() => setOperation({ kind: "assign", row })}
                        >
                          派单
                        </Button>
                      )}
                    {auth.hasPerm("order:cancel") &&
                      row.status === "PENDING" && (
                        <Button
                          actionTone="reject"
                          size="sm"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "cancel", row })}
                        >
                          取消
                        </Button>
                      )}
                    {auth.hasPerm("order:refund") &&
                      ["PAID", "PARTIALLY_REFUNDED"].includes(
                        row.payment_status
                      ) && (
                        <Button
                          actionTone="adjust"
                          size="sm"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "refund", row })}
                        >
                          退款
                        </Button>
                      )}
                  </div>
                ),
              },
            ]}
          />
          <Pagination snapshotAvailable={!!result.data}
            page={result.page}
            total={result.total}
            loading={result.loading}
            onChange={result.setPage}
            pageSize={result.pageSize}
            onPageSizeChange={result.setPageSize}
          />
          {detailId && (
            <OrderDetail
              key={detailId}
              id={detailId}
              onClose={() => setDetailId(null)}
              onOperation={(kind, row) => {
                setDetailId(null)
                setOperation({ kind, row })
              }}
            />
          )}
          {operation && ["timeout", "traffic", "capacity", "grab"].includes(operation.kind) && (
            <OrderConfig
              kind={operation.kind as "timeout" | "traffic" | "capacity" | "grab"}
              onClose={() => setOperation(null)}
            />
          )}
          {operation?.kind === "dispatch" && (
            <DispatchDialog
              onClose={closeOperation}
              onSaved={afterMutation}
            />
          )}
          {operation?.kind === "message" && (
            <OrderMessages
              order={operation.row!}
              onClose={() => setOperation(null)}
            />
          )}
          {operation?.kind === "negotiation-case" && (
            <NegotiationCaseDialog order={operation.row!} onClose={() => setOperation(null)} onSaved={afterMutation} />
          )}
          {operation?.kind === "negotiation-proposal" && (
            <NegotiationProposalEditor ticket={operation.row!.ticket} onClose={() => setOperation(null)} onSaved={afterMutation} />
          )}
          {operation?.kind === "negotiation-execution" && (
            <NegotiationExecutionDialog ticket={operation.row!.ticket} proposal={operation.row!.proposal}
              onClose={() => setOperation(null)} onSaved={afterMutation} />
          )}
          {operation?.kind === "assign" && (
            <AssignDialog
              order={operation.row!}
              onClose={() => setOperation(null)}
              onSaved={afterMutation}
            />
          )}
          {operation?.kind === "refund" && (
            <RefundDialog
              order={operation.row!}
              onClose={() => setOperation(null)}
              onSaved={afterMutation}
            />
          )}
          {operation?.kind === "transfer" && (
            <TransferDialog
              order={operation.row!}
              onClose={() => setOperation(null)}
              onSaved={afterMutation}
            />
          )}
          {operation &&
            [
              "cancel",
              "start",
              "complete",
              "enable-team-evidence",
              "resolve",
            ].includes(operation.kind) && (
              <OrderAction
                kind={operation.kind}
                order={operation.row!}
                onClose={() => setOperation(null)}
                onSaved={afterMutation}
              />
            )}
        </div>
      }
    </DataPageState>
  )
}

function SegmentSettlementPlan({ orderId, revision }: { orderId: number; revision: Row }) {
  const [plan, setPlan] = useState<Row | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState("")
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    setPlan(null); setFeedback(""); setBusy(false)
    return () => { generation.current += 1 }
  }, [revision])
  async function load() {
    if (busy) return
    const current = generation.current
    setBusy(true); setFeedback(""); setPlan(null)
    try {
      const data = (await request({ url: `/orders/${orderId}/segment-settlement-plan/` })).data
      if (generation.current === current) setPlan(data)
    }
    catch (error) { if (generation.current === current) setFeedback(errorMessage(error)) }
    finally { if (generation.current === current) setBusy(false) }
  }
  return <div className="space-y-2 rounded-lg border p-3" data-testid="segment-settlement-plan">
    <Button size="sm" variant="outline" disabled={busy} onClick={() => void load()}>{busy ? "正在核对" : plan ? "重新核对逐段收益" : "核对逐段收益"}</Button>
    <p className="text-xs text-muted-foreground">核对结果不代表已结算；资金流水、钱包和退款仍须另行核验。</p>
    {feedback && <p role="status">{feedback}</p>}
    {plan && <div className="space-y-2 text-sm">
      {(plan.segments || []).map((item: Row) => <p key={item.segment_id}>履约段 #{item.segment_id} · 陪玩 #{item.account_id} · 成交基数 {amountToXaCoin(item.basis)} · 抽成 {amountToXaCoin(item.fee)} · 预计收益 {amountToXaCoin(item.income)} {coinUnit()}</p>)}
      <p>预计门店留存 {amountToXaCoin(plan.store_income)} · 推荐佣金 {amountToXaCoin(plan.inviter_income)} · 门店承担固定抽成差额 {amountToXaCoin(plan.fixed_fee_shortfall)} {coinUnit()}（已反映在留存中）</p>
      {(plan.persons || []).map((person: Row) => <p key={person.account_id}>陪玩 #{person.account_id}：{person.blockers?.length ? person.blockers.join("；") : "报单和等待条件已满足"}{person.freeze_until ? ` · 报单冻结参考截止 ${formatDateTime(person.freeze_until)}` : ""}</p>)}
    </div>}
  </div>
}

function SegmentReportReview({ orderId, segmentId, report, canAudit, onSaved }: {
  orderId: number; segmentId: number; report: Row; canAudit: boolean; onSaved: () => Promise<unknown>
}) {
  const [remark, setRemark] = useState("")
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState("")
  const labels: Record<string, string> = { DRAFT: "已撤回，待重新提交", PENDING: "待审核", APPROVED: "审核通过", REJECTED: "已驳回", WITHDRAWN: "已撤回" }
  async function review(action: "approve" | "reject") {
    if (busy || (action === "reject" && remark.trim().length < 2)) return
    setBusy(true); setFeedback("")
    try {
      await request({ url: `/orders/${orderId}/segments/${segmentId}/report-review/`, method: "POST", data: { action, version: report.version, remark } })
      setFeedback("审核已记录，收益仍待结算。")
      try { await onSaved() } catch { setFeedback("审核已记录，刷新失败，请重新加载核对。") }
    } catch (error) { setFeedback(errorMessage(error)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-2 rounded-lg border p-3" data-testid={`segment-report-review-${segmentId}`}>
    <p>履约报单 · 第 {report.version} 次 · <Status value={report.status} label={labels[report.status]} /></p>
    <p className="text-muted-foreground">审核通过不代表收益已结算。</p>
    {report.submission?.review_remark && <p>审核意见：{report.submission.review_remark}</p>}
    {(report.history || []).map((item: Row) => <details key={item.version} open={item.version === report.version}>
      <summary>第 {item.version} 次提交 · <Status value={item.status} label={labels[item.status]} /> · {formatDateTime(item.submitted_at)}</summary>
      {item.review_remark && <p>审核意见：{item.review_remark}</p>}
      <div className="flex flex-wrap gap-3 py-2">
        {item.entry_image_url && <PrivateImage label="查看提交时入队截图" urls={[item.entry_image_url]} />}
        {item.completion_image_url && <PrivateImage label="查看提交时结单截图" urls={[item.completion_image_url]} />}
        {(item.result_images || []).map((image: Row) => <PrivateImage key={image.id} label="查看提交时战绩截图" urls={[image.url]} />)}
      </div>
    </details>)}
    {canAudit && report.status === "PENDING" && <div className="space-y-2">
      <Textarea aria-label="本次报单审核意见" placeholder="例如 信息有误，请重新提交…" value={remark} onChange={event => setRemark(event.target.value)} maxLength={500} />
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => void review("approve")}>审核通过</Button>
        <Button size="sm" variant="outline" disabled={busy || remark.trim().length < 2} onClick={() => void review("reject")}>驳回报单</Button>
      </div>
    </div>}
    {feedback && <p role="status">{feedback}</p>}
  </div>
}

function OrderDetail({
  id,
  onClose,
  onOperation,
}: {
  id: number
  onClose: () => void
  onOperation: (kind: string, row: Row) => void
}) {
  const auth = useAdminAuth()
  const result = useLoad(async () => {
    const [order, logs, aftersales] = await Promise.all([
      request({ url: `/orders/${id}/` }),
      request({ url: `/orders/${id}/logs/` }),
      request({ url: "/aftersales/", params: { order: id } }),
    ])
    return {
      ...order.data,
      logs: logs.data,
      aftersales: aftersales.data.list || [],
    }
  }, [id])
  useLiveReload(result.reload)
  const row = result.data
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value) onClose()
      }}
    >
      <DialogContent className="admin-order-detail-dialog max-h-[92dvh] overflow-y-auto sm:max-w-[1440px]">
        <DialogHeader>
          <DialogTitle>订单详情 {row?.entity_no || row?.order_no || `#${id}`}</DialogTitle>
          <DialogDescription>
            核对金额、参与者、履约凭证及状态流水。
          </DialogDescription>
        </DialogHeader>
        <Feedback message={result.error} retry={result.reload} />
        {result.loading && !row ? (
          <AdminPageSkeleton page="order-detail" />
        ) : (
          row && (
            <div className="space-y-4">
              <div className="flex items-center justify-end">
                <Button
                  variant="outline"
                  disabled={result.loading}
                  onClick={result.reload}
                >
                  <RefreshCw
                    className={
                      result.loading ? "size-4 animate-spin" : "size-4"
                    }
                  />
                  刷新订单
                </Button>
              </div>
              <section className="admin-order-detail-hero">
                <div>
                  <div className="admin-order-hero-badges">
                    <Status value={row.status} label={row.status_display} />
                    <Status value={row.payment_status} />
                    {row.aftersale_status === "OPEN" && (
                      <Status value="OPEN" label="售后处理中" />
                    )}
                  </div>
                  <div className="admin-order-number">
                    <h2>{row.entity_no || row.order_no}</h2>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="复制订单号"
                      onClick={() =>
                        void navigator.clipboard
                          .writeText(row.entity_no || row.order_no)
                          .then(() => toast.success("订单号已复制"))
                          .catch(() =>
                            toast.error("复制失败，请手动复制订单号")
                          )
                      }
                    >
                      <Copy size={16} />
                    </Button>
                  </div>
                  <p>
                    {row.service_name_snapshot} · {row.customer_name}
                  </p>
                </div>
                <div className="admin-order-hero-amount">
                  <p>服务成交金额</p>
                  <strong>
                    {amountToXaCoin(row.amount)}
                    <small>{coinUnit()}</small>
                  </strong>
                  <p>已计优惠，未扣退款</p>
                </div>
              </section>
              <OrderSyncBar loading={result.loading} error={result.error} snapshot={row} reload={result.reload} />
              <OrderJourney order={row} />
              <fieldset
                disabled={result.loading || !!result.error}
                className="admin-order-detail-grid"
              >
                <div className="admin-order-detail-main">
                  {" "}
                  <FormSection title="订单与服务信息">
                    <Details
                      data={row}
                      fields={[
                        ["customer_name", "老板"],
                        ["customer_phone", "联系手机"],
                        ["service_name_snapshot", "服务"],
                        ["game_rounds", "购买数量"],
                        ["escort_mode_display", "陪玩模式"],
                        ["dispatch_route", "接单路径"],
                        ["created_at", "下单时间"],
                        ["auto_cancel_at", "待接单截止"],
                        ["settlement_status", "结算状态"],
                        ["settlement_review_reason", "结算核对说明"],
                      ]}
                    />
                  </FormSection>
                  {row.funding_confirmation && <FormSection title="老板付款选择确认">
                    <p className="whitespace-pre-wrap break-words text-sm">{row.funding_confirmation.confirmation}</p>
                    <p className="mt-2 text-xs text-muted-foreground">记录人账号 {row.funding_confirmation.actor_account_id} · {formatDateTime(row.funding_confirmation.recorded_at)}</p>
                  </FormSection>}
                  <FormSection
                    title="金额与收益"
                    note="成交金额、预计收益与结算状态分别展示"
                  >
                    <div className="admin-order-money-grid">
                      {[
                        ["original_amount", "原价"],
                        ["boss_discount", "老板折扣优惠"],
                        ["promo_discount", "活动优惠"],
                        ["coupon_discount", "优惠券优惠"],
                        [
                          "provider_income",
                          row.payment_status?.includes("REFUND")
                            ? "原预计陪玩收益"
                            : row.settlement_status === "SETTLED"
                              ? "陪玩实得"
                              : "预计陪玩收益",
                        ],
                        ["inviter_commission", "推荐人收益"],
                        ["shop_income", "店铺留存快照"],
                      ].map(([key, label]) => (
                        <div key={key}>
                          <p className="text-xs text-muted-foreground">
                            {label}
                          </p>
                          <p className="mt-1 tabular-nums">
                            {row[key] == null && ["provider_income", "inviter_commission", "shop_income"].includes(key)
                              ? "待核对"
                              : `${amountToXaCoin(row[key])} ${coinText(" 浪花")}`}
                          </p>
                        </div>
                      ))}
                    </div>
                    <p className="mt-3 rounded-lg bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
                      订单净额和店铺留存均不等于利润，退款后按实际追回与冲正流水核对。
                    </p>
                  </FormSection>{" "}
                  {row.refund_summary && (
                    <FormSection
                      title="实际退款与追回"
                      note="以实际退款与冲正流水为准"
                    >
                      <div className="admin-order-money-grid">
                        {[
                          ["累计退款", row.refund_summary.amount],
                          ["原实付金额", row.amount],
                          [
                            "退款后保留金额",
                            row.refund_summary.remaining_amount ??
                              Number(row.amount) -
                                Number(row.refund_summary.amount || 0),
                          ],
                        ].map(([label, value]) => (
                          <div key={String(label)}>
                            <p>{label}</p>
                            <strong>
                              {amountToXaCoin(value)} {coinUnit()}
                            </strong>
                          </div>
                        ))}
                      </div>
                      <dl className="admin-record-definition">
                        <div>
                          <dt>退款笔数</dt>
                          <dd>
                            {row.refund_summary.refund_count ??
                              row.refund_summary.records?.length ??
                              "—"}{" "}
                            笔
                          </dd>
                        </div>
                        {row.refund_summary.recovered_amount != null && (
                          <div>
                            <dt>累计追回收益</dt>
                            <dd>
                              {amountToXaCoin(
                                row.refund_summary.recovered_amount
                              )}{" "}
                              {coinUnit()}
                            </dd>
                          </div>
                        )}
                      </dl>
                      <div className="admin-order-refund-records">
                        {(row.refund_summary.records || []).map(
                          (refund: Row) => (
                            <article key={refund.id}>
                              <div>
                                <strong>
                                  退款 #{refund.id} ·{" "}
                                  {amountToXaCoin(refund.amount)} {coinUnit()}
                                </strong>
                                <time>{formatDateTime(refund.created_at)}</time>
                              </div>
                              <p>{refund.reason || "未填写退款原因"}</p>
                              {refund.recovered_amount != null && (
                                <p>
                                  已追回{" "}
                                  {amountToXaCoin(refund.recovered_amount)}{" "}
                                  {coinUnit()}
                                </p>
                              )}
                            </article>
                          )
                        )}
                      </div>
                    </FormSection>
                  )}
                  {row.team_progress && (
                    <DataTable
                      title={row.team_progress.stage === "ENTRY" ? "入队截图进度" : row.team_progress.stage === "COMPLETION" ? "结单截图进度" : "履约截图进度"}
                      titleIcon={Images}
                      description={`已提交 ${row.team_progress.submitted_count ?? "—"}/${row.team_progress.total_count ?? "—"}`}
                      rows={row.team_progress.members || []}
                      columns={[
                        { key: "nickname", label: "陪玩" },
                        {
                          key: "submitted",
                          label: "提交状态",
                          render: (member) =>
                            <Status value={member.submitted == null ? undefined : member.submitted ? "SUBMITTED" : "WAITING"} label={member.submitted == null ? undefined : member.submitted ? "已提交" : "等待提交"} tone={member.submitted ? "success" : "warning"} />,
                        },
                      ]}
                    />
                  )}
                  {row.application_progress && (
                    <p className="rounded-xl border p-4 text-sm">
                      接单申请 {row.application_progress.total} 组 ·
                      等待搭档确认 {row.application_progress.waiting_partner} 组
                    </p>
                  )}
                  {row.prestart_handoffs?.length > 0 && (
                    <section className="space-y-3 rounded-xl border p-4">
                      <h3 className="font-semibold">服务前更换陪玩记录</h3>
                      {row.prestart_handoffs.map((item: Row) => (
                        <article key={item.id} className="space-y-1 border-t pt-3 text-sm">
                          <p>{item.old_snapshot?.provider_name_snapshot || `陪玩 #${item.old_provider}`} → {item.new_snapshot?.provider_name_snapshot || `陪玩 #${item.new_provider}`}</p>
                          <p className="text-muted-foreground">{formatDateTime(item.created_at)} · {item.operator_name || `员工 #${item.operator}`} · {item.reason}</p>
                          {item.new_snapshot?.legacy_minimum_review && <p className="text-xs text-muted-foreground">历史最低等级核对：{item.new_snapshot.legacy_minimum_review.original_requirement}；按{item.new_snapshot.legacy_minimum_review.choice === "NONE" ? "不限等级" : item.new_snapshot.legacy_minimum_review.level_name}校验。依据：{item.new_snapshot.legacy_minimum_review.evidence}</p>}
                          <p className="text-xs text-muted-foreground">原预期收益 {amountToXaCoin(Number(item.old_snapshot?.provider_income || 0) + Number(item.old_snapshot?.subsidy_amount || 0))} {coinUnit()}；接替预期收益 {amountToXaCoin(Number(item.new_snapshot?.provider_income || 0) + Number(item.new_snapshot?.subsidy_amount || 0))} {coinUnit()}。抽成：{item.new_snapshot?.commission_type === "FIXED" ? `固定 ${amountToXaCoin(item.new_snapshot?.commission_fixed || 0)} ${coinUnit()}` : `${item.new_snapshot?.commission_rate ?? "—"}%`}</p>
                        </article>
                      ))}
                    </section>
                  )}
                  {row.original_attribution?.segments?.length > 0 && <section className="space-y-3 rounded-xl border p-4">
                    <h3 className="font-semibold">履约凭证</h3>
                    {row.original_attribution.segments.map((segment: Row) => <article key={segment.id} className="space-y-2 border-t pt-3 text-sm">
                      <p>陪玩 #{segment.provider} · 履约段 #{segment.id} · 入队 {segment.started_at ? formatDateTime(segment.started_at) : "尚未入队"}{segment.released_at ? ` · 转出 ${formatDateTime(segment.released_at)}` : ""}</p>
                      {auth.hasPerm("report:view") ? <div className="flex flex-wrap gap-3">
                        {segment.entry_image_url ? <PrivateImage label="查看入队截图" urls={[segment.entry_image_url]} /> : <span className="text-muted-foreground">暂无入队截图</span>}
                        {segment.completion_image_url ? <PrivateImage label="查看结单截图" urls={[segment.completion_image_url]} /> : <span className="text-muted-foreground">暂无结单截图</span>}
                      </div> : <p className="text-muted-foreground">查看凭证需要报单查看权限</p>}
                      {segment.segment_report && <SegmentReportReview orderId={id} segmentId={segment.id} report={segment.segment_report} canAudit={auth.hasPerm("report:audit")} onSaved={result.reload} />}
                    </article>)}
                  </section>}
                  {row.original_attribution?.transfers?.length > 0 && <section className="space-y-3 rounded-xl border p-4">
                    <h3 className="font-semibold">转单原价份额记录</h3>
                    {row.original_attribution.transfers.map((item: Row) => <article key={item.id} className="space-y-1 border-t pt-3 text-sm">
                      <p>陪玩 #{item.old_provider} → 陪玩 #{item.new_provider}：原份额 {amountToXaCoin(item.original_share_before)} {coinUnit()}，原陪玩 {amountToXaCoin(item.retained_share)}，接替陪玩 {amountToXaCoin(item.transferred_share)}</p>
                      <p className="text-muted-foreground">{formatDateTime(item.created_at)} · {item.operator_name || `员工 #${item.operator}`} · {item.agreement}</p>
                    </article>)}
                  </section>}
                  {row.original_attribution?.financial_capture && <section className="space-y-3 rounded-xl border p-4">
                    <h3 className="font-semibold">接入时成交与抽成快照</h3>
                    <p className="text-xs text-muted-foreground">本记录用于核对，不代表实际结算或打款。</p>
                    {auth.hasPerm("order:view") && auth.hasPerm("report:view") && row.original_attribution?.segments?.some((segment: Row) => segment.segment_report) && <SegmentSettlementPlan orderId={id} revision={row} />}
                    {row.original_attribution.financial_capture.status === "REVIEW" &&
                      <p className="text-sm">待核对：{row.original_attribution.financial_capture.review_reason}</p>}
                    {row.original_attribution.financial_capture.segments.map((item: Row) => <article key={item.segment} className="space-y-1 border-t pt-3 text-sm">
                      <p>陪玩 #{item.provider} · 履约段 #{item.segment} · 接入成交基数 {amountToXaCoin(item.incoming_basis)} {coinUnit()}</p>
                      <p className="text-muted-foreground">抽成 {item.commission_type === "FIXED" ? `固定 ${amountToXaCoin(item.commission_fixed)} ${coinUnit()}` : `${item.commission_rate}%`} · 接入时预计收益 {amountToXaCoin(item.income_at_entry)} {coinUnit()}{item.retained_basis != null ? ` · 转出后保留基数 ${amountToXaCoin(item.retained_basis)} ${coinUnit()}` : ""}</p>
                    </article>)}
                    {(row.original_attribution.financial_capture.transfers || []).map((item: Row) => <article key={`basis-${item.id}`} className="space-y-1 border-t pt-3 text-sm">
                      <p>陪玩 #{item.old_provider} → 陪玩 #{item.new_provider} · 转单前成交基数 {amountToXaCoin(item.basis_before)} {coinUnit()}</p>
                      <p className="text-muted-foreground">原陪玩保留 {amountToXaCoin(item.retained_basis)}，接替陪玩 {amountToXaCoin(item.transferred_basis)} {coinUnit()}；{row.original_attribution.financial_capture.segments.some((segment: Row) => segment.segment === item.new_segment) ? "逐段结算待核对。" : "接替抽成和结算待核对。"}</p>
                      {item.fixed_fee_store_shortfall > 0 && <p className="text-muted-foreground">门店承担固定抽成差额 {amountToXaCoin(item.fixed_fee_store_shortfall)} {coinUnit()}，尚未结算。</p>}
                    </article>)}
                  </section>}
                  {(row.provider_requirement?.label ||
                    row.gameplay_snapshot?.name ||
                    row.roulette_result?.name) && (
                    <section className="space-y-3 rounded-xl border p-4">
                      <h3 className="font-semibold">玩法与履约快照</h3>
                      {row.provider_requirement?.label && (
                        <p className="text-sm">
                          接单范围：{row.provider_requirement.label}
                        </p>
                      )}
                      {row.gameplay_snapshot?.name && (
                        <p className="text-sm">
                          {row.gameplay_snapshot.name} ·{" "}
                          {row.gameplay_snapshot.platform_name} ·{" "}
                          {row.gameplay_snapshot.security_level_name}
                        </p>
                      )}
                      {row.roulette_result?.name && (
                        <p className="text-sm">
                          转盘目标：{row.roulette_result.name} ·{" "}
                          {row.roulette_result.description}
                        </p>
                      )}
                      {row.gameplay_snapshot?.rules && (
                        <p className="text-sm leading-6 whitespace-pre-wrap">
                          {row.gameplay_snapshot.rules}
                        </p>
                      )}
                    </section>
                  )}
                  <DataTable
                    title="履约陪玩"
                    titleIcon={UsersRound}
                    rows={row.providers || []}
                    columns={[
                      { key: "provider_name", label: "陪玩" },
                      { key: "provider_no", label: "编号" },
                      { key: "status", label: "履约状态", render: (person) => <Status value={person.status} /> },
                      {
                        align: "right",
                        key: "provider_income",
                        label: "分账收益",
                        render: (person) =>
                          person.provider_income == null
                            ? "待核对" : `${amountToXaCoin(person.provider_income)} ${coinUnit()}`,
                      },
                      {
                        align: "right",
                        key: "expected_income",
                        label: "预计收益",
                        render: (person) =>
                          person.expected_income == null
                            ? "待核对" : `${amountToXaCoin(person.expected_income)} ${coinUnit()}`,
                      },
                    ]}
                  />
                  {row.service_action_block_reason && (
                    <Feedback message={row.service_action_block_reason} />
                  )}
                  <FormSection
                    title="售后记录"
                    note="处理完成只结束售后，不会自动退款"
                  >
                    {auth.hasPerm("support:edit") && row.status === "IN_SERVICE" &&
                      (row.payment_status === "PAID" || row.negotiation_continuation_supported === true &&
                        ["PARTIALLY_REFUNDED", "REFUNDED"].includes(row.payment_status)) &&
                      !row.is_gift_snapshot && !(row.aftersales || []).some((ticket: Row) => ticket.status === "OPEN") &&
                      <Button size="sm" variant="outline" onClick={() => onOperation("negotiation-case", row)}>登记协商售后</Button>}
                    <div className="admin-order-aftersales">
                      {(row.aftersales || []).map((ticket: Row) => (
                        <article key={ticket.id}>
                          <div>
                            <h4>售后 #{ticket.id}</h4>
                            <Status value={ticket.status} />
                          </div>
                          <p className="text-xs text-muted-foreground">
                            申请于 {formatDateTime(ticket.created_at)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            售后范围：{ticket.affected_provider_account_ids?.length
                              ? (ticket.affected_provider_names || []).join("、") || "陪玩信息待核对"
                              : "整单"}
                          </p>
                          <div className="admin-order-aftersale-reason">
                            <p>申请说明</p>
                            <strong>{ticket.reason || "未填写说明"}</strong>
                            {ticket.resolution && (
                              <p className="mt-3 border-t pt-3">
                                处理说明：{ticket.resolution}
                              </p>
                            )}
                          </div>
                          <div className="mt-4 flex flex-wrap gap-2">
                            {ticket.status === "OPEN" ? (
                              <>
                                {ticket.addon_payment == null && auth.hasPerm("support:edit") && (row.status === "IN_SERVICE" ||
                                  (row.status === "COMPLETED" && (!!ticket.completed_followup_supported ||
                                   (!!ticket.created_at && !!row.completed_at &&
                                    Date.parse(ticket.created_at) <= Date.parse(row.completed_at))))) && !!ticket.affected_provider_account_ids?.length &&
                                  <NegotiationProposalEntry ticket={ticket} open={() => onOperation("negotiation-proposal", {...row, ticket})} />}
                                {ticket.addon_payment == null && auth.hasPerm("order:refund") && !ticket.affected_provider_account_ids?.length && (
                                  <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={() =>
                                      onOperation("refund", {
                                        ...row,
                                        ticketId: ticket.id,
                                      })
                                    }
                                  >
                                    退款并结案
                                  </Button>
                                )}
                                {auth.hasPerm("order:settle") && (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() =>
                                      onOperation("resolve", {
                                        ...row,
                                        ticketId: ticket.id,
                                      })
                                    }
                                  >
                                    处理完成
                                  </Button>
                                )}
                              </>
                            ) : (
                              auth.hasPerm("salary:edit") && (
                                <Button size="sm" variant="outline" asChild>
                                  <Link
                                    to={`/salary-activities?kind=AFTERSALE&source_id=${ticket.id}`}
                                  >
                                    核实售后计次
                                  </Link>
                                </Button>
                              )
                            )}
                            {ticket.addon_payment != null && (auth.hasPerm("order:view") || auth.hasPerm("order:refund")) && <AddonFirstProposal key={`${auth.revision}:${row.id}:${ticket.id}:${ticket.client_request_id}`} orderId={row.id} ticket={ticket} />}
                            {ticket.addon_payment == null && !!ticket.affected_provider_account_ids?.length && auth.hasPerm("order:refund") &&
                              <NegotiationExecutionEntry ticket={ticket} open={proposal => onOperation("negotiation-execution", {...row, ticket, proposal})} />}
                          </div>
                        </article>
                      ))}
                    </div>
                    {!(row.aftersales || []).length && (
                      <p className="text-sm text-muted-foreground">
                        暂无售后记录。
                      </p>
                    )}
                  </FormSection>
                  <FormSection title="状态流水" note="订单操作与状态变化记录">
                    <ol className="admin-record-timeline">
                      {(row.logs || []).map((log: Row, index: number) => (
                        <li key={log.id || index}>
                          <div>
                            <strong>
                              {log.action_display || log.action || "状态更新"}
                            </strong>
                            <p>
                              {[
                                log.from_status
                                  ? enumLabel(log.from_status)
                                  : "",
                                log.to_status
                                  ? `→ ${enumLabel(log.to_status)}`
                                  : "",
                                log.operator_name,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </p>
                            {log.reason && <p>{log.reason}</p>}
                          </div>
                          <time>{formatDateTime(log.created_at)}</time>
                        </li>
                      ))}
                    </ol>
                    {!(row.logs || []).length && (
                      <p className="text-sm text-muted-foreground">
                        暂无状态流水。
                      </p>
                    )}
                  </FormSection>
                </div>{" "}
                <aside className="admin-order-detail-aside">
                  <FormSection
                    title="当前可执行操作"
                    note="提交前重新核对订单与支付状态"
                  >
                    {" "}
                    <div className="flex flex-wrap gap-2">
                      {auth.hasPerm("salary:edit") &&
                        row.dispatch_route === "STAFF" &&
                        ["PAID", "PARTIALLY_REFUNDED"].includes(
                          row.payment_status
                        ) && (
                          <Button size="sm" variant="outline" asChild>
                            <Link
                              to={`/salary-activities?kind=DISPATCH&source_id=${id}`}
                            >
                              核实派单业绩
                            </Link>
                          </Button>
                        )}
                      {auth.hasPerm("order:dispatch") &&
                        row.escort_mode === "DOUBLE" &&
                        row.requires_boss_selection === false &&
                        row.status === "GRABBED" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              onOperation("enable-team-evidence", row)
                            }
                          >
                            启用双方凭证流程
                          </Button>
                        )}
                      {auth.hasPerm("order:dispatch") && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => onOperation("message", row)}
                        >
                          一键发单
                        </Button>
                      )}
                      {auth.hasPerm("order:dispatch") &&
                        row.dispatch_assignment_allowed && (
                          <Button
                            size="sm"
                            onClick={() => onOperation("assign", row)}
                          >
                            指派陪玩
                          </Button>
                        )}
                      {auth.hasPerm("order:dispatch") &&
                        (row.status === "IN_SERVICE" || row.prestart_handoff_available) &&
                        !row.service_action_block_reason && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onOperation("transfer", row)}
                          >
                            转单
                          </Button>
                        )}
                      {auth.hasPerm("order:dispatch") &&
                        row.status === "GRABBED" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onOperation("start", row)}
                          >
                            开始服务
                          </Button>
                        )}
                      {auth.hasPerm("order:settle") &&
                        row.status === "IN_SERVICE" &&
                        !row.service_action_block_reason && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onOperation("complete", row)}
                          >
                            完成服务
                          </Button>
                        )}
                      {auth.hasPerm("order:cancel") &&
                        row.status === "PENDING" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onOperation("cancel", row)}
                          >
                            取消订单
                          </Button>
                        )}
                      {auth.hasPerm("order:refund") &&
                        ["PAID", "PARTIALLY_REFUNDED"].includes(
                          row.payment_status
                        ) && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => onOperation("refund", row)}
                          >
                            退款试算
                          </Button>
                        )}
                    </div>
                  </FormSection>
                  <FormSection title="游戏资料">
                    <Details
                      data={row}
                      fields={[
                        ["game_region", "区服"],
                        ["game_nickname", "游戏昵称"],
                        ["game_uid", "游戏 ID"],
                      ]}
                    />
                  </FormSection>
                  <FormSection title="备注与时间">
                    <Details
                      data={row}
                      fields={[
                        ["remark", "订单备注"],
                        ["created_at", "下单时间"],
                        ["aftersale_deadline", "售后截止"],
                        ["consumption_confirmed_at", "实销确认"],
                      ]}
                    />
                  </FormSection>
                  <FormSection title="负责客服">
                    <p>
                      {row.support_contact_name_snapshot ||
                        row.support_contact?.name ||
                        "未记录客服"}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      下单时间 {formatDateTime(row.created_at)}
                    </p>
                  </FormSection>
                </aside>
              </fieldset>
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  )
}

function NegotiationCaseDialog({order, onClose, onSaved}: {order: Row, onClose: () => void, onSaved: () => void}) {
  const providers: Row[] = order.providers || []
  const active: Row[] = providers.filter((person: Row) => !person.released_at)
  const candidates = providers.length ? active : order.provider ? [{provider: order.provider, provider_name: order.provider_name}] : []
  const valid = candidates.length > 0 && candidates.every(person => Number.isSafeInteger(Number(person.provider)) && Number(person.provider) > 0)
  const [selected, setSelected] = useState<number[]>([])
  const [reason, setReason] = useState("")
  const [unknown, setUnknown] = useState(false)
  const [id] = useState(() => requestId("staff-aftersale"))
  return <MutationDialog title="登记协商售后" description="依据老板的咨询登记受影响陪玩与问题。此操作不代表双方同意，也不会退款；后续处理以系统提示为准。"
    onClose={onClose} disabled={unknown || !valid || !selected.length || reason.trim().length < 2}
    onSubmit={async () => {
      try {
        const result = await request({url: "/aftersales/open-negotiation/", method: "POST", data: {
          order_id: order.id, reason: reason.trim(), affected_provider_account_ids: selected, client_request_id: id,
        }})
        toast.success(result.msg || "售后申请已登记")
        onSaved()
      } catch (error: any) {
        if (!error.status || error.status >= 500) setUnknown(true)
        throw error
      }
    }}>
    {!valid && <Feedback message="当前陪玩身份不完整，请返回订单刷新核对。" />}
    <fieldset className="space-y-2"><legend className="text-sm font-medium">受影响陪玩</legend>
      {candidates.map(person => <label key={person.provider} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={selected.includes(Number(person.provider))} disabled={!valid}
          onChange={event => setSelected(previous => event.target.checked
            ? [...previous, Number(person.provider)] : previous.filter(id => id !== Number(person.provider)))} />
        {person.provider_name || person.provider_name_snapshot || "陪玩信息待核对"}
      </label>)}
    </fieldset>
    <Field field={{key:"negotiation-reason", label:"老板咨询说明", type:"textarea", maxLength:1000, required:true}}
      value={reason} onChange={setReason} />
    {unknown && <Feedback message="登记结果未确认，请关闭后刷新订单售后记录，核对后再操作。" />}
  </MutationDialog>
}

function AssignDialog({
  order,
  onClose,
  onSaved,
}: {
  order: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [selected, setSelected] = useState<number[]>([])
  const required = Number(
    order.required_team_size ||
      order.required_provider_count ||
      (order.escort_mode === "DOUBLE" ? 2 : 1)
  )
  return (
    <MutationDialog
      title="指派陪玩"
      contentClassName="admin-order-assign-dialog sm:max-w-xl"
      description={`${(order.entity_no || order.order_no)} · ${order.service_name_snapshot} · 需 ${required} 名陪玩。提交时将重新核验订单状态与陪玩资格。`}
      onClose={onClose}
      submitText="确认指派"
      disabled={selected.length !== required}
      onSubmit={async () => {
        const latest = (await request({ url: `/orders/${order.id}/` })).data
        if (!latest.dispatch_assignment_allowed)
          throw new Error("订单已变化，请刷新后核对。")
        await request({
          url: `/orders/${order.id}/assign/`,
          method: "POST",
          data:
            required > 1
              ? { provider_ids: selected }
              : { provider_id: selected[0] },
        })
        toast.success("已指派")
        onSaved()
      }}
    >
      <OptionPicker
        resource="escorts"
        label="选择符合资格的陪玩"
        keyField="user"
        multiple
        value={selected}
        query={{
          service_id: order.service_item || order.service,
          provider_gender: order.provider_gender,
        }}
        onChange={(value) => setSelected(value)}
      />
      <p className="text-sm text-muted-foreground">
        已选择 {selected.length} / {required} 名
      </p>
    </MutationDialog>
  )
}
function OrderAction({
  kind,
  order,
  onClose,
  onSaved,
}: {
  kind: string
  order: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [reason, setReason] = useState("")
  const title = (
    {
      cancel: "取消订单",
      start: "开始服务",
      complete: "完成服务",
      "enable-team-evidence": "启用逐人凭证",
      resolve: "处理售后结案",
    } as Row
  )[kind]
  return (
    <MutationDialog
      title={title}
      contentClassName="admin-order-action-dialog sm:max-w-md"
      submitVariant={kind === "cancel" ? "destructive" : "default"}
      description={`${(order.entity_no || order.order_no)} · ${order.service_name_snapshot} · ${amountToXaCoin(order.amount)} ${coinUnit()}。请核对订单后确认。`}
      onClose={onClose}
      disabled={["cancel", "resolve"].includes(kind) && !reason.trim()}
      submitText={`确认${title}`}
      onSubmit={async () => {
        await request({
          url:
            kind === "resolve"
              ? `/aftersales/${order.ticketId}/resolve/`
              : `/orders/${order.id}/${kind}/`,
          method: "POST",
          data:
            kind === "resolve"
              ? { resolution: reason.trim() }
              : kind === "enable-team-evidence"
                ? { confirmed: true }
                : { reason: reason.trim() },
        })
        toast.success(`${title}已提交`)
        onSaved()
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="order-action-reason">
          {kind === "resolve" ? "处理说明" : "操作说明"}
          {["cancel", "resolve"].includes(kind) ? "（必填）" : "（选填）"}
        </Label>
        <Textarea
          id="order-action-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={255}
        />
      </div>
      {kind === "resolve" && (
        <p className="text-sm text-muted-foreground">
          此操作记录处理说明并结案；需要退款时请先使用退款试算入口。
        </p>
      )}
    </MutationDialog>
  )
}
function TransferDialog({
  order,
  onClose,
  onSaved,
}: {
  order: Row
  onClose: () => void
  onSaved: () => void
}) {
  const beforeStart = order.status === "GRABBED"
  const [rows, setRows] = useState<Row[]>(() =>
    (order.providers?.length
      ? order.providers.filter(
          (person: Row) =>
            person.provider && !person.settled_at && !person.released_at
        )
      : [
          {
            provider: order.provider,
            provider_name: order.provider_name,
            provider_income: order.provider_income,
          },
        ]
    ).map((person: Row) => ({
      ...person,
      enabled:
        (order.providers?.filter(
          (person: Row) =>
            person.provider && !person.settled_at && !person.released_at
        ).length || 1) === 1,
      new_provider_id: null,
      split_type: "PERCENT",
      split_value: 0,
      penalty: 0,
      original_segment: order.original_attribution?.segments?.find((segment: Row) =>
        segment.provider === person.provider && !segment.released_at),
      original_retained: "",
      original_transferred: "",
      original_agreement: "",
    }))
  )
  const [reason, setReason] = useState("")
  const [minimumChoice, setMinimumChoice] = useState("")
  const [minimumLevel, setMinimumLevel] = useState("")
  const [originalRequirement, setOriginalRequirement] = useState("")
  const [minimumEvidence, setMinimumEvidence] = useState("")
  const needsMinimumReview = beforeStart && !!order.prestart_legacy_minimum?.required
  const needsOriginalAgreement = !beforeStart && order.original_attribution?.status === "ACTIVE"
  const segmentCapture = !beforeStart && (!!order.original_attribution?.financial_capture || order.provider_income == null)
  const [reviewed, setReviewed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const selected = rows.filter((row) => row.enabled)
  function change(index: number, patch: Row) {
    setRows((previous) =>
      previous.map((row, i) => (i === index ? { ...row, ...patch } : row))
    )
    setReviewed(false)
  }
  return (
    <MutationDialog
      contentClassName="sm:max-w-4xl"
      title={beforeStart ? "开始服务前更换陪玩" : "转单与收益分配"}
      description={beforeStart ? `${order.entity_no || order.order_no} · 保留原付款，新陪玩按接入时门店规则计算收益。` : `${(order.entity_no || order.order_no)} · 转出原陪玩并保留其已约定收益，新陪玩承接剩余份额。`}
      onClose={onClose}
      submitText="确认转单"
      disabled={
        !selected.length ||
        !reviewed ||
        (beforeStart && !reason.trim()) ||
        (needsMinimumReview && (!minimumChoice || (minimumChoice === "LEVEL" && !minimumLevel) || originalRequirement.trim().length < 2 || minimumEvidence.trim().length < 3)) ||
        (needsOriginalAgreement && selected.some((row) => !row.original_segment || row.original_retained === "" || row.original_transferred === "" || row.original_agreement.trim().length < 3)) ||
        unknown ||
        selected.some(
          (row) => !row.new_provider_id || row.new_provider_id === row.provider
        )
      }
      onSubmit={async () => {
        if (
          selected.some((row) =>
            row.split_type === "PERCENT"
              ? !Number.isInteger(row.split_value) ||
                row.split_value < 0 ||
                row.split_value > 100
              : !Number.isFinite(Number(row.split_value)) ||
                row.split_value < 0 ||
                Number(amountToXaCoin(xaCoinToAmount(row.split_value))) !==
                  Number(row.split_value)
          )
        )
          throw new Error(
            coinText(
              "保留比例须为 0–100 整数；固定金额须为非负浪花，最多一位小数。"
            )
          )
        if (
          selected.some(
            (row) =>
              !Number.isFinite(Number(row.penalty)) ||
              row.penalty < 0 ||
              Number(amountToXaCoin(xaCoinToAmount(row.penalty))) !==
                Number(row.penalty)
          )
        )
          throw new Error(coinText("处罚金额须为非负浪花，最多一位小数。"))
        const latest = (await request({ url: `/orders/${order.id}/` })).data
        if (beforeStart && !!latest.prestart_legacy_minimum?.required !== needsMinimumReview)
          throw new Error("历史等级核对状态已变化，请重新打开订单核对。")
        if (needsOriginalAgreement && (latest.original_attribution?.status !== "ACTIVE" || selected.some((row) => {
          const current = latest.original_attribution?.segments?.find((segment: Row) => segment.id === row.original_segment?.id && !segment.released_at)
          return !current || current.original_share !== row.original_segment.original_share || current.started_at !== row.original_segment.started_at
        }))) throw new Error("原价份额或履约状态已变化，请重新打开订单核对。")
        if (needsOriginalAgreement && selected.some((row) => {
          const retained = Number(row.original_retained), transferred = Number(row.original_transferred)
          return !Number.isFinite(retained) || !Number.isFinite(transferred) || retained < 0 || transferred < 0 ||
            Number(amountToXaCoin(xaCoinToAmount(retained))) !== retained || Number(amountToXaCoin(xaCoinToAmount(transferred))) !== transferred ||
            xaCoinToAmount(retained) + xaCoinToAmount(transferred) !== row.original_segment.original_share
        })) throw new Error("双方原价份额须为非负数，最多一位小数，且合计等于原份额。")
        if (
          latest.status !== (beforeStart ? "GRABBED" : "IN_SERVICE") ||
          (beforeStart && !latest.prestart_handoff_available) ||
          latest.service_action_block_reason
        )
          throw new Error(
            latest.service_action_block_reason ||
              "订单状态已变化，请重新打开订单核对。"
          )
        if (
          selected.some((row) => {
            const person = latest.providers?.find(
              (item: Row) =>
                item.provider === row.provider &&
                !item.settled_at &&
                !item.released_at
            ) || (beforeStart && !latest.providers?.length && latest.provider === row.provider
              ? { provider: latest.provider, provider_income: latest.provider_income } : null)
            return (
              !person ||
              Number(
                person.transferable_income ??
                  person.expected_income ??
                  person.provider_income
              ) !==
                Number(
                  row.transferable_income ??
                    row.expected_income ??
                    row.provider_income
                )
            )
          })
        )
          throw new Error(
            "陪玩人员或可分配收益已变化，请重新打开转单窗口核对。"
          )
        try {
          await request({
            url: `/orders/${order.id}/transfer/`,
            method: "POST",
            data: {
              reason: reason.trim(),
              ...(needsMinimumReview ? { legacy_minimum_review: {
                choice: minimumChoice,
                level_id: minimumChoice === "LEVEL" ? Number(minimumLevel) : null,
                original_requirement: originalRequirement.trim(),
                evidence: minimumEvidence.trim(),
              } } : {}),
              transfers: selected.map((row) => ({
                old_provider_id: row.provider,
                new_provider_id: row.new_provider_id,
                split_type: row.split_type,
                split_value:
                  row.split_type === "FIXED"
                    ? xaCoinToAmount(row.split_value)
                    : row.split_value,
                penalty_amount: xaCoinToAmount(row.penalty),
                ...(needsOriginalAgreement ? {original_share: {
                  retained: xaCoinToAmount(row.original_retained), transferred: xaCoinToAmount(row.original_transferred),
                  agreement: row.original_agreement.trim(),
                }} : {}),
              })),
            },
          })
          toast.success("转单已完成")
          onSaved()
        } catch (cause: any) {
          if (!cause.status || cause.status >= 500) setUnknown(true)
          throw cause
        }
      }}
    >
      <div className="space-y-4">
        {rows.map((row, index) => (
          <section
            key={row.provider}
            className="space-y-4 rounded-xl border bg-muted/15 p-4"
          >
            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                checked={row.enabled}
                onChange={(event) =>
                  change(index, { enabled: event.target.checked })
                }
              />
              转出 {row.provider_name || `陪玩 #${row.provider}`}
            </label>
            {row.enabled && (
              <>
                {!beforeStart && (segmentCapture ? <p className="text-xs text-muted-foreground">收益将根据成交份额和对应抽成核对，目前不能结算。</p> : <p className="text-xs text-muted-foreground">
                  可分配收益{" "}
                  {amountToXaCoin(
                    row.transferable_income ??
                      row.expected_income ??
                      row.provider_income
                  )}{" "}
                  {coinText("浪花，实际份额以核对结果为准。")}
                </p>)}
                <OptionPicker
                  resource="escorts"
                  keyField="user"
                  label="转入陪玩"
                  value={row.new_provider_id}
                  query={beforeStart ? {} : { service_id: order.service_item || order.service }}
                  onChange={(value) =>
                    change(index, { new_provider_id: value })
                  }
                />
                {beforeStart && <p className="text-xs text-muted-foreground">提交时按原订单要求核验接替陪玩的资格、档期及接单名额。</p>}
                {needsOriginalAgreement && <div className="space-y-3 rounded-lg border p-3">
                  <p className="text-sm font-medium">双方协商的原价份额</p>
                  <p className="text-xs text-muted-foreground">原份额：{row.original_segment ? `${amountToXaCoin(row.original_segment.original_share)} ${coinUnit()}` : "记录不完整，请先核对"}。{segmentCapture ? "据此记录成交基数；各段抽成及结算以核对结果为准。" : "此处用于接单额统计，与收益分配分别记录。"}</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1"><Label htmlFor={`original-retained-${index}`}>原陪玩份额（{coinUnit()}）</Label><Input id={`original-retained-${index}`} type="number" min="0" step="0.1" value={row.original_retained} onChange={(event) => change(index, {original_retained:event.target.value})} /></div>
                    <div className="space-y-1"><Label htmlFor={`original-transferred-${index}`}>接替陪玩份额（{coinUnit()}）</Label><Input id={`original-transferred-${index}`} type="number" min="0" step="0.1" value={row.original_transferred} onChange={(event) => change(index, {original_transferred:event.target.value})} /></div>
                  </div>
                  <Label htmlFor={`original-agreement-${index}`}>协商依据</Label><Textarea id={`original-agreement-${index}`} maxLength={500} value={row.original_agreement} onChange={(event) => change(index, {original_agreement:event.target.value})} />
                </div>}
                {!beforeStart && <div className={segmentCapture ? "grid gap-3" : "grid grid-cols-3 gap-3"}>
                  {!segmentCapture && <><Field
                    field={{
                      key: `split-type-${index}`,
                      label: "原陪玩保留方式",
                      type: "select",
                      options: [
                        ["PERCENT", "按比例"],
                        ["FIXED", coinText("固定浪花")],
                      ],
                    }}
                    value={row.split_type}
                    onChange={(value) => change(index, { split_type: value })}
                  />
                  <Field
                    field={{
                      key: `split-${index}`,
                      label:
                        row.split_type === "FIXED"
                          ? coinText("保留浪花")
                          : "保留比例（%）",
                      type: "number",
                      min: 0,
                      max: row.split_type === "PERCENT" ? 100 : undefined,
                      step: row.split_type === "FIXED" ? 0.1 : 1,
                    }}
                    value={row.split_value}
                    onChange={(value) => change(index, { split_value: value })}
                  /></>}
                  <Field
                    field={{
                      key: `penalty-${index}`,
                      label: coinText("处罚金额（浪花）"),
                      type: "number",
                      min: 0,
                      step: 0.1,
                    }}
                    value={row.penalty}
                    onChange={(value) => change(index, { penalty: value })}
                  />
                </div>}
              </>
            )}
          </section>
        ))}
      </div>
      <div className="space-y-2">
        {needsMinimumReview && <section className="space-y-3 rounded-xl border p-4">
          <h3 className="font-semibold">核对历史最低接单等级</h3>
          <p className="text-sm text-muted-foreground">原订单未保存最低等级。请先核对原始要求及依据，再选择对应的本店等级。</p>
          <Label htmlFor="legacy-minimum-choice">核对结果</Label>
          <select autoComplete="off" name="legacy-minimum-choice" id="legacy-minimum-choice" className="flex h-10 w-full rounded-md border bg-background px-3 text-sm" value={minimumChoice} onChange={(event) => { setMinimumChoice(event.target.value); setMinimumLevel(""); setReviewed(false) }}>
            <option value="">请选择</option><option value="NONE">原要求不限等级</option><option value="LEVEL">原要求有最低等级</option>
          </select>
          {minimumChoice === "LEVEL" && <><Label htmlFor="legacy-minimum-level">对应本店最低等级</Label>
            <select autoComplete="off" name="legacy-minimum-level" id="legacy-minimum-level" className="flex h-10 w-full rounded-md border bg-background px-3 text-sm" value={minimumLevel} onChange={(event) => { setMinimumLevel(event.target.value); setReviewed(false) }}>
              <option value="">请选择等级</option>{(order.prestart_legacy_minimum?.levels || []).map((level: Row) => <option key={level.id} value={level.id}>{level.name}</option>)}
            </select></>}
          <Label htmlFor="legacy-original-requirement">原等级要求</Label>
          <Input id="legacy-original-requirement" value={originalRequirement} maxLength={120} onChange={(event) => { setOriginalRequirement(event.target.value); setReviewed(false) }} />
          <Label htmlFor="legacy-minimum-evidence">核对依据</Label>
          <Textarea id="legacy-minimum-evidence" value={minimumEvidence} maxLength={500} onChange={(event) => { setMinimumEvidence(event.target.value); setReviewed(false) }} />
        </section>}
        <Label htmlFor="transfer-reason">转单说明</Label>
        <Textarea
          id="transfer-reason"
          value={reason}
          onChange={(event) => {
            setReason(event.target.value)
            setReviewed(false)
          }}
          maxLength={255}
        />
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input
          className="mt-1 size-4 accent-blue-600"
          type="checkbox"
          checked={reviewed}
          onChange={(event) => setReviewed(event.target.checked)}
        />
        {beforeStart ? "已核对原陪玩、接替陪玩和订单安排" : segmentCapture ? "已核对转出与转入人员、原价份额及处罚金额" : "已核对转出与转入人员、原陪玩保留收益及处罚金额"}
      </label>
      {unknown && (
        <Feedback message="转单结果未确认。请关闭并核对订单人员、收益和流水，避免重复转单。" />
      )}
    </MutationDialog>
  )
}
