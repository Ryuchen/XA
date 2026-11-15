import { AdminFilters } from "@/components/AdminFilters"
import { PendingResult } from "@/components/PayrollFields"
import { DataValue, PageDataContext } from "@/components/page-data-state"
import { coinText, coinUnit } from "@xa/shared"
import { AdminPageSkeleton } from "@/components/AdminPageSkeleton"
import { FileCheck2, WalletCards, FileText } from "@/components/ui/icons"
import { ReceiptText, Banknote, BanknoteArrowDown, Gift, LockKeyhole, ArrowLeftRight, ArrowDownLeft, ArrowUpRight, ShieldCheck, Medal, CircleMinus, type LucideIcon } from "@/components/ui/icons"
import type { ReactNode } from "react"
import { useEffect, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"
import { rmbToRawAmount } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Checkbox } from "@/components/ui/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { hasMoneyAmount, rmb as financialRmb } from "@/lib/payroll"
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
  FormSection,
  Feedback,
  Field,
  MutationDialog,
  PageHeader as SharedPageHeader,
  Pagination,
  RefreshButton,
  Status,
  useList,
  useLiveReload,
  useLoad,
  type Column,
  type FormField,
  type Row,
} from "@/components/business"
import DefaultCommission from "@/components/CommissionEditor"
import PrivateImage from "@/components/PrivateImage"
import { request, requestId, reviewedFundRequest } from "@/api/request"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime, xaCoinToAmount } from "@/lib/format"
import { PAYEE_METHOD, TX_TYPE } from "@/lib/dict"
import { reportPayoutDisplay, reportSettlementLabel } from "@/lib/reports"

// Missing money fields are unknown, including within an otherwise readable row.
const displayCoin = (value: unknown) => hasMoneyAmount(value) ? amountToXaCoin(Number(value)) : "—"
const readAmountTotal = (rows: Row[], key: string) => rows.every(row => hasMoneyAmount(row[key])) ? rows.reduce((sum, row) => sum + Number(row[key]), 0) : undefined

function FilterFields({ fields, value, onChange }: { fields: FormField[]; value: Row; onChange: (value: Row) => void }) {
  return <>{fields.map(field => <Field key={field.key} field={field} value={value[field.key]} onChange={next => onChange({ ...value, [field.key]: next })} />)}</>
}

export function FinancialHeader(props: { title: string; description?: string; children?: ReactNode; tone?: "plain" | "feature"; tag?: string; metrics?: Array<{ label: string; value: ReactNode }>; metricsColumns?: number; filters?: ReactNode; filterDescription?: string }) {
  return <SharedPageHeader {...props} tone="feature" />
}
const PageHeader = FinancialHeader

export function FinancialStats({ items, wallet = false, columns = 4, snapshotAvailable }: { snapshotAvailable?: boolean; items: Array<{ label: string; value: ReactNode; unit?: string; note: string; icon: LucideIcon; tone?: string }>; wallet?: boolean; columns?: number }) {
  if (wallet) return <section className="fin-stat-grid fin-wallet-stats" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>{items.map(item => <Card key={item.label} className="fin-wallet-stat gap-0 py-4"><CardContent><div><p className="fin-wallet-stat-label">{item.label}</p><p className={"fin-wallet-stat-amount " + (item.tone || "")}><DataValue known={snapshotAvailable}>{item.value}</DataValue>{item.unit && <span>{item.unit}</span>}</p><p className="fin-wallet-stat-note">{item.note}</p></div><span className="fin-wallet-stat-icon"><item.icon size={16} /></span></CardContent></Card>)}</section>
  return <dl className={"fin-stat-grid" + (wallet ? " fin-wallet-stats" : "")} style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>{items.map(item => <div key={item.label}><div><dt>{item.label}</dt><item.icon className={item.tone || "text-primary"} size={16} /></div><dd className={item.tone}><DataValue known={snapshotAvailable}>{item.value}</DataValue>{item.unit && <span>{item.unit}</span>}</dd><p>{item.note}</p></div>)}</dl>
}

export function FinancialSection({ title, description, icon: Icon, action, children, className = "" }: { title: string; description?: ReactNode; icon?: LucideIcon; action?: ReactNode; children: ReactNode; className?: string }) {
  return <Card className={"fin-section " + className}><CardHeader><div><CardTitle>{Icon && <Icon size={16} />}{title}</CardTitle>{description && <CardDescription>{description}</CardDescription>}</div>{action}</CardHeader><CardContent>{children}</CardContent></Card>
}

export function ReportsPage() {
  const [searchParams] = useSearchParams()
  const [draft, setDraft] = useState<Row>(() =>
    Object.fromEntries(searchParams.entries())
  )
  const [query, setQuery] = useState<Row>(draft)
  const result = useList("reports", query)
  const [commission, setCommission] = useState(false)
  const [detail, setDetail] = useState<number | null>(null)
  const [action, setAction] = useState<{
    kind: "approve" | "reject"
    row: Row
  } | null>(null)
  const [remark, setRemark] = useState("")
  const [unknown, setUnknown] = useState(false)
  const auth = useAdminAuth()
  useLiveReload(result.reload)
  const current = useLoad(
    async () =>
      detail ? (await request({ url: `/reports/${detail}/` })).data : null,
    [detail]
  )
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton="reports"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="订单分账快照"
            metrics={[
              {
                label: "本页待审核",
                value: result.data
                  ? result.rows.filter((row) => row.status === "PENDING").length
                  : "—",
              },
              {
                label: "本页订单待核对",
                value: result.data
                  ? result.rows.filter((row) => !!row.order_approval_block_reason)
                      .length
                  : "—",
              },
              {
                label: "本页已通过",
                value: result.data ? result.rows.filter((row) => row.status === "APPROVED").length : "—",
              },
            ]}
            title="履约凭证审核"
            description="按逐人履约凭证核验。通过不代表立即入账，结算仍遵循售后窗口与报单规则。"

          >
            <RefreshButton loading={result.loading} reload={result.reload} />
            {auth.hasPerm("report:audit") && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCommission(true)}
              >
                订单默认抽成配置
              </Button>
            )}
          </PageHeader>
          
          <Feedback message={result.error} retry={result.reload} />
          <div className="fin-records-group"><DataTable
            toolbar={<form
            className="w-full"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...draft })
            }}
          >
            <AdminFilters primary={<>
<div className="w-72 space-y-2">
              <Label htmlFor="report-keyword">订单、陪玩或编号</Label>
              <Input
                id="report-keyword"
                value={draft.keyword || ""}
                onChange={(event) =>
                  setDraft({ ...draft, keyword: event.target.value })
                }
              />
            </div>
            <Field
              field={{
                key: "report-status",
                label: "审核状态",
                type: "select",
                options: [
                  ["PENDING", "待审核"],
                  ["APPROVED", "已通过"],
                  ["REJECTED", "已驳回"],
                ],
              }}
              value={draft.status}
              onChange={(value) => setDraft({ ...draft, status: value })}
            />
            </>} actions={<Button size="sm" variant="outline" type="submit">
              查询
            </Button>}>
