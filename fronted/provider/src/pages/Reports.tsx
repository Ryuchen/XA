import { coinText, coinUnit } from "@xa/shared"
import {
  providerReportPresentation,
  providerReportFocus,
  loadProviderReportPage,
  mutateAndReload,
  uploadProviderReportImages,
} from "@xa/shared"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { Check, CheckCircle2, ChevronDown, FileCheck2, ImagePlus, Plus, Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Panel,
  PageHeader,
  Notice,
  LoadState,
  SnapshotGate,
  Status,
  DetailGrid,
  Confirm,
  Field,
  UploadField,
  Pager,
  PrivateImage,
} from "@/components/provider-ui"
import {
  api,
  useLoad,
  useRefresh,
  money,
  date,
  message,
  uncertain,
  useViewGuard,
} from "@/lib/provider"
import { RefundSummary } from "./Orders"

type Report = {
  id: number
  order_id: number
  order_no: string
  order_entity_no?: string
  game_name: string
  service_name?: string
  fulfillment?: string
  expected_income: number | null
  submitted_at: string | null
  settled_at: string | null
  settlement_status?: string
  negotiated_settlement?: boolean
  platform_funding_insufficient?: boolean
  order_status: string
  payment_status?: string
  aftersale_status?: string
  refund_summary?: Parameters<typeof RefundSummary>[0]["refund"]
  status: string
  status_display: string
  entry_image_url: string
  completion_image_url: string
  result_image_urls: string[]
  result_images: { id: number; url: string }[]
  audit_remark: string
  can_edit: boolean
  can_submit: boolean
  can_withdraw: boolean
  edit_block_reason: string
  evidence_upload_block_reasons?: Partial<
    Record<"ENTRY" | "COMPLETION", string>
  >
}
type Choice = { id: number; order_no: string; entity_no?: string; service_name: string }
const missing = (row: Report) => providerReportPresentation(row).missing
export function ReportsPage() {
  const guardView = useViewGuard()
  const [params, setParams] = useSearchParams(),
    focusInfo = providerReportFocus(params),
    focus = focusInfo.orderId,
    reportFocus = focusInfo.reportId,
    [page, setPage] = useState(1),
    [rows, setRows] = useState<Report[]>([]),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [chooser, setChooser] = useState(false),
    [choicePage, setChoicePage] = useState(1),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState(""),
    [selected, setSelected] = useState<Choice | null>(null),
    [submitTarget, setSubmitTarget] = useState<Report | null>(null),
    [withdrawTarget, setWithdrawTarget] = useState<Report | null>(null),
    [deleteTarget, setDeleteTarget] = useState<{
      report: Report
      image: number
    } | null>(null)
  const data = useLoad(
    () => loadProviderReportPage<Report>(page, focusInfo),
    [page, focus, reportFocus]
  )
  const choices = useLoad(
    () =>
      chooser
        ? api<{ list: Choice[]; total: number }>(
            `/wallet/report-order-choices/?page=${choicePage}&page_size=20&q=${encodeURIComponent(query)}`
          ).then(result => ({
            ...result,
            list: result.list.map(row => ({ ...row, order_no: row.entity_no || row.order_no })),
          }))
        : Promise.resolve({ list: [], total: 0 }),
    [chooser, choicePage, query]
  )
  const eligibility = useLoad(
    () =>
      focus
        ? api<{
            status: string
            report_entry?: { available: boolean; reason: string }
          }>(`/orders/orders/${focus}/`)
        : Promise.resolve(null),
    [focus]
  )
  useEffect(() => {
    setRows((data.data?.list || []).map(row => ({
      ...row,
      order_no: row.order_entity_no || row.order_no,
    })))
  }, [data.data])
  useEffect(() => {
    setPage(1)
    setBusy(false)
    setNotice("")
    setSubmitTarget(null)
    setWithdrawTarget(null)
    setDeleteTarget(null)
  }, [focus, reportFocus])
  useRefresh(async () => {
    if (!busy) await data.reload()
  }, ["report_status_update", "order_status_update"])
  async function create(id: number) {
    const currentView = guardView()
    setBusy(true)
    try {
      const outcome = await mutateAndReload({
        role: "provider",
        mutate: () =>
          api("/wallet/reports/", { method: "POST", data: { order_id: id } }),
        reload: data.reload,
        isCurrent: currentView,
      })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setNotice("报单已创建")
      setChooser(false)
      setParams({ order: String(id) })
      setPage(1)
    } catch (cause) {
      if (!currentView()) return
      setNotice(
        uncertain(cause) ? "创建结果未确认，请核对该订单报单。" : message(cause)
      )
      if (uncertain(cause)) {
        setChooser(false)
        setParams({ order: String(id) })
      }
    } finally {
      if (currentView()) setBusy(false)
    }
  }
  async function upload(
    row: Report,
    kind: "ENTRY" | "COMPLETION" | "RESULT",
    files: File[]
  ) {
    if (!row.can_edit || busy || !files.length || data.loading || data.error)
      return
    setBusy(true)
    const currentView = guardView()
    try {
      const outcome = await uploadProviderReportImages({
        reportId: row.id,
        kind,
        files,
        existingCount: row.result_image_urls.length,
        reload: data.reload,
        isCurrent: currentView,
      })
      if (outcome.stale) return
      setNotice(
        outcome.error
          ? `已确认上传 ${outcome.uploaded} 张；${uncertain(outcome.error) ? "当前图片结果未确认，请核对截图，后续图片未上传。" : message(outcome.error)}`
          : `已上传 ${outcome.uploaded} 张${outcome.skipped ? `，另有 ${outcome.skipped} 张超出上限未上传` : ""}。`
      )
    } finally {
      if (currentView()) setBusy(false)
    }
  }
  async function operate() {
    const target = submitTarget || withdrawTarget || deleteTarget?.report
    if (!target || busy || data.loading || data.error) return
    const currentView = guardView()
    setBusy(true)
    try {
      const outcome = await mutateAndReload({
        role: "provider",
        isCurrent: currentView,
        mutate: () => {
          if (submitTarget)
            return api(`/wallet/reports/${target.id}/submit/`, { method: "POST" })
          if (withdrawTarget)
            return api(`/wallet/reports/${target.id}/withdraw/`, { method: "POST" })
          return api(
            `/wallet/reports/${target.id}/images/${deleteTarget!.image}/`,
            { method: "DELETE" }
          )
        },
        reload: data.reload,
      })
      if (outcome.stale) return
      if (outcome.error) throw outcome.error
      setNotice(
        submitTarget ? "已提交审核" : withdrawTarget ? "已撤回审核" : "截图已删除"
      )
    } catch (cause) {
      if (!currentView()) return
      setNotice(
        uncertain(cause) ? "操作结果未确认，请核对报单。" : message(cause)
      )
    } finally {
      if (currentView()) {
        setSubmitTarget(null)
        setWithdrawTarget(null)
        setDeleteTarget(null)
        setBusy(false)
      }
    }
  }
  return (
    <div className="space-y-6">
      <PageHeader title="我的报单" description="核对订单凭证、审核结果与实际结算。" compact>
        <Button
          size="sm"
          className="rounded-[6px]"
          disabled={busy || data.loading}
          onClick={() => {
            setChooser(true)
            setSelected(null)
            setSearch("")
            setQuery("")
            setChoicePage(1)
          }}
        >
          <Plus className="size-4" />
          <span className="hidden sm:inline">从订单创建</span>
          <span className="sm:hidden">创建</span>
        </Button>
      </PageHeader>
      <Notice tone={["报单已创建", "已提交审核", "已撤回审核", "截图已删除"].includes(notice) ? "success" : "info"}>{notice}</Notice>
      <SnapshotGate
        loading={data.loading}
        error={data.error}
        hasData={data.data != null}
        reload={() => data.reload().catch(() => {})}
      >
        {(focus || reportFocus) && (
          <div className="flex items-center justify-between text-sm">
            <span>关联订单报单</span>
            <Button
              variant="outline"
              onClick={() => {
                setParams({})
                setPage(1)
              }}
            >
              查看全部
            </Button>
          </div>
        )}
        <LoadState
          hasData={data.data != null}
          loading={data.loading}
          error={data.error}
          reload={() => data.reload().catch(() => {})}
          empty={!rows.length}
          emptyTitle="暂无报单"
          emptyDescription="接单后创建报单，服务截图会自动关联。"
          emptyAction={<Button className="h-11" disabled={busy} onClick={() => { setChooser(true); setSelected(null); setSearch(""); setQuery(""); setChoicePage(1) }}><Plus className="size-4" />从订单创建</Button>}
        >
          <div className="grid items-start gap-4 lg:grid-cols-2">
            {rows.map((row) => (
              <Panel key={row.id} className="shadow-sm">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="font-semibold">
                      {row.service_name || row.game_name}
                    </h2>
                    <p className="mt-1 font-mono text-xs text-muted-foreground">
                      {row.order_no}
                    </p>
                  </div>
                  <Status
                    value={row.status}
                    label={row.status_display}
                    tone={row.status === "PENDING" ? "info" : undefined}
                  />
                </div>
                <dl className="grid grid-cols-2 divide-x divide-border border-y py-3 text-xs">
                  {[
                    {
                      label: providerReportPresentation(row).incomeLabel,
                      value:
                        providerReportPresentation(row).displayIncome == null
                          ? "待核对"
                          : `${money(providerReportPresentation(row).displayIncome)} ${coinUnit()}`,
                    },
                    {
                      label: "凭证准备",
                      value: `${3 - missing(row).length} / 3 类`,
                    },
                  ].map((item, index) => (
                    <div
                      key={item.label}
                      className={`flex min-w-0 items-center justify-between gap-2 ${index === 0 ? "pr-3" : "pl-3"}`}
                    >
                      <dt className="text-muted-foreground">{item.label}</dt>
                      <dd className="text-sm font-semibold whitespace-nowrap">
                        {item.value}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: "入队截图", ready: !!row.entry_image_url },
                    { label: "结单截图", ready: !!row.completion_image_url },
                    {
                      label: "战绩截图",
                      ready: row.result_image_urls.length > 0,
                    },
                  ].map((item) => (
                    <div
                      key={item.label}
                      className={`flex min-h-20 flex-col items-center justify-center gap-1 rounded-[8px] border text-center text-xs ${item.ready ? "border-success/20 bg-success/5 text-success" : "border-dashed text-muted-foreground"}`}
                    >
                      {item.ready ? (
                        <CheckCircle2 className="size-5" />
                      ) : (
                        <ImagePlus className="size-5" />
                      )}
                      <span>{item.label}</span>
                    </div>
                  ))}
                </div>
                {row.fulfillment && (
                  <p className="text-xs leading-5 text-muted-foreground">履约目标：{row.fulfillment}</p>
                )}
                {row.submitted_at && (
                  <p className="text-xs text-muted-foreground">
                    提交时间：{date(row.submitted_at)}
                  </p>
                )}
                {providerReportPresentation(row).aftersaleOpen && (
                  <Notice>
                    售后处理中，个人收益结算以本单和钱包信息为准。
                  </Notice>
                )}
                {row.order_status === "CANCELLED" && (
                  <Notice>订单已取消，报单流程已停止。</Notice>
                )}
                {row.edit_block_reason && (
                  <Notice>{row.edit_block_reason}</Notice>
                )}
                {row.audit_remark && (
                  <Notice>审核备注：{row.audit_remark}</Notice>
                )}
                {row.status === "APPROVED" && (
                  <Notice>
                    {providerReportPresentation(row).settlementNotice}
                  </Notice>
                )}
                {providerReportPresentation(row).netIncome != null && (
                  <p className="text-sm">
                    本人服务收益净额{" "}
                    {money(providerReportPresentation(row).netIncome)} {coinText(" 浪花 · 本单剩余冻结")}{" "}
                    {providerReportPresentation(row).remainingFrozen == null
                      ? "待核对"
                      : money(providerReportPresentation(row).remainingFrozen) +
                        coinText(" 浪花")}
                  </p>
                )}
                {row.refund_summary && (
                  <RefundSummary refund={row.refund_summary} />
                )}
                <details className="group/evidence overflow-hidden rounded-[8px] border bg-muted/10">
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 text-xs font-medium [&::-webkit-details-marker]:hidden">
                  <span className="flex items-center gap-2"><FileCheck2 className="size-4 text-primary" />{row.can_edit ? "查看与补充凭证" : "查看服务凭证"}</span>
                  <ChevronDown className="size-4 text-muted-foreground transition-transform group-open/evidence:rotate-180" />
                </summary>
                <div className="space-y-4 border-t p-3">
                <section className="space-y-3">
                  <h3 className="text-sm font-semibold">服务凭证</h3>
                  <div className="grid gap-4 sm:grid-cols-2">
                    {(
                      [
                        {
                          kind: "ENTRY",
                          label: "入队截图",
                          url: row.entry_image_url,
                        },
                        {
                          kind: "COMPLETION",
                          label: "结单截图",
                          url: row.completion_image_url,
                        },
                      ] as const
                    ).map((item) => (
                      <div className="space-y-2" key={item.kind}>
                        <p className="text-sm font-medium">{item.label}</p>
                        {item.url ? (
                          <PrivateImage src={item.url} alt={item.label} />
                        ) : (
                          <p className="rounded-md border border-dashed p-6 text-center text-xs text-muted-foreground">
                            暂无截图
                          </p>
                        )}
                        {row.evidence_upload_block_reasons?.[item.kind] ? (
                          <div>
                            <p className="text-xs text-muted-foreground">
                              {row.evidence_upload_block_reasons[item.kind]}
                            </p>
                            <Button variant="link" asChild>
                              <Link to={`/orders?order=${row.order_id}`}>
                                前往订单提交
                              </Link>
                            </Button>
                          </div>
                        ) : (
                          row.can_edit && (
                            <Field label={item.url ? "更换截图" : "上传截图"}>
                              <UploadField
                                accept="image/*"
                                disabled={busy}
                                onChange={(e) => {
                                  const files = Array.from(e.target.files || [])
                                  e.target.value = ""
                                  void upload(row, item.kind, files)
                                }}
                              />
                            </Field>
                          )
                        )}
                      </div>
                    ))}
                  </div>
                </section>
                <section className="space-y-3 border-t pt-4">
                  <div className="flex justify-between text-sm">
                    <h3 className="font-semibold">战绩截图</h3>
                    <span className="text-xs text-muted-foreground">
                      {row.result_image_urls.length} / 9 张
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    至少 1 张，最多 9 张；提交审核后仍可逐张查看。
                  </p>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    {row.result_images.map((picture, index) => (
                      <div key={picture.id} className="space-y-2">
                        <PrivateImage
                          src={picture.url}
                          alt={`战绩截图${index + 1}`}
                        />
                        {row.can_edit && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="w-full rounded-[6px] px-2 text-xs text-muted-foreground"
                            disabled={busy}
                            onClick={() =>
                              setDeleteTarget({
                                report: row,
                                image: picture.id,
                              })
                            }
                          >
                            删除此图
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                  {!row.result_images.length && (
                    <p className="text-xs text-muted-foreground">
                      至少需要一张战绩截图。
                    </p>
                  )}
                  {row.can_edit && (
                    <Field label="添加战绩截图">
                      <UploadField
                        multiple
                        accept="image/*"
                        disabled={busy || row.result_image_urls.length >= 9}
                        onChange={(e) => {
                          const files = Array.from(e.target.files || [])
                          e.target.value = ""
                          void upload(row, "RESULT", files)
                        }}
                      />
                    </Field>
                  )}
                </section>
                </div>
                </details>
                {(row.can_edit || providerReportPresentation(row).canWithdraw) && (
                  <div className="provider-report-actions flex flex-nowrap items-center justify-between gap-3 border-t pt-3">
                    <p className="min-w-0 text-xs leading-5 text-muted-foreground">
                      {providerReportPresentation(row).canWithdraw
                        ? "审核完成前可撤回，全部凭证都会保留。"
                        : missing(row).length
                          ? `待补齐：${missing(row).join("、")}`
                          : "三类凭证已齐全"}
                    </p>
                    {row.can_edit && (
                      <Button
                        size="sm"
                        className="shrink-0 rounded-[6px]"
                        disabled={
                          busy || !providerReportPresentation(row).canSubmit
                        }
                        onClick={() => setSubmitTarget(row)}
                      >
                        <FileCheck2 className="size-3.5" />
                        {row.can_submit ? "提交审核" : "服务完成后提交"}
                      </Button>
                    )}
                    {providerReportPresentation(row).canWithdraw && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="shrink-0 rounded-[6px]"
                        disabled={busy}
                        onClick={() => setWithdrawTarget(row)}
                      >
                        <Undo2 className="size-3.5" />
                        撤回审核
                      </Button>
                    )}
                  </div>
                )}
              </Panel>
            ))}
          </div>
        </LoadState>
        {focus && !data.loading && !rows.length && (
          <Panel>
            <p className="text-sm">
              {eligibility.data?.report_entry?.reason ||
                "核对订单后可创建报单。"}
            </p>
            {eligibility.data?.report_entry?.available && (
              <Button
                disabled={busy}
                onClick={() => void create(Number(focus))}
              >
                为此订单创建报单
              </Button>
            )}
            <Notice error>{eligibility.error}</Notice>
          </Panel>
        )}
        <Pager
          page={page}
          total={data.data?.total || 0}
          change={setPage}
          disabled={data.loading}
        />
      </SnapshotGate>
      <Confirm
        open={chooser}
        title="选择报单订单"
        description="请选择需要补充凭证的订单，已有报单可继续编辑。"
        onClose={() => setChooser(false)}
        onConfirm={() => selected && void create(selected.id)}
        busy={busy}
        disabled={!selected}
        label="创建报单"
      >
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setQuery(search.trim())
            setChoicePage(1)
            setSelected(null)
          }}
        >
          <Input
            aria-label="搜索订单"
            placeholder="输入订单号或服务名称…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Button variant="outline" type="submit">
            搜索
          </Button>
        </form>
        <LoadState
          hasData={choices.data != null}
          loading={choices.loading}
          error={choices.error}
          reload={() => choices.reload().catch(() => {})}
          empty={!choices.data?.list.length}
          emptyTitle="暂无可创建报单的订单"
          emptyDescription="可补充凭证的订单会在这里显示。"
        >
          {choices.data?.list.map((row) => (
            <button
              key={row.id}
              className={`flex min-h-16 w-full items-center gap-3 rounded-[8px] border p-3 text-left ${selected?.id === row.id ? "border-primary bg-primary/5" : ""}`}
              onClick={() => setSelected(row)}
              aria-pressed={selected?.id === row.id}
            >
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">
                  {row.service_name}
                </span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  {row.order_no}
                </span>
              </span>
              {selected?.id === row.id && (
                <Check className="size-4 text-primary" />
              )}
            </button>
          ))}
        </LoadState>
        <Pager
          page={choicePage}
          total={choices.data?.total || 0}
          change={setChoicePage}
        />
      </Confirm>
      <Confirm
        open={!!submitTarget || !!deleteTarget}
        confirmVariant={submitTarget ? "default" : "destructive"}
        label={submitTarget ? "提交审核" : "删除截图"}
        title={submitTarget ? "提交报单审核" : "删除战绩截图"}
        description={
          submitTarget
            ? "提交后等待审核，请确认三类凭证清晰且属于此订单。"
            : "请确认要删除这张战绩截图。"
        }
        onClose={() => {
          setSubmitTarget(null)
          setDeleteTarget(null)
        }}
        onConfirm={() => void operate()}
        busy={busy}
      >
        {submitTarget ? (
          <DetailGrid
            items={[
              { label: "订单", value: submitTarget.order_no },
              {
                label: "服务",
                value: submitTarget.service_name || submitTarget.game_name,
              },
              {
                label: "凭证准备",
                value: `${3 - missing(submitTarget).length} / 3 类`,
              },
            ]}
          />
        ) : (
          deleteTarget && (
            <>
              <DetailGrid
                items={[
                  { label: "订单", value: deleteTarget.report.order_no },
                  {
                    label: "服务",
                    value:
                      deleteTarget.report.service_name ||
                      deleteTarget.report.game_name,
                  },
                ]}
              />
              {deleteTarget.report.result_images.find(
                (image) => image.id === deleteTarget.image
              )?.url && (
                <PrivateImage
                  src={
                    deleteTarget.report.result_images.find(
                      (image) => image.id === deleteTarget.image
                    )!.url
                  }
                  alt="将删除的战绩截图"
                />
              )}
            </>
          )
        )}
      </Confirm>
      <Confirm
        open={!!withdrawTarget}
        label="撤回审核"
        title="撤回报单审核"
        description="撤回后将回到待提交，全部凭证保留，可修改后重新提交。"
        onClose={() => setWithdrawTarget(null)}
        onConfirm={() => void operate()}
        busy={busy}
      >
        {withdrawTarget && (
          <DetailGrid
            items={[
              { label: "订单", value: withdrawTarget.order_no },
              {
                label: "服务",
                value: withdrawTarget.service_name || withdrawTarget.game_name,
              },
              { label: "当前状态", value: withdrawTarget.status_display },
            ]}
          />
        )}
      </Confirm>
    </div>
  )
}