<FilterFields
              fields={[
                { key: "game_name", label: "游戏 / 项目" },
                { key: "provider_username", label: "陪玩账号" },
                { key: "provider_nickname", label: "陪玩昵称 / 编号" },
                {
                  key: "order_blocked",
                  label: "订单状态待核对",
                  type: "select",
                  options: [["1", "只看待核对"]],
                },
                {
                  key: "exclude_direct_test",
                  label: "直接支付测试单",
                  type: "select",
                  options: [["1", "排除测试单"]],
                },
              ]}
              value={draft}
              onChange={setDraft}
            />
            </AdminFilters>
          </form>}

            title="报单审核队列"
            titleIcon={FileCheck2}
            description={`本页 ${result.data ? result.rows.length : "—"} 条 · 收益以分账快照为准`}
            minWidth={1240}
            rows={result.rows}
            error={result.error}
            retry={result.reload}
            loading={result.loading}
            columns={[
{ key: "id", label: "报单 / 状态", render: row => <div><Button variant="link" size="sm" onClick={() => setDetail(row.id)}>#{row.id}</Button><Status value={row.status} label={row.status_display} />{row.status === "APPROVED" && <div className="mt-1"><ReportSettlement row={row} /></div>}</div> },
{ key: "provider_name", label: "陪玩", render: row => <div className="fin-identity"><span className="fin-avatar">{(row.provider_name || "?").slice(0, 1)}</span><div><p className="font-medium">{row.provider_name}</p><small>{row.provider_no}</small><small>{row.provider_username}</small></div></div> },
{ key: "order_no", label: "关联订单", className: "text-xs leading-4", render: row => { const orderNo = row.order_entity_no || row.order_no; return orderNo && auth.hasPerm("order:view") ? <Link className="inline-flex min-h-6 items-center text-primary hover:underline" to={"/orders?keyword=" + encodeURIComponent(orderNo)}>{orderNo}</Link> : orderNo || "独立报单" } },
{ key: "game_name", label: "服务 / 玩法", className: "text-xs leading-4" },
{ key: "amount", label: "报单金额", className: "text-xs leading-4", render: row => <span className="fin-inline-amount">{displayCoin(row.amount)} {coinUnit()}</span> },
{ key: "payout_amount", label: "预计 / 核定收益", className: "text-xs leading-4 [&_span]:whitespace-nowrap", render: reportPayoutDisplay },
{ key: "commission_source_label", label: "抽成来源", className: "text-xs leading-4", render: row => <span className="text-xs text-muted-foreground">{row.commission_source_label || "以分账快照为准"}</span> },
{ key: "created_at", label: "提交时间", className: "text-xs leading-4 whitespace-nowrap text-muted-foreground", render: row => formatDateTime(row.submitted_at || row.created_at) },
{
                key: "actions",
                label: "操作",
                render: (row) => (
                  <div className="flex flex-wrap gap-1">
                    <Button
                      actionTone="read"
                      size="sm"
                      variant="ghost"
                      onClick={() => setDetail(row.id)}
                    >
                      凭证详情
                    </Button>
                    {row.status === "PENDING" &&
                      auth.hasPerm("report:audit") && (
                        <>
                          <Button
                            actionTone="approve"
                            size="sm"
                            variant="outline"
                            disabled={
                              !!row.order_approval_block_reason ||
                              !!row.settlement_review_reason
                            }
                            onClick={() => {
                              setAction({ kind: "approve", row })
                              setRemark("")
                              setUnknown(false)
                            }}
                          >
                            通过
                          </Button>
                          <Button
                            actionTone="reject"
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setAction({ kind: "reject", row })
                              setRemark("")
                              setUnknown(false)
                            }}
                          >
                            驳回
                          </Button>
                        </>
                      )}
                  </div>
                ),
              }
]}
          /></div>
          <Pagination
            page={result.page}
            total={result.total}
            loading={result.loading}
            onChange={result.setPage}
            pageSize={result.pageSize}
            onPageSizeChange={result.setPageSize}
          />
          {commission && (
            <DefaultCommission onClose={() => setCommission(false)} />
          )}
          <Dialog
            open={!!detail}
            onOpenChange={(value) => {
              if (!value) setDetail(null)
            }}
          >
            <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-[1120px]">
              <DialogHeader>
                <DialogTitle>报单凭证 #{detail}</DialogTitle>
                <DialogDescription>
                  核对归属、入队、结单与战绩截图。
                </DialogDescription>
              </DialogHeader>
              <Feedback
                variant={current.data ? "inline" : "panel"}
                message={current.error}
                retry={current.reload}
              />
              {current.loading ? (
                <AdminPageSkeleton page="report-detail" />
              ) : (
                current.data && (
                  <div className="space-y-4">
                    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.4fr)_340px]">
                      <FormSection title="报单与归属">
                        <Details
                          data={current.data}
                          fields={[
                            ["provider_name", "陪玩"],
                            ["provider_no", "陪玩编号"],
                            ["provider_username", "账号"],
                            ["order_no", "关联订单"],
                            ["game_name", "服务"],
                            ["description", "报单说明"],
                            ["remark", "备注"],
                            ["audit_remark", "审核意见"],
                            ["auditor_name", "审核人"],
                            ["audited_at", "审核时间"],
                          ]}
                        />
                      </FormSection>
                      <FormSection
                        title="分账与结算快照"
                        className="bg-muted/15"
                      >
                        <Feedback
                          message={
                            current.data.order_approval_block_reason ||
                            current.data.settlement_review_reason ||
                            (current.data.platform_funding_insufficient
                              ? "平台资金不足，核定报酬等待补款。"
                              : "")
                          }
                        />

                        <p className="text-sm text-muted-foreground">
                          抽成来源：
                          {current.data.commission_source_label ||
                            "以订单分账快照为准"}
                          。退款后的净收益请核对钱包流水。
                        </p>
                        <p className="rounded-lg border bg-muted/30 p-3 text-sm">
                          {reportPayoutDisplay(current.data)} ·{" "}
                          {current.data.status === "APPROVED" ? <ReportSettlement row={current.data} /> : <Status value={current.data.status} label={current.data.status_display} />}
                        </p>
                      </FormSection>
                    </div>
                    <section className="grid gap-4 xl:grid-cols-3">
                      <FormSection title="入队截图">
                        <PrivateImage
                          label="入队截图"
                          urls={[current.data.entry_image_url]}
                        />
                      </FormSection>
                      <FormSection title="结单截图">
                        <PrivateImage
                          label="结单截图"
                          urls={[current.data.completion_image_url]}
                        />
                      </FormSection>
                      <FormSection title="战绩截图">
                        <PrivateImage
                          label="战绩截图"
                          urls={current.data.result_image_urls || []}
                        />
                      </FormSection>
                    </section>
                    {current.data.proof_image_url && (
                      <FormSection title="历史履约凭证">
                        <PrivateImage
                          label="历史履约凭证"
                          urls={[current.data.proof_image_url]}
                        />
                      </FormSection>
                    )}
                  </div>
                )
              )}
            </DialogContent>
          </Dialog>
          {action && (
            <MutationDialog
              title={action.kind === "approve" ? "通过报单审核" : "驳回报单"}
              submitVariant={
                action.kind === "reject" ? "destructive" : "default"
              }
              description={`报单 #${action.row.id} · ${action.row.provider_name} · ${action.row.provider_no} · ${action.row.order_entity_no || action.row.order_no || "独立报单"}。`}
              onClose={() => setAction(null)}
              submitText={action.kind === "approve" ? "确认通过" : "确认驳回"}
              disabled={unknown || (action.kind === "reject" && !remark.trim())}
              onSubmit={async () => {
                const latest = (
                  await request({ url: `/reports/${action.row.id}/` })
                ).data
                if (
                  latest.status !== "PENDING" ||
                  (action.kind === "approve" &&
                    latest.order_approval_block_reason)
                )
                  throw new Error(
                    latest.order_approval_block_reason ||
                      "报单状态已变化，请刷新核对。"
                  )
                try {
                  await request({
                    url: `/reports/${latest.id}/${action.kind}/`,
                    method: "POST",
                    data:
                      action.kind === "reject"
                        ? { audit_remark: remark.trim() }
                        : undefined,
                  })
                  toast.success(
                    action.kind === "approve" ? "审核已通过" : "报单已驳回"
                  )
                  void result.reload()
                } catch (cause: any) {
                  setUnknown(true)
                  throw cause
                }
              }}
            >
              <p className="rounded-lg border bg-muted/40 p-4 text-sm leading-6">
                {action.row.order
                  ? "请确认该陪玩的入队、结单和战绩截图完整有效。全体报单通过、售后窗口结束且无未结客诉后，系统按订单分账快照结算。"
                  : `核定收益 ${amountToXaCoin(action.row.expected_income ?? action.row.payout_amount)} ${coinUnit()}，实际入账以结算记录为准。`}
              </p>
              {action.kind === "reject" && (
                <div className="space-y-2">
                  <Label htmlFor="report-remark">驳回原因</Label>
                  <Textarea
                    id="report-remark"
                    value={remark}
                    onChange={(event) => setRemark(event.target.value)}
                    maxLength={255}
                    required
                  />
                </div>
              )}
              {unknown && (
                <PendingResult>请返回列表刷新并核对报单状态后继续。</PendingResult>
              )}
            </MutationDialog>
          )}
        </div>
      }
    </DataPageState>
  )
}

function ReportSettlement({ row }: { row: Row }) {
  const label = reportSettlementLabel(row)
  const tone = label.includes("退款") ? "muted" : label.startsWith("已") ? "success" : "warning"
  return <Status label={label} tone={tone} />
}

function claimState(row: Row) {
  if (
    !Object.hasOwn(row, "claimed_by_account") ||
    typeof row.claim_is_mine !== "boolean"
  )
    return "unknown"
  return row.claimed_by_account === null
    ? row.claim_is_mine
      ? "unknown"
      : "unclaimed"
    : row.claim_is_mine
      ? "mine"
      : "other"
}
const claimLabel = (row: Row) =>
  ({
    unknown: "领取状态待核对",
    unclaimed: "未领取",
    mine: "我已领取",
    other: `${row.claimed_by_name || "其他客服"}已领取`,
  })[claimState(row)]
export function WithdrawalsPage() {
  const [searchParams] = useSearchParams()
  const [draft, setDraft] = useState<Row>(() =>
    Object.fromEntries(searchParams.entries())
  )
  const [query, setQuery] = useState<Row>(draft)
  const list = useList("withdrawals", query)
  const [detail, setDetail] = useState<Row | null>(null)
  const [detailTarget, setDetailTarget] = useState<number | null>(null)
  const [action, setAction] = useState<string | null>(null)
  const [reason, setReason] = useState("")
  const [confirmed, setConfirmed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [configOpen, setConfigOpen] = useState(false)
  const auth = useAdminAuth()
  useLiveReload(list.reload)
  async function view(row: Row) {
    setDetailTarget(row.id)
    setLoading(true)
    setError("")
    setDetail(null)
    try {
      setDetail((await request({ url: `/withdrawals/${row.id}/` })).data)
    } catch (cause: any) {
      setError(cause.message)
    } finally {
      setLoading(false)
    }
  }
  function openAction(kind: string) {
    setAction(kind)
    setReason("")
    setConfirmed(false)
    setUnknown(false)
  }
  const mine = detail && claimState(detail) === "mine"
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={list.reload}
      skeleton="withdrawals"
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag="门店初审"
            metrics={[
              {
                label: "本页待审核",
                value: list.data
                  ? list.rows.filter((row) => row.status === "PENDING" && row.workflow_version === 2).length
                  : "—",
              },
              {
                label: "本页初审申请金额",
                value: list.data ? financialRmb(readAmountTotal(list.rows.filter(row => row.status === "PENDING" && row.workflow_version === 2), "actual_amount")) : "—",
              },
            ]}
            title="提现审核"
            description="先领取并核对申请，初审通过后交平台财务处理。"

          >
            <RefreshButton loading={list.loading} reload={list.reload} />
            {auth.hasPerm("withdraw:config") && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfigOpen(true)}
              >
                提现规则
              </Button>
            )}
          </PageHeader>
          
          <Feedback message={list.error || error} retry={list.reload} />
          <div className="fin-records-group"><DataTable
            toolbar={<form
            className="w-full"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...draft })
            }}
          >
            <AdminFilters primary={<>
<div className="space-y-2">
              <Label htmlFor="withdraw-keyword">陪玩 / 账号</Label>
              <Input
                id="withdraw-keyword"
                value={draft.keyword || ""}
                onChange={(event) =>
                  setDraft({ ...draft, keyword: event.target.value })
                }
              />
            </div>
            <Field
              field={{
                key: "withdraw-status",
                label: "审核状态",
                type: "select",
                options: [
                  ["PENDING", "待审核"],
                  ["STORE_APPROVED", "门店初审通过"],
                  ["PLATFORM_APPROVED", "平台终审通过，待打款"],
                  ["PAYMENT_UNKNOWN", "打款结果待核实"],
                  ["PAID", "已打款"],
                  ["APPROVED", "历史已通过"],
                  ["REJECTED", "已驳回"],
                  ["CANCELLED", "已取消"],
                ],
              }}
              value={draft.status}
              onChange={(value) => setDraft({ ...draft, status: value })}
            />
            <Field
              field={{
                key: "claim_scope",
                label: "领取范围",
                type: "select",
                options: [
                  ["unclaimed", "未领取"],
                  ["mine", "我已领取"],
                  ["other", "其他客服已领取"],
                ],
              }}
              value={draft.claim_scope}
              onChange={(value) => setDraft({ ...draft, claim_scope: value })}
            />
            <Field
              field={{
                key: "payee-method",
                label: "收款方式",
                type: "select",
                options: Object.entries(PAYEE_METHOD).map(([key, item]) => [
                  key,
                  item.label,
                ]),
              }}
              value={draft.payee_method}
              onChange={(value) => setDraft({ ...draft, payee_method: value })}
            />
            </>} actions={<Button type="submit" size="sm" variant="outline">查询</Button>}>
<FilterFields
              fields={[
                { key: "provider_username", label: "陪玩账号" },
                { key: "provider_nickname", label: "陪玩昵称" },
                { key: "payee_name", label: "收款人" },
                { key: "payee_account", label: "收款账号" },
              ]}
              value={draft}
              onChange={setDraft}
            />
            </AdminFilters>
          </form>}

            title="提现申请记录"
            titleIcon={WalletCards}
            description={`本页 ${list.data ? list.rows.length : "—"} 项`}
            minWidth={1060}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
{ key: "identity", label: "申请 / 陪玩", render: row => <div className="fin-identity"><span className="fin-avatar">{(row.user_name || "?").slice(0, 1)}</span><div><p className="font-medium">{row.user_name}</p><Button variant="link" size="sm" onClick={() => void view(row)}>#{row.id}</Button><small>{row.provider_username}</small></div></div> },
{ key: "amount", label: "提现金额", render: row => <div><p className="fin-inline-amount">{displayCoin(row.amount)} {coinUnit()}</p><p className="mt-1 text-xs text-muted-foreground">扣除费用 {financialRmb(row.tax_amount)} · {row.tax_rate}%</p></div> },
{ key: "actual_amount", label: "应打款", render: row => <strong className="fin-inline-amount">{financialRmb(row.actual_amount)}</strong> },
{ key: "status", label: "状态 / 领取", render: row => <div><Status value={row.status} label={row.status_display} /><div className="mt-1">{row.status === "PENDING" && row.workflow_version === 1 ? <Status label="历史申请待核实" tone="warning" /> : <Status label={claimLabel(row)} tone={claimState(row) === "mine" ? "primary" : claimState(row) === "unknown" ? "warning" : "muted"} />}</div></div> },
{ key: "payee_method", label: "收款信息", render: row => <div><p>{PAYEE_METHOD[row.payee_method]?.label} · {row.payee_name}</p><p className="mt-1 text-xs text-muted-foreground">{row.payee_account}</p></div> },
{ key: "created_at", label: "提交时间", render: row => formatDateTime(row.created_at) },
{
                key: "actions",
                label: "操作",
                render: (row) => (
                  <Button
                    actionTone="read"
                    size="sm"
                    variant="outline"
                    disabled={loading}
                    onClick={() => void view(row)}
                  >
                    核对处理
                  </Button>
                ),
              }
]}
          /></div>
          <Pagination
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          <Dialog
            open={detailTarget !== null || !!detail || loading}
            onOpenChange={(value) => {
              if (!value) {
                setDetail(null)
                setDetailTarget(null)
              }
            }}
          >
            <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>
                  提现申请 #{detail?.id || detailTarget}
                </DialogTitle>
                <DialogDescription>
                  收款资料为申请时快照，不能用陪玩当前账户资料替代。
                </DialogDescription>
              </DialogHeader>
              <Feedback
                message={error}
                retry={() => {
                  if (detailTarget) void view({ id: detailTarget })
                }}
              />
              {loading ? (
                <AdminPageSkeleton page="financial-detail" />
              ) : (
                detail && (
                  <div className="space-y-4">
                    <section className="fin-money-summary"><p>预计打款人民币</p><strong>{financialRmb(detail.actual_amount)}</strong><p className="mt-2 text-muted-foreground">{PAYEE_METHOD[detail.payee_method]?.label} · {detail.payee_name} · {detail.payee_account}</p></section>
                    <Details
                      data={detail}
                      fields={[
                        ["user_name", "陪玩"],
                        ["provider_username", "账号"],
                        ["payee_name", "收款人"],
                        ["payee_account", "收款账号"],
                        ["payee_method_display", "收款方式"],
                        ["claimed_by_name", "领取人"],
                        ["claimed_at", "领取时间"],
                        ["remark", "申请备注"],
                        ["payout_reference", "打款凭据编号"],
                        ["audit_remark", "审核说明"],
                        ["auditor_name", "审核人"],
                        ["audited_at", "审核时间"],
                      ]}
                    />
                    <div className="grid grid-cols-3 gap-4 rounded-lg border bg-muted/40 p-4">
                      {[
                        ["amount", "申请金额"],
                        ["tax_amount", "扣除费用"],
                        ["actual_amount", ["PAID", "APPROVED"].includes(detail.status) ? "实际打款" : "预计打款"],
                      ].map(([key, label]) => (
                        <div key={key}>
                          <p className="text-xs text-muted-foreground">
                            {label}
                          </p>
                          <p className="mt-1 font-semibold">
                            {key === "amount" ? `${displayCoin(detail[key])} ${coinUnit()}` : financialRmb(detail[key])}</p>
                        </div>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {detail.status === "PENDING" && detail.workflow_version === 1 && <p className="text-sm text-muted-foreground">历史申请需先人工核实是否已打款，不能在此直接审核。</p>}
                      {detail.status === "PENDING" && detail.workflow_version === 2 &&
                        auth.hasPerm("withdraw:audit") &&
                        claimState(detail) === "unclaimed" && (
                          <Button actionTone="edit" onClick={() => openAction("claim")}>
                            领取申请
                          </Button>
                        )}
                      {detail.status === "PENDING" && detail.workflow_version === 2 &&
                        auth.hasPerm("withdraw:audit") &&
                        mine && (
                          <>
                            <Button actionTone="approve" onClick={() => openAction("approve")}>
                              初审通过
                            </Button>
                            <Button
                              actionTone="reject"
                              variant="outline"
                              onClick={() => openAction("reject")}
                            >
                              驳回并解冻
                            </Button>
                          </>
                        )}
                      {detail.status === "PENDING" && detail.workflow_version === 2 &&
                        auth.hasPerm("withdraw:release") &&
                        ["mine", "other"].includes(claimState(detail)) && (
                          <Button
                            actionTone="adjust"
                            variant="outline"
                            onClick={() => openAction("release-claim")}
                          >
                            释放领取
                          </Button>
                        )}
                      <Button
                        actionTone="read"
                        variant="outline"
                        onClick={() => void view(detail)}
                      >
                        重新读取
                      </Button>
                    </div>
                  </div>
                )
              )}
            </DialogContent>
          </Dialog>
          {action && detail && (
            <MutationDialog
              title={
                action === "claim"
                  ? "领取提现申请"
                  : action === "approve"
                    ? "确认门店初审通过"
                    : action === "reject"
                      ? "驳回提现"
                      : "释放领取"
              }
              description={`申请 #${detail.id} · ${detail.user_name} · 应打款 ${financialRmb(detail.actual_amount)} · ${detail.payee_name} / ${detail.payee_account}`}
              submitVariant={
                action === "reject"
                  ? "destructive"
                  : action === "release-claim"
                    ? "outline"
                    : "default"
              }
              onClose={() => setAction(null)}
              disabled={
                unknown ||
                (action === "approve" && !confirmed) ||
                (["reject", "release-claim"].includes(action) && (!confirmed || !reason.trim()))
              }
              submitText="确认处理"
              onSubmit={async () => {
                const latest = (
                  await request({ url: `/withdrawals/${detail.id}/` })
                ).data
                if (latest.status !== "PENDING")
                  throw new Error("申请已经处理，请重新读取。")
                if (
                  ["approve", "reject"].includes(action) &&
                  claimState(latest) !== "mine"
                )
                  throw new Error("申请未由你领取，请重新核对。")
                if (
                  action !== "claim" &&
                  latest.claimed_at !== detail.claimed_at
                )
                  throw new Error("领取状态已经变化，请重新读取。")
                const payload =
                  action === "claim"
                    ? {}
                    : action === "release-claim"
                      ? {
                          confirmed_unpaid: true,
                          reason: reason.trim(),
                          expected_claimed_by_account:
                            detail.claimed_by_account,
                          expected_claimed_at: detail.claimed_at,
                        }
                      : {
                          expected_claimed_at: latest.claimed_at,
                          ...(action === "approve" ? {} : { audit_remark: reason.trim() }),
                        }
                try {
                  const result = await request({
                    url: `/withdrawals/${detail.id}/${action}/`,
                    method: "POST",
                    data: payload,
                  })
                  setDetail(result.data)
                  toast.success("提现申请已更新")
                  void list.reload()
                } catch (cause) {
                  setUnknown(true)
                  throw cause
                }
              }}
            >
              <p className="rounded-lg border bg-muted/40 p-4 text-sm leading-6">
                {action === "approve"
                  ? "初审通过后交由平台财务终审及登记线下打款，资金继续冻结。"
                  : action === "reject"
                    ? "驳回后申请金额解冻返还给陪玩，请说明原因。"
                    : action === "release-claim"
                      ? "仅在核实尚未打款后释放。释放后其他审核人可以领取该申请。"
                      : "领取后由你核对申请和收款资料，其他客服不能同时初审。"}
              </p>
              {action !== "claim" && (
                <>
                  {action !== "approve" && <div className="space-y-2">
                    <Label htmlFor="withdraw-reason">
                      原因说明
                    </Label>
                    <Textarea
                      id="withdraw-reason"
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      maxLength={255}
                      required
                    />
                  </div>}
                  <label className="flex items-start gap-2 text-sm">
                    <Checkbox
                      id="withdraw-confirmation"
                      className="mt-1 shrink-0"
                      checked={confirmed}
                      onCheckedChange={(checked) => setConfirmed(checked === true)}
                    />
                    {action === "approve"
                      ? "已核对申请金额、收款人和收款账号"
                      : action === "release-claim"
                        ? "已核实该申请尚未打款"
                        : "已核对申请及驳回原因"}
                  </label>
                </>
              )}
              {unknown && (
                <PendingResult>请返回详情重新读取并核对状态、领取人和打款凭据后继续。</PendingResult>
              )}
            </MutationDialog>
          )}
          {configOpen && (
            <WithdrawConfig onClose={() => setConfigOpen(false)} />
          )}
        </div>
      }
    </DataPageState>
  )
}

export function WithdrawConfig({ onClose }: { onClose: () => void }) {
  const result = useLoad(
    async () => (await request({ url: "/config/withdraw" })).data
  )
  const [form, setForm] = useState<Row>({})
  useEffect(() => {
    if (result.data)
      setForm({
        min_amount: Number(amountToXaCoin(result.data.min_amount)),
        balance_threshold: result.data.balance_threshold == null ? "" : Number(amountToXaCoin(result.data.balance_threshold)),
        tax_rate: result.data.tax_rate,
      })
  }, [result.data])
  return (
    <MutationDialog
      title="提现规则"
      description="单笔最低额和可用余额门槛分别设置，陪玩余额必须严格大于门槛。"
      onClose={onClose}
      disabled={!result.data || result.loading || !!result.error}
      onSubmit={async () => {
        if (
          !Number.isFinite(form.min_amount) ||
          form.min_amount < 0 ||
          !Number.isFinite(form.balance_threshold) ||
          form.balance_threshold < 0 ||
          !Number.isInteger(form.tax_rate) ||
          form.tax_rate < 0 ||
          form.tax_rate > 100
        )
          throw new Error("请检查最低金额、余额门槛和费率。")
        await request({
          url: "/config/withdraw",
          method: "PUT",
          data: {
            min_amount: xaCoinToAmount(form.min_amount),
            balance_threshold: xaCoinToAmount(form.balance_threshold),
            tax_rate: form.tax_rate,
            expected_version: result.data.version,
          },
        })
        toast.success("提现规则已保存")
      }}
    >
      <Feedback message={result.error} retry={result.reload} />
      <Field
        field={{
          key: "min_amount",
          label: coinText("最低提现金额（浪花）"),
          type: "number",
          min: 0,
          step: 0.1,
        }}
        value={form.min_amount}
        onChange={(value) => setForm({ ...form, min_amount: value })}
      />
      <Field
        field={{ key: "balance_threshold", label: coinText("可用余额门槛（浪花）"),
          type: "number", min: 0, step: 0.1, required: true,
          hint: "可用余额须严格大于该值；等于门槛时不能申请。" }}
        value={form.balance_threshold}
        onChange={(value) => setForm({ ...form, balance_threshold: value })}
      />
      <Field
        field={{
          key: "tax_rate",
          label: "提现扣除费率（%）",
          type: "number",
          min: 0,
          max: 100,
          step: 1,
        }}
        value={form.tax_rate}
        onChange={(value) => setForm({ ...form, tax_rate: value })}
      />
    </MutationDialog>
  )
}

export function WalletsPage() {
  const [draft, setDraft] = useState<Row>({
    keyword: "",
    account_type: "BOSS",
    is_active: "",
  })
  const [query, setQuery] = useState<Row>(draft)
  const list = useList("wallets", query)
  const auth = useAdminAuth()
  const navigate = useNavigate()
  const [pendingOpen, setPendingOpen] = useState(false), [pendingPage, setPendingPage] = useState(1)
  const pendingLedger = useLoad(async () => pendingOpen ? (await request({ url: "/wallets/platform-funds/", params: { pending_page: pendingPage } })).data : null, [pendingOpen, pendingPage])
  const [operation, setOperation] = useState<{
    kind: string
    row?: Row
  } | null>(null)
  const platform = useLoad(
    async () => (await request({ url: "/wallets/platform-funds/" })).data
  )
  const walletReadError = [list.error && `钱包列表：${list.error}`, platform.error && `平台资金：${platform.error}`].filter(Boolean).join("；")
  const fundsLocked = !list.data || !platform.data || list.loading || platform.loading || !!list.error || !!platform.error
  useLiveReload(() => Promise.all([list.reload(), platform.reload(), ...(pendingOpen ? [pendingLedger.reload()] : [])]))
  return (
    <DataPageState
      loading={list.loading}
      hasSnapshot={!!list.data}
      error={list.error}
      retry={() => {
        void list.reload()
        void platform.reload()
      }}
      skeleton="wallets"
    >
      {
        <div className="fin-page space-y-4">
          <PageDataContext.Provider value={{ loading: platform.loading, hasSnapshot: !!platform.data, error: platform.error, retry: platform.reload, noticeTarget: "region" }}><PageHeader
            tone="feature"
            tag="资金快照"
            metricsColumns={2}
            metrics={
              (platform.data?.enabled !== false)
                ? [
                    ["available", coinText("可用于补贴 · 浪花")],
                    ["balance", coinText("平台资金余额 · 浪花")],
                    ["deposits", "陪玩押金 · 不可补贴"],
                    ["pending", coinText("待结算金额 · 浪花")],
                  ].map(([key, label]) => ({
                    label,
                    value: <DataValue known={typeof (key === "pending" ? platform.data?.pending?.amount : platform.data?.[key]) === "number"} loading={platform.loading}>{amountToXaCoin(
                      key === "pending"
                        ? platform.data?.pending?.amount
                        : platform.data?.[key]
                    )}</DataValue>,
                  }))
                : undefined
            }
            title="钱包台账"
            description="核对账户身份和余额。充值、赠送与调账均写入真实账务流水。"
          >
            <RefreshButton loading={list.loading || platform.loading} reload={() => { void list.reload(); void platform.reload() }} />
            {platform.data?.enabled && <Button variant="outline" onClick={() => { setPendingPage(1); setPendingOpen(true) }}>待结算订单</Button>}
            {platform.data?.enabled && auth.hasPerm("wallet:adjust") && <Button variant="outline" disabled={fundsLocked} onClick={() => setOperation({ kind: "platform" })}>平台线下补入</Button>}
          </PageHeader></PageDataContext.Provider>
          <FinancialStats wallet snapshotAvailable={!!list.data} items={[
            { label: `${query.account_type === "PROVIDER" ? "陪玩" : "老板"}本页钱包余额`, value: list.data ? displayCoin(readAmountTotal(list.rows, "balance")) : "—", unit: coinUnit(), note: `本页 ${list.data ? list.rows.length : "—"} 个钱包 · 不含冻结`, icon: WalletCards },
            { label: "本页冻结金额", value: list.data ? displayCoin(readAmountTotal(list.rows, "frozen_amount")) : "—", unit: coinUnit(), note: "订单或提现处理中", icon: LockKeyhole },
            { label: "本页累计实充", value: list.data ? displayCoin(readAmountTotal(list.rows, "total_recharge")) : "—", unit: coinUnit(), note: "仅客服实充，不含赠送", icon: BanknoteArrowDown },
            { label: "本页累计赠送", value: list.data ? displayCoin(readAmountTotal(list.rows, "total_gift")) : "—", unit: coinUnit(), note: "与实充分开记账", icon: Gift },
          ]} />
          <section className="fin-policy"><ShieldCheck size={16} /><div><h2>平台资金与陪玩押金分口径展示</h2><p>可用于补贴金额不包含陪玩押金；待结算金额与订单单独核对。调账、充值和线下补入均需确认并保留凭据。</p></div></section>
          <Feedback message={walletReadError} retry={() => { if (list.error) void list.reload(); if (platform.error) void platform.reload() }} />
          {platform.data?.enabled !== true && <section className="fin-policy"><LockKeyhole size={16} /><div>
            <h2 className="flex items-center gap-2">平台补贴功能 <Status label="未开放" readState={platform.data?.enabled === false ? undefined : platform.loading ? "loading" : platform.error ? "error" : "unread"} /></h2>
            {platform.data?.enabled === false && <p>仅显示已授权的钱包与账务功能，平台线下补入暂不可用。</p>}
          </div></section>}
          {platform.data?.enabled && <Dialog open={pendingOpen} onOpenChange={setPendingOpen}><DialogContent className="sm:max-w-4xl"><DialogHeader><DialogTitle>待结算订单</DialogTitle><DialogDescription>平台可用资金与待结算报酬分别核对，不能当作利润。</DialogDescription></DialogHeader><Feedback message={pendingLedger.data ? pendingLedger.error : ""} retry={pendingLedger.reload} />{!auth.hasPerm("order:view") ? <p className="text-sm text-muted-foreground">当前无订单查看权限，可核对资金总额；订单明细需对应授权。</p> : <><DataTable
                rows={pendingLedger.data?.pending?.orders || []} snapshotAvailable={Array.isArray(pendingLedger.data?.pending?.orders)}
                loading={pendingLedger.loading}
                error={pendingLedger.error}
                retry={pendingLedger.reload}
                columns={[
                  { key: "order_no", label: "待结算订单" },
                  { key: "service_name", label: "服务" },
                  {
                    key: "amount",
                    label: "报酬",
                    render: (row) => `${displayCoin(row.amount)} ${coinUnit()}`,
                  },
                  {
                    key: "status",
                    label: "订单状态",
                    render: (row) => (
                      <Status value={row.status} label={row.status_display} />
                    ),
                  },
                ]}
              /><Pagination snapshotAvailable={Number.isSafeInteger(pendingLedger.data?.pending?.order_count)} page={pendingLedger.data?.pending?.page || pendingPage} pageSize={10} total={pendingLedger.data?.pending?.order_count ?? 0} loading={pendingLedger.loading} onChange={setPendingPage} /></>}</DialogContent></Dialog>}
          
          <div className="fin-records-group"><DataTable
            toolbar={<form
            className="w-full"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...draft })
            }}
          >
            <AdminFilters description="仅筛选账户列表及本页钱包统计，不影响平台资金和待结算台账。" primary={<>
<div className="space-y-2">
              <Label htmlFor="wallet-account-type">账户类型</Label>
              <Select value={query.account_type} onValueChange={value => { setDraft(old => ({ ...old, account_type: value })); setQuery(old => ({ ...old, account_type: value })) }}>
                <SelectTrigger id="wallet-account-type" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="BOSS">老板钱包</SelectItem><SelectItem value="PROVIDER">陪玩钱包</SelectItem></SelectContent>
              </Select>
            </div>
<div className="w-80 space-y-2">
              <Label htmlFor="wallet-keyword">账号 / 昵称 / 编号 / 手机</Label>
              <Input
                id="wallet-keyword"
                value={draft.keyword ?? ""}
                onChange={(event) =>
                  setDraft({ ...draft, keyword: event.target.value })
                }
              />
            </div>

            <Field
              field={{
                key: "wallet-active",
                label: "账户状态",
                type: "select",
                options: [
                  ["1", "正常"],
                  ["0", "停用"],
                ],
              }}
              value={draft.is_active}
              onChange={(value) => setDraft({ ...draft, is_active: value })}
            />
            </>} actions={<Button type="submit" size="sm" variant="outline">查询</Button>}>
<FilterFields
              fields={[
                { key: "boss_no", label: "账户编号" },
                { key: "nickname", label: "昵称" },
                { key: "phone", label: "手机号" },
              ]}
              value={draft}
              onChange={setDraft}
            />
            </AdminFilters>
          </form>}
            title="账户资金列表"
            titleIcon={WalletCards}
            description={`本页 ${list.data ? list.rows.length : "—"} 项 · 余额与冻结分开核对`}
            minWidth={960}
            rows={list.rows}
            error={list.error}
            retry={list.reload}
            loading={list.loading}
            columns={[
              {
                key: "nickname",
                label: "账户",
                render: row => <div className="fin-identity"><span className="fin-avatar">{(row.nickname || row.username || "?").slice(0, 1)}</span><div><p className="font-medium">{row.nickname || row.username}</p><small>{row.boss_no || "无编号"} · {row.username}</small><small>{row.user_role === "PROVIDER" ? "陪玩" : "老板"}{row.phone ? " · " + row.phone : ""}</small></div></div>,
              },
              {
                key: "balance",
                label: "可用余额",
                render: (row) => `${displayCoin(row.balance)} ${coinUnit()}`,
              },
              {
                key: "frozen_amount",
                label: "冻结金额",
                render: (row) => `${displayCoin(row.frozen_amount)} ${coinUnit()}`,
              },
              {
                align: "right",
                key: "total_recharge",
                label: "累计实充",
                render: (row) => `${displayCoin(row.total_recharge)} ${coinUnit()}`,
              },
              {
                align: "right",
                key: "total_gift",
                label: "累计赠送",
                render: (row) => `${displayCoin(row.total_gift)} ${coinUnit()}`,
              },
              {
                key: "is_active",
                label: "状态",
                render: (row) => (
                  <Status
                    value={row.is_active}
                    readState={typeof row.is_active === "boolean" ? undefined : "unread"}
                    label={row.is_active ? "正常" : "停用"}
                  />
                ),
              },
              {
                key: "actions",
                label: "操作",
                render: (row) => (
                  <div className="ml-auto flex max-w-80 flex-wrap justify-end gap-1">
                    {auth.hasPerm("transaction:view") && (
                      <Button
                        actionTone="read"
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          navigate(`/transactions?wallet_id=${row.id}`)
                        }
                      >
                        流水
                      </Button>
                    )}
                    {auth.hasPerm("recharge:view") && (
                      <Button
                        actionTone="history"
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          navigate(`/recharge-records?wallet_id=${row.id}`)
                        }
                      >
                        充值记录
                      </Button>
                    )}
                    {auth.hasPerm("wallet:recharge") && row.is_active && row.user_role === "CUSTOMER" && (
                      <Button
                        actionTone="credit"
                        size="sm"
                        variant="outline"
                        disabled={fundsLocked}
                        onClick={() => setOperation({ kind: "recharge", row })}
                      >
                        充值
                      </Button>
                    )}
                    {auth.hasPerm("wallet:adjust") &&
                      row.is_active &&
                      (row.user_role === "CUSTOMER" || row.user_role === "PROVIDER") && (
                        <Button
                          actionTone="adjust"
                          size="sm"
                          variant="outline"
                          disabled={fundsLocked}
                          onClick={() => setOperation({ kind: "adjust", row })}
                        >
                          调账
                        </Button>
                      )}
                  </div>
                ),
              },
            ]}
          /></div>
          <Pagination
            page={list.page}
            total={list.total}
            loading={list.loading}
            onChange={list.setPage}
            pageSize={list.pageSize}
            onPageSizeChange={list.setPageSize}
          />
          {operation && (
            <WalletMutation
              kind={operation.kind}
              locked={fundsLocked}
              wallet={operation.row}
              onClose={() => setOperation(null)}
              onSaved={() => {
                void list.reload()
                void platform.reload()
              }}
            />
          )}
        </div>
      }
    </DataPageState>
  )
}
function WalletMutation({
  kind,
  locked = false,
  wallet,
  onClose,
  onSaved,
}: {
  kind: string
  locked?: boolean
  wallet?: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [amount, setAmount] = useState("")
  const [balanceKind, setBalanceKind] = useState("PREPAID")
  const [gift, setGift] = useState("0")
  const [bonusQuote, setBonusQuote] = useState<{ amount: number; gift_amount: number; version: string; matches: Array<{ amount: number; count: number; gift_total: number }> } | null>(null)
  const [quoteError, setQuoteError] = useState("")
  const [reference, setReference] = useState("")
  const [remark, setRemark] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [intent] = useState(() => requestId(kind))
  useEffect(() => {
    if (kind !== "recharge") return
    setBonusQuote(null); setQuoteError("")
    if (!/^\d+(?:\.\d{1,2})?$/.test(amount)) return
    const raw = rmbToRawAmount(amount)
    if (!Number.isSafeInteger(raw) || raw <= 0 || raw > 2147483647) return
    let active = true
    request({ url: "/wallets/recharge-quote/", params: { amount: raw } }).then(result => {
      if (active) { setBonusQuote(result.data); setGift(String(result.data.gift_amount / 10)) }
    }).catch(() => { if (active) setQuoteError("预存试算暂不可用，请检查后台版本和门店档位后重试。") })
    return () => { active = false }
  }, [amount, kind])
  const enteredAmount = /^\d+(?:\.\d{1,2})?$/.test(amount) ? rmbToRawAmount(amount) : 0
  const hasValidAmount = Number.isSafeInteger(enteredAmount) && enteredAmount > 0 && enteredAmount <= 2147483647
  const currentQuote = hasValidAmount && bonusQuote?.amount === enteredAmount && hasMoneyAmount(bonusQuote.gift_amount) ? bonusQuote : null
  const title =
    kind === "recharge"
      ? "确认收款并充值"
      : kind === "adjust"
        ? "钱包调账"
        : "登记平台线下补入"
  return (
    <MutationDialog
      title={title}
      readOnly={locked}
      description={
        wallet
          ? `${wallet.nickname || wallet.username} · ${wallet.username} · 编号 ${wallet.boss_no || "未记录"} · 钱包 #${wallet.id}`
          : "确认已经核实线下资金与凭据后登记。"
      }
      onClose={onClose}
      disabled={
        !confirmed ||
        unknown ||
        !amount ||
        (kind !== "adjust" && !reference.trim()) ||
        (kind === "recharge" && !file) ||
        (kind === "recharge" && !bonusQuote) ||
        (kind === "adjust" && !remark.trim())
      }
      submitText="确认入账"
      onSubmit={async () => {
        if (kind === "recharge") {
          const raw = rmbToRawAmount(amount),
            giftRaw = xaCoinToAmount(gift)
          if (!(raw > 0) || !/^\d+(?:\.\d)?$/.test(gift) || !Number.isSafeInteger(giftRaw) || giftRaw < 0 || !file)
            throw new Error(coinText("请检查实收金额、赠送浪花及凭证。"))
          const data = new FormData()
          data.append(
            wallet?.account_id ? "account_id" : "user_id",
            String(wallet?.account_id || wallet?.user)
          )
          data.append("amount", String(raw))
          data.append("gift_amount", String(giftRaw))
          data.append("bonus_version", bonusQuote!.version)
          data.append("remark", remark)
          data.append("trade_no", reference.trim())
          data.append("client_request_id", intent)
          data.append("proof_image", file)
          try {
            const response = await request({ url: "/wallets/recharge/", method: "POST", data })
            toast.success(response.data?.gift_approval_pending ? "本金已到账，赠送待门店管理员审批" : "已完成入账，请核对流水")
          } catch (cause: any) {
            if (!cause.status || cause.status >= 500) setUnknown(true)
            throw cause
          }
        } else {
          const raw = xaCoinToAmount(amount)
          if (
            !Number.isSafeInteger(raw) ||
            raw === 0 ||
            (kind === "platform" && raw < 0)
          )
            throw new Error(coinText("请输入有效金额，浪花最多一位小数。"))
          try {
            await reviewedFundRequest(
              kind === "platform"
                ? "/admin/wallets/platform-topup/"
                : `/admin/wallets/${wallet!.id}/adjust/`,
              kind === "platform"
                ? {
                    amount: raw,
                    reference: reference.trim(),
                    client_request_id: intent,
                  }
                : {
                    amount: raw,
                    balance_kind: balanceKind,
                    remark: remark.trim(),
                    client_request_id: intent,
                  }
            )
          } catch (cause: any) {
            if (!cause.status || cause.status >= 500) setUnknown(true)
            throw cause
          }
        }
        if (kind !== "recharge") toast.success("已完成入账，请核对流水")
        onSaved()
      }}
    >
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="wallet-amount">
            {kind === "recharge" ? "实收人民币（元）" : coinText("变动金额（浪花）")}
          </Label>
          <Input
            id="wallet-amount"
            type="number"
            step={kind === "recharge" ? ".01" : ".1"}
            value={amount}
            onChange={(event) => {
              setAmount(event.target.value)
              setConfirmed(false)
            }}
          />
        </div>
        {kind === "adjust" && wallet?.user_role !== "PROVIDER" && (
          <div className="space-y-2">
            <Label htmlFor="wallet-balance-kind">正向入账资金类型</Label>
            <Select autoComplete="off" name="balance_kind" value={balanceKind} onValueChange={value => { setBalanceKind(value); setConfirmed(false) }}>
              <SelectTrigger id="wallet-balance-kind" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="PREPAID">预存</SelectItem>
                <SelectItem value="GIFT">赠送</SelectItem>
                <SelectItem value="COMPENSATION">赔款</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        {kind === "recharge" && (
          <div className="space-y-2">
            <Label htmlFor="wallet-gift">{coinText("赠送浪花")}</Label>
            <Input
              id="wallet-gift"
              type="number"
              min="0"
              step=".1"
              value={gift}
              onChange={event => { setGift(event.target.value); setConfirmed(false) }}
            />
            <p className="text-xs text-muted-foreground">标准赠送按本店档位从高到低组合；修改后本金先到账，赠送待门店管理员审批。</p>
            {bonusQuote && <p className="text-xs text-muted-foreground">{bonusQuote.matches.length ? bonusQuote.matches.map(item => `${amountToXaCoin(item.amount)} × ${item.count}`).join(" + ") : "未匹配充值档位"}</p>}
            {quoteError && <p role="alert" className="text-xs text-destructive">{quoteError}</p>}
          </div>
        )}
        {kind !== "adjust" && (
          <div className="col-span-2 space-y-2">
            <Label htmlFor="wallet-reference">
              {kind === "platform" ? "线下凭据编号" : "收款流水号"}
            </Label>
            <Input
              id="wallet-reference"
              value={reference}
              onChange={(event) => {
                setReference(event.target.value)
                setConfirmed(false)
              }}
              maxLength={64}
            />
          </div>
        )}
        {kind === "recharge" && (
          <div className="col-span-2 space-y-2">
            <Label htmlFor="recharge-proof">收款凭证</Label>
            <Input
              id="recharge-proof"
              type="file"
              accept="image/*"
              onChange={(event) => {
                setFile(event.target.files?.[0] || null)
                setConfirmed(false)
              }}
            />
          </div>
        )}
        <div className="col-span-2 space-y-2">
          <Label htmlFor="wallet-remark">
            {kind === "adjust" ? "调账原因（必填）" : "备注"}
          </Label>
          <Textarea
            id="wallet-remark"
            value={remark}
            onChange={(event) => {
              setRemark(event.target.value)
              setConfirmed(false)
            }}
            maxLength={255}
          />
        </div>
      </div>
      {kind === "adjust" && (
        <p className="text-sm text-muted-foreground">
          正数按所选类型入账；负数按资金批次顺序冲减，不单独指定扣款类型。操作将记录经办人和原因。
        </p>
      )}
      {kind === "recharge" && (
        <p className="rounded-lg border bg-muted/40 p-4 text-sm">
          {currentQuote && xaCoinToAmount(gift || 0) !== currentQuote.gift_amount ? "先到账本金：" : "预计到账："}
          {currentQuote
            ? `${amountToXaCoin(enteredAmount + (xaCoinToAmount(gift || 0) === currentQuote.gift_amount ? currentQuote.gift_amount : 0))} ${coinUnit()}`
            : <Status readState={quoteError ? "error" : hasValidAmount && !bonusQuote ? "loading" : "unread"} />}{" "}
          {coinText("1 元 = 10 浪花。")}</p>
      )}
      <label className="flex items-start gap-2 text-sm">
        <Checkbox name="已核对账户身份、金额、收款凭据与备注"
          className="mt-1 shrink-0"
          checked={confirmed}
          onCheckedChange={(checked) => setConfirmed(checked === true)}
        />
        已核对账户身份、金额、收款凭据与备注
      </label>
      {unknown && (
        <PendingResult>请核对钱包余额、充值记录和资金流水，避免重复入账。</PendingResult>
      )}
    </MutationDialog>
  )
}

export function RecordsPage({
  kind,
}: {
  kind: "transactions" | "recharge-records" | "dispose-records"
}) {
  const auth = useAdminAuth()
  const [searchParams] = useSearchParams()
  const [draft, setDraft] = useState<Row>(() => ({
    ...Object.fromEntries(searchParams.entries()),
    ...(searchParams.get("order_no")
      ? { order_no_exact: searchParams.get("order_no") }
      : {}),
  }))
  const [query, setQuery] = useState(draft)
  const result = useList(kind, query)
  const [original, setOriginal] = useState<number | null>(null)
  const source = useLoad(
    async () =>
      original
        ? (await request({ url: `/transactions/${original}/` })).data
        : null,
    [original]
  )
  useLiveReload(result.reload)
  const [disposeDetail, setDisposeDetail] = useState<Row | null>(null)
  const [giftDecision, setGiftDecision] = useState<{ row: Row; decision: "APPROVED" | "REJECTED" } | null>(null)
  const [giftDecisionUnknown, setGiftDecisionUnknown] = useState(false)
  const columns: Column[] = kind === "transactions" ? [
    { key: "identity", label: "流水 / 账户", render: row => <div><div className="flex items-center gap-2"><strong>{row.tx_type_display || row.tx_type}</strong><Status value={row.status} label={row.status_display} /></div><p className="mt-1 text-xs text-muted-foreground">{row.tx_no}</p><p className="mt-1 text-xs text-muted-foreground">{row.username} · 钱包 #{row.wallet}</p>{["EARNINGS_FREEZE", "EARNINGS_UNFREEZE"].includes(row.tx_type) && <p className="mt-2 rounded-md bg-primary/5 p-2 text-xs leading-5 text-muted-foreground">{row.tx_type === "EARNINGS_FREEZE" ? "余额转入冻结资金" : "冻结资金转回余额"}</p>}</div> },
    { key: "amount", label: "金额 / 余额", align: "right", render: row => <div><p className={"fin-row-amount " + (Number(row.amount) > 0 && row.status === "SUCCESS" ? "text-success" : "")}>{Number(row.amount) > 0 ? "+" : ""}{displayCoin(row.amount)} <small>{coinUnit()}</small></p><p className="mt-1 text-xs text-muted-foreground">变动前 {displayCoin(row.balance_before)}</p><p className="mt-1 text-xs text-muted-foreground">变动后 {displayCoin(row.balance_after)} {coinUnit()}</p></div> },
    { key: "reference", label: "关联订单与冲正", className: "max-w-96 whitespace-normal", render: row => <div>{row.order_no ? auth.hasPerm("order:view") ? <Link className="inline-flex min-h-6 items-center text-primary hover:underline" to={`/orders?keyword=${encodeURIComponent(row.order_no)}`}>{row.order_no}</Link> : <span>{row.order_no}</span> : <span className="text-muted-foreground">未关联订单</span>}{row.is_reversal && <Button variant="link" size="sm" className="block" onClick={() => setOriginal(row.reversal_of)}>查看原流水 #{row.reversal_of}</Button>}{row.remark && <p className="mt-2 text-xs leading-5 text-muted-foreground">{row.remark}</p>}</div> },
    { key: "operator", label: "经办记录", render: row => <div><p>{row.operator_name || "—"}</p><p className="mt-1 text-xs text-muted-foreground">{formatDateTime(row.created_at)}</p></div> },
  ] : kind === "recharge-records" ? [
    { key: "identity", label: "收款账户", render: row => <div className="fin-identity"><span className="fin-avatar">{(row.nickname || row.username || "账户").slice(0, 1)}</span><div><p className="font-medium">{row.nickname || row.username}</p><small>{row.user_role === "PROVIDER" ? "陪玩" : "老板"} · {row.boss_no || "未记录编号"}</small><small>{row.username}{row.phone ? ` · ${row.phone}` : ""}</small></div></div> },
    { key: "amount", label: "到账明细", align: "right", render: row => <div><p className="font-semibold">实收 {financialRmb(row.amount)}</p><p className="mt-1 text-xs text-muted-foreground">实充 {displayCoin(row.amount)} {coinUnit()}</p><p className="mt-1 text-xs text-muted-foreground">赠送 {displayCoin(row.gift_amount)} {coinUnit()}</p><p className="mt-2 font-semibold text-success">合计 +{displayCoin(row.total_amount)} {coinUnit()}</p>{row.gift_approval && <p className="mt-2 text-xs">赠送审批 <Status value={row.gift_approval.status} label={({ PENDING: "待审批", APPROVED: "已通过", REJECTED: "已驳回并发放标准赠送" } as Record<string, string>)[row.gift_approval.status]} /></p>}{row.gift_approval?.status === "PENDING" && auth.hasPerm("recharge:gift_approve") && <div className="mt-2 flex flex-wrap gap-2"><Button actionTone="approve" size="sm" variant="outline" onClick={() => { setGiftDecisionUnknown(false); setGiftDecision({ row, decision: "APPROVED" }) }}>通过</Button><Button actionTone="reject" size="sm" variant="outline" onClick={() => { setGiftDecisionUnknown(false); setGiftDecision({ row, decision: "REJECTED" }) }}>驳回</Button></div>}</div> },
    { key: "proof", label: "流水与凭证", className: "max-w-80 whitespace-normal", render: row => <div><p>{row.trade_no || "未记录收款流水号"}</p><PrivateImage label="收款凭证" urls={[row.proof_image_url]} previewSkeleton={<AdminPageSkeleton page="recharge-proof" />} /></div> },
    { key: "operator", label: "经办记录", className: "max-w-72 whitespace-normal", render: row => <div><p>{row.operator_name || "—"}</p><p className="mt-1 text-xs text-muted-foreground">{formatDateTime(row.created_at)}</p>{row.remark && <p className="mt-2 text-xs leading-5 text-muted-foreground">{row.remark}</p>}{auth.hasPerm("salary:edit") && <Button variant="ghost" size="sm" asChild><Link to={`/salary-activities?kind=RECHARGE&source_id=${row.id}`}>核实充值业绩</Link></Button>}</div> },
  ] : [
    { key: "identity", label: "陪玩 / 账号", render: row => <div><p className="font-medium">{row.nickname || "陪玩待核对"}</p><p className="mt-1 text-xs text-muted-foreground">{row.username || "未记录"}</p></div> },
    { key: "amount", label: "类型 / 金额", align: "right", render: row => <div><Status value={row.dispose_type} label={row.dispose_type_display} tone={row.dispose_type === "REWARD" ? "success" : row.dispose_type === "PENALTY" ? "destructive" : "muted"} /><p className={"fin-row-amount mt-2 " + (row.dispose_type === "REWARD" ? "text-success" : row.dispose_type === "PENALTY" ? "text-destructive" : "")}>{hasMoneyAmount(row.amount) ? row.dispose_type === "REWARD" ? "+" : row.dispose_type === "PENALTY" ? "−" : "" : ""}{displayCoin(row.amount)} <small>{coinUnit()}</small></p></div> },
    { key: "reason", label: "奖惩原因", className: "max-w-96 whitespace-normal" },
    { key: "operator", label: "经办记录", render: row => <div><p>{row.operator_name || "—"}</p><p className="mt-1 text-xs text-muted-foreground">{formatDateTime(row.created_at)}</p></div> },
    { key: "actions", label: "详情", render: row => <Button variant="outline" size="sm" onClick={() => setDisposeDetail(row)}>查看详情</Button> },
  ]
  const totalFor = (key: string, predicate: (row: Row) => boolean = () => true) => result.rows.filter(predicate).reduce((sum, row) => sum + Number(row[key] || 0), 0)
  const income = totalFor("amount", row => row.status === "SUCCESS" && Number(row.amount) > 0), expense = -totalFor("amount", row => row.status === "SUCCESS" && Number(row.amount) < 0)
  const reward = totalFor("amount", row => row.dispose_type === "REWARD"), penalty = totalFor("amount", row => row.dispose_type === "PENALTY")
  const snapshot = (value: ReactNode) => result.data ? value : "—"
  const amountSnapshot = (value: ReactNode, key = "amount") => result.data && readAmountTotal(result.rows, key) != null ? value : "—"
  const statistics = kind === "transactions" ? [
    { label: "本页记录", value: snapshot(result.rows.length), unit: "笔", note: `符合条件共 ${result.data ? result.total : "—"} 笔`, icon: ReceiptText },
    { label: "本页成功流入", value: amountSnapshot("+" + displayCoin(income)), unit: coinUnit(), note: "仅本页成功记录", icon: ArrowUpRight, tone: "text-success" },
    { label: "本页成功流出", value: amountSnapshot("−" + displayCoin(expense)), unit: coinUnit(), note: "按金额绝对值汇总", icon: ArrowDownLeft },
    { label: "本页净变动", value: amountSnapshot((income - expense >= 0 ? "+" : "−") + displayCoin(Math.abs(income - expense))), unit: coinUnit(), note: "不等于平台经营收入", icon: ArrowLeftRight },
  ] : kind === "recharge-records" ? [
    { label: "本页充值记录", value: snapshot(result.rows.length), unit: "笔", note: `符合条件共 ${result.data ? result.total : "—"} 笔`, icon: ReceiptText },
    { label: "本页实收人民币", value: amountSnapshot(financialRmb(totalFor("amount"))), note: "实际收款与虚拟币分开核对", icon: Banknote },
    { label: "本页充值赠送", value: amountSnapshot(displayCoin(totalFor("gift_amount")), "gift_amount"), unit: coinUnit(), note: "独立关联赠送流水", icon: Gift },
    { label: "本页合计到账", value: amountSnapshot("+" + displayCoin(totalFor("total_amount")), "total_amount"), unit: coinUnit(), note: "实充与赠送的内部账务合计", icon: WalletCards, tone: "text-success" },
  ] : [
    { label: "本页奖惩记录", value: snapshot(result.rows.length), unit: "笔", note: `符合条件共 ${result.data ? result.total : "—"} 笔`, icon: ReceiptText },
    { label: "本页奖励", value: amountSnapshot("+" + displayCoin(reward)), unit: coinUnit(), note: "正向钱包记账", icon: Medal, tone: "text-success" },
    { label: "本页处罚", value: amountSnapshot("−" + displayCoin(penalty)), unit: coinUnit(), note: "负向钱包记账", icon: CircleMinus, tone: "text-destructive" },
    { label: "本页净变动", value: amountSnapshot((reward - penalty >= 0 ? "+" : "−") + displayCoin(Math.abs(reward - penalty))), unit: coinUnit(), note: "仅为本页记录合计", icon: ArrowLeftRight },
  ]
  const title =
    kind === "transactions"
      ? "资金流水"
      : kind === "recharge-records"
        ? "充值记录"
        : "奖惩记录"
  return (
    <DataPageState
      loading={result.loading}
      hasSnapshot={!!result.data}
      error={result.error}
      retry={result.reload}
      skeleton={kind === "transactions" ? "transactions" : kind === "recharge-records" ? "recharges" : "disposes"}
    >
      {
        <div className="fin-page space-y-4">
          <PageHeader
            tone="feature"
            tag={kind === "transactions" ? "只读审计" : "只读台账"}
            title={title}
            description={
              kind === "transactions"
                ? "按流水类型和关联账户核对余额变动。收益冻结/解冻须同时核对可用与冻结余额。"
                : kind === "recharge-records"
                  ? "按账户或完整收款流水号核对客服确认的到账记录。"
                  : "查看奖励、处罚及经办记录。"
            }

          >
            {kind === "recharge-records" ? auth.hasPerm("transaction:view") && <Button variant="outline" asChild><Link to={"/transactions" + (query.wallet_id ? "?wallet_id=" + encodeURIComponent(String(query.wallet_id)) : "")}><ArrowLeftRight size={16} />核对资金流水</Link></Button> : <div className="fin-header-readonly"><span><ShieldCheck size={20} /></span><div><p>账务留痕</p><small>{kind === "transactions" ? "只读记录 · 不在此页修改资金" : "金额方向明确 · 操作人可追溯"}</small></div></div>}
            <RefreshButton loading={result.loading} reload={result.reload} />
          </PageHeader>
          <FinancialStats items={statistics} />
          <section className="fin-policy"><ShieldCheck size={16} /><div><h2>{kind === "transactions" ? "冻结与解冻不是普通收支" : kind === "recharge-records" ? "实收、赠送与到账分口径核对" : "记录与钱包同步留痕"}</h2><p>{kind === "transactions" ? "收益冻结与解冻须结合可用和冻结余额核对，冲正记录应追溯原流水。失败记录不计入成功收支汇总。" : kind === "recharge-records" ? "实收人民币不等同到账虚拟币。合计到账由实充和赠送组成，缺失凭证不会使用其他记录代替。" : "本页为已发生记账的只读审计记录；新增奖惩从陪玩档案发起，并单独确认记账内容。"}</p></div></section>
          
          <Feedback message={result.error} retry={result.reload} />
          <div className="fin-records-group"><DataTable
            toolbar={<div className="space-y-4">{(query.wallet_id || query.order_no_exact) && <div className="fin-record-scopes">{[["wallet_id", "钱包"], ["order_no_exact", "订单"]].map(([key, title]) => query[key] && <span key={key}>{title} {query[key]}<Button type="button" variant="ghost" size="sm" onClick={() => { const withoutScope = (old: Row) => { const next = { ...old }; delete next[key]; if (key === "order_no_exact") delete next.order_no; return next }; setDraft(withoutScope); setQuery(withoutScope) }}>清除{title}筛选</Button></span>)}</div>}<form
            className="w-full"
            onSubmit={(event) => {
              event.preventDefault()
              setQuery({ ...draft })
            }}
          >
            <AdminFilters primary={<>
{kind !== "recharge-records" && (
              <div className="w-72 space-y-2">
                <Label htmlFor="record-keyword">账号 / 昵称 / 流水号</Label>
                <Input
                  id="record-keyword"
                  value={draft.keyword || ""}
                  onChange={(event) =>
                    setDraft({ ...draft, keyword: event.target.value })
                  }
                />
              </div>
            )}
            {kind === "transactions" && (
              <Field
                field={{
                  key: "tx_type",
                  label: "流水类型",
                  type: "select",
                  options: Object.entries(TX_TYPE).map(([key, value]) => [
                    key,
                    value.label,
                  ]),
                }}
                value={draft.tx_type}
                onChange={(value) => setDraft({ ...draft, tx_type: value })}
              />
            )}
            {kind === "recharge-records" && (
              <Field
                field={{ key: "trade_no", label: "完整收款流水号" }}
                value={draft.trade_no}
                onChange={(value) => setDraft({ ...draft, trade_no: value })}
              />
            )}
            </>} actions={<Button type="submit" size="sm" variant="outline">查询</Button>}>
<FilterFields
              fields={
                kind === "recharge-records"
                  ? [
                      { key: "boss_no", label: "账户编号" },
                      { key: "nickname", label: "昵称" },
                      { key: "phone", label: "手机号" },
                      { key: "wallet_id", label: "钱包 ID" },
                    ]
                  : kind === "transactions"
                    ? [
                        { key: "wallet_id", label: "钱包 ID" },
                        { key: "order_no_exact", label: "完整订单号" },
                      ]
                    : [
                        { key: "account_id", label: "陪玩业务账户 ID" },
                        {
                          key: "dispose_type",
                          label: "奖罚类型",
                          type: "select",
                          options: [
                            ["REWARD", "奖励"],
                            ["PENALTY", "处罚"],
                          ],
                        },
                      ]
              }
              value={draft}
              onChange={setDraft}
            />
            </AdminFilters>
          </form></div>}

            title={kind === "transactions" ? "资金变动明细" : kind === "recharge-records" ? "充值与赠送明细" : "陪玩奖惩明细"}
            titleIcon={FileText}
            loadingSkeleton={kind === "dispose-records" ? <AdminPageSkeleton page="disposes" /> : undefined}
            description={`本页 ${result.data ? result.rows.length : "—"} 条`}
            minWidth={1120}
            rows={result.rows}
            error={result.error}
            retry={result.reload}
            columns={columns}
            loading={result.loading}
          /></div>
          <Pagination
            page={result.page}
            total={result.total}
            loading={result.loading}
            onChange={result.setPage}
            pageSize={result.pageSize}
            onPageSizeChange={result.setPageSize}
          />
          <Dialog open={!!disposeDetail} onOpenChange={open => { if (!open) setDisposeDetail(null) }}><DialogContent className="sm:max-w-xl"><DialogHeader><DialogTitle>奖惩记录 #{disposeDetail?.id}</DialogTitle><DialogDescription>已发生记账的只读审计快照。</DialogDescription></DialogHeader>{disposeDetail && <div className="space-y-4"><div className="fin-money-summary"><p>{disposeDetail.nickname || disposeDetail.username}</p><strong className={disposeDetail.dispose_type === "REWARD" ? "text-success" : "text-destructive"}>{disposeDetail.dispose_type === "REWARD" ? "+" : "−"}{displayCoin(disposeDetail.amount)} {coinUnit()}</strong></div><Details data={disposeDetail} fields={[["username", "陪玩账号"], ["dispose_type_display", "奖惩类型"], ["reason", "奖惩原因"], ["operator_name", "操作人"], ["created_at", "记账时间"]]} /></div>}</DialogContent></Dialog>
          {giftDecision && <MutationDialog title={giftDecision.decision === "APPROVED" ? "通过赠送调整" : "驳回赠送调整"} description={`充值 #${giftDecision.row.id} · ${giftDecision.row.nickname || giftDecision.row.username}。标准赠送 ${amountToXaCoin(giftDecision.row.gift_approval.standard_amount)} ${coinUnit()}，申请赠送 ${amountToXaCoin(giftDecision.row.gift_approval.requested_amount)} ${coinUnit()}；${giftDecision.decision === "APPROVED" ? "通过后发放申请金额" : "驳回后发放标准金额"}。`} disabled={giftDecisionUnknown} onClose={() => setGiftDecision(null)} onSubmit={async () => {
            try { await request({ url: `/recharge-records/${giftDecision.row.id}/gift-decision/`, method: "POST", data: { decision: giftDecision.decision } }); result.reload(); toast.success("审批完成，赠送已按结果入账") }
            catch (cause: any) { if (!cause.status || cause.status >= 500 || cause.status === 409) setGiftDecisionUnknown(true); throw cause }
          }}>{giftDecisionUnknown && <PendingResult>请关闭并刷新充值记录后核对赠送审批。</PendingResult>}</MutationDialog>}
          <Dialog
            open={!!original}
            onOpenChange={(value) => {
              if (!value) setOriginal(null)
            }}
          >
            <DialogContent className="sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>原资金流水 #{original}</DialogTitle>
                <DialogDescription>
                  核对冲正所关联的原始资金变动。
                </DialogDescription>
              </DialogHeader>
              <Feedback
                variant={source.data ? "inline" : "panel"}
                message={source.error}
                retry={source.reload}
              />
              {source.loading ? (
                <AdminPageSkeleton page="transaction-original" />
              ) : (
                source.data && (
                  <div className="space-y-4"><section className="fin-money-summary"><p>原流水金额</p><strong className={Number(source.data.amount) > 0 && source.data.status === "SUCCESS" ? "text-success" : undefined}>{Number(source.data.amount) > 0 ? "+" : ""}{displayCoin(source.data.amount)} {coinUnit()}</strong><p className="mt-2 text-xs text-muted-foreground">{source.data.tx_type_display} · {source.data.tx_no}</p></section><div className="grid grid-cols-2 gap-3 rounded-xl border p-4 text-sm"><div><p className="text-xs text-muted-foreground">变动前余额</p><strong>{displayCoin(source.data.balance_before)} {coinUnit()}</strong></div><div><p className="text-xs text-muted-foreground">变动后余额</p><strong>{displayCoin(source.data.balance_after)} {coinUnit()}</strong></div></div><Details
                    data={source.data}
                    fields={[
                      ["tx_no", "流水号"],
                      ["username", "账号"],
                      ["tx_type_display", "类型"],
                      ["status", "状态"],
                      ["remark", "备注"],
                      ["operator_name", "经办人"],
                      ["created_at", "时间"],
                      ["order_no", "关联订单"],
                    ]}
                  /></div>
                )
              )}
            </DialogContent>
          </Dialog>
        </div>
      }
    </DataPageState>
  )
}
