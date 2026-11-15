import { RecordSection as FormSection } from "./OrderRecordVisual"
import { coinText, coinUnit } from "@xa/shared"
import { useEffect, useRef, useState } from "react"
import { getSession } from "@xa/shared"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  DataTable,
  Feedback,
  MutationDialog,
  type Row,
} from "@/components/business"
import { request, requestId } from "@/api/request"
import { amountToXaCoin, xaCoinToAmount } from "@/lib/format"

function parseAmount(value: string, positive = false) {
  if (!/^\d+(?:\.\d)?$/.test(value.trim()))
    throw new Error(coinText("浪花金额须为非负数，最多一位小数。"))
  const amount = xaCoinToAmount(value)
  if (!Number.isSafeInteger(amount) || (positive && amount <= 0))
    throw new Error("请填写有效的正数金额。")
  return amount
}
export default function RefundDialog({
  order,
  onClose,
  onSaved,
}: {
  order: Row
  onClose: () => void
  onSaved: () => void
}) {
  const [amount, setAmount] = useState("")
  const [reason, setReason] = useState("")
  const [quote, setQuote] = useState<Row | null>(null)
  const [negotiated, setNegotiated] = useState<Row[]>([])
  const [subsidy, setSubsidy] = useState<Row | null>(null)
  const [segmentMode, setSegmentMode] = useState(false)
  const [segmentCandidates, setSegmentCandidates] = useState<Row[]>([])
  const [affected, setAffected] = useState<number[]>([])
  const [scopeLocked, setScopeLocked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [reviewed, setReviewed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [intent, setIntent] = useState(() => requestId("refund"))
  const sequence = useRef(0)
  const alive = useRef(true)
  const owner = useRef(getSession("admin").revision)
  const current = (id: number) =>
    alive.current &&
    sequence.current === id &&
    getSession("admin").revision === owner.current
  function invalidate() {
    ++sequence.current
    setQuote(null)
    setReviewed(false)
    setBusy(false)
    setIntent(requestId("refund"))
  }
  async function preview(initialize = false) {
    const id = ++sequence.current
    setBusy(true)
    setError("")
    setQuote(null)
    setReviewed(false)
    try {
      let response: Row
      if (segmentMode) {
        response = (await request({ url: `/orders/${order.id}/segment-refund-preview/`, method: "POST",
          data: { refund_amount: parseAmount(amount, true), affected_provider_account_ids: affected } })).data
        if (current(id)) { setSegmentCandidates(response.candidates || []); setScopeLocked(!!response.scope_locked) }
      } else if (subsidy) {
        const allocations = subsidy.shares.map((row: Row) => ({
          share_id: row.share_id,
          amount: parseAmount(row.input),
        }))
        response = (
          await request({
            url: `/orders/${order.id}/subsidy-negotiation-preview/`,
            method: "POST",
            data: { allocations },
          })
        ).data
      } else if (negotiated.length) {
        const raw = parseAmount(amount, true)
        const allocations = negotiated.map((row) => ({
          key: row.key,
          amount: parseAmount(row.input),
        }))
        if (allocations.reduce((sum, row) => sum + row.amount, 0) !== raw)
          throw new Error("各方承担金额合计须等于本次退款金额。")
        response = (
          await request({
            url: `/orders/${order.id}/negotiated-refund-preview/`,
            method: "POST",
            data: { refund_amount: raw, allocations },
          })
        ).data
      } else
        response = (
          await request({
            url: `/orders/${order.id}/refund-preview/`,
            params: initialize
              ? undefined
              : { refund_amount: parseAmount(amount, true) },
          })
        ).data
      if (!current(id)) return
      if (Number(response.order_id) !== Number(order.id))
        throw new Error("试算返回的订单不匹配，请重新读取。")
      if (
        !subsidy &&
        !initialize &&
        response.refund_amount !== parseAmount(amount, true)
      )
        throw new Error("试算金额与输入不一致，请重新试算。")
      if (initialize && response.refund_amount != null)
        setAmount(amountToXaCoin(response.refund_amount))
      setQuote(response)
    } catch (cause: any) {
      if (!current(id)) return
      const code = cause.errorCode || cause.body?.errorCode
      if (initialize && code === "SEGMENT_SETTLEMENT_REFUND_REVIEW") {
        try {
          const raw = Number(order.amount) - Number(order.refund_summary?.amount || 0)
          const data = (await request({ url: `/orders/${order.id}/segment-refund-preview/`, method: "POST",
            data: { refund_amount: raw } })).data
          if (!current(id)) return
          if (Number(data.order_id) !== Number(order.id) || data.refund_amount !== raw)
            throw new Error("核对结果与订单不一致，请重新读取。")
          setSegmentMode(true); setSegmentCandidates(data.candidates || []); setAffected(data.scope || [])
          setScopeLocked(!!data.scope_locked); setAmount(String(raw / 10)); setQuote(data)
          setError("")
        } catch (nested: any) { if (current(id)) setError(nested.message || "逐段退款核对暂不可用。") }
      } else
      if (
        initialize &&
        ["SUBSIDY_NEGOTIATION_REQUIRED", "REFUND_LEDGER_INCOMPLETE"].includes(
          code
        )
      ) {
        try {
          if (code === "SUBSIDY_NEGOTIATION_REQUIRED") {
            const data = (
              await request({
                url: `/orders/${order.id}/subsidy-negotiation-options/`,
              })
            ).data
            if (!current(id)) return
            setSubsidy({
              ...data,
              shares: data.shares.map((row: Row) => ({
                ...row,
                input: amountToXaCoin(row.expected_income),
              })),
            })
            setError("请按已协商结果填写各陪玩保留报酬，再试算终止方案。")
          } else {
            const data = (
              await request<Row[]>({
                url: `/orders/${order.id}/negotiated-refund-options/`,
              })
            ).data
            if (!current(id)) return
            setNegotiated(data.map((row) => ({ ...row, input: "" })))
            setError("原始流水不完整，需要逐项核对各方承担金额后协商退款。")
          }
        } catch (nested: any) {
          if (current(id))
            setError(nested.message || "核账信息读取失败，请刷新订单重试。")
        }
      } else setError(cause.message || "试算未完成，请核对输入后重试。")
    } finally {
      if (current(id)) setBusy(false)
    }
  }
  useEffect(() => {
    alive.current = true
    void preview(true)
    return () => {
      alive.current = false
      ++sequence.current
    }
  }, [])
  const eligible = subsidy
    ? quote?.recovery_available
    : quote?.eligible && !quote?.evidence_error && !quote?.shortfalls?.length
  return (
    <MutationDialog
      contentClassName="admin-order-refund-dialog sm:max-w-[1120px]"
      submitVariant="destructive"
      title={
        segmentMode
          ? "逐段退款分摊核对"
          : subsidy
          ? "安全箱报酬协商终止"
          : negotiated.length
            ? "协商核账退款"
            : "退款试算与确认"
      }
      description={`${order.order_no} · ${segmentMode ? "当前仅供核对，不能提交退款。" : subsidy ? "核对陪玩保留报酬、追回金额和平台补付，再确认终止。" : "退款退回老板余额，各方收益与权益按本次试算方案处理。"}`}
      onClose={onClose}
      disabled={
        !quote ||
        !eligible ||
        !quote.plan_token ||
        busy ||
        reason.trim().length < 2 ||
        !reviewed ||
        unknown
      }
      submitText={subsidy ? "确认协商终止" : "确认退款"}
      onSubmit={async () => {
        if (!quote || !eligible || !reviewed) return
        const data: Row = {
          reason: reason.trim(),
          plan_token: quote.plan_token,
          client_request_id: intent,
        }
        let path = "refund"
        if (subsidy) {
          path = "subsidy-negotiation-confirm"
          data.allocations = quote.shares.map((row: Row) => ({
            share_id: row.share_id,
            amount: row.agreed_income,
          }))
          data.reviewed = true
        } else {
          data.refund_amount = quote.refund_amount
          if (negotiated.length) {
            path = "negotiated-refund"
            data.allocations = negotiated.map((row) => ({
              key: row.key,
              amount: parseAmount(row.input),
            }))
            data.reviewed = true
          }
        }
        const target =
          order.ticketId && path === "refund"
            ? `/aftersales/${order.ticketId}/refund/`
            : `/orders/${order.id}/${path}/`
        if (order.ticketId && path === "refund") {
          data.resolution = data.reason
          delete data.reason
        }
        try {
          await request({
            url: target,
            method: "POST",
            data,
          })
          toast.success(
            subsidy ? "已协商终止，请核对报酬结算状态" : "退款已完成"
          )
          onSaved()
        } catch (cause: any) {
          setQuote(null)
          setReviewed(false)
          if (
            !cause.status ||
            cause.status >= 500 ||
            [408, 409].includes(cause.status)
          )
            setUnknown(true)
          throw cause
        }
      }}
    >
      <section
        className="admin-refund-original-summary"
        aria-label="订单退款金额摘要"
      >
        {[
          ["原实付", order.amount],
          ["累计已退", order.refund_summary?.amount ?? 0],
          [
            "剩余可退",
            Math.max(
              0,
              Number(order.amount) - Number(order.refund_summary?.amount || 0)
            ),
          ],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <p>{label}</p>
            <strong>
              {amountToXaCoin(value)}
              <small>{coinUnit()}</small>
            </strong>
          </div>
        ))}
      </section>
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <FormSection title="退款与承担方案">
          <fieldset disabled={unknown || busy} className="space-y-4">
            {subsidy ? (
              <>
                <p className="text-sm">优惠券处理：{subsidy.coupon_outcome}</p>
                <DataTable
                  rows={subsidy.shares}
                  columns={[
                    { key: "provider_name", label: "陪玩" },
                    {
                      align: "right",
                      key: "expected_income",
                      label: "原预计报酬",
                      render: (row) =>
                        `${amountToXaCoin(row.expected_income)} ${coinUnit()}`,
                    },
                    {
                      align: "right",
                      key: "paid_income",
                      label: "已支付",
                      render: (row) =>
                        `${amountToXaCoin(row.paid_income)} ${coinUnit()}`,
                    },
                    {
                      key: "input",
                      label: coinText("协商保留报酬（浪花）"),
                      render: (row) => (
                        <Input
                          aria-label={`${row.provider_name} 协商保留报酬`}
                          type="number"
                          min="0"
                          step="0.1"
                          value={row.input}
                          onChange={(event) => {
                            invalidate()
                            setSubsidy((old) =>
                              old
                                ? {
                                    ...old,
                                    shares: old.shares.map((x: Row) =>
                                      x.share_id === row.share_id
                                        ? { ...x, input: event.target.value }
                                        : x
                                    ),
                                  }
                                : old
                            )
                          }}
                        />
                      ),
                    },
                  ]}
                />
              </>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="refund-amount">
                  {coinText("本次退款（浪花）")}
                </Label>
                <Input
                  id="refund-amount"
                  className="h-11"
                  type="number"
                  min={segmentMode ? "1" : "0.1"}
                  step={segmentMode ? "1" : "0.1"}
                  value={amount}
                  onChange={(event) => {
                    invalidate()
                    setAmount(event.target.value)
                  }}
                />
              </div>
            )}
            {negotiated.length > 0 && (
              <DataTable
                rows={negotiated}
                columns={[
                  { key: "name", label: "参与方" },
                  {
                    align: "right",
                    key: "entitlement",
                    label: "原权益金额",
                    render: (row) =>
                      `${amountToXaCoin(row.entitlement)} ${coinUnit()}`,
                  },
                  {
                    key: "settled",
                    label: "已结算",
                    render: (row) => (row.settled ? "是" : "否"),
                  },
                  {
                    key: "input",
                    label: coinText("协商承担（浪花）"),
                    render: (row) => (
                      <Input
                        type="number"
                        min="0"
                        step="0.1"
                        aria-label={`${row.name} 承担金额`}
                        value={row.input}
                        onChange={(event) => {
                          invalidate()
                          setNegotiated((old) =>
                            old.map((x) =>
                              x.key === row.key
                                ? { ...x, input: event.target.value }
                                : x
                            )
                          )
                        }}
                      />
                    ),
                  },
                ]}
              />
            )}
            {segmentMode && segmentCandidates.length > 0 && <div className="space-y-2">
              <p className="text-sm font-medium">受影响陪玩{scopeLocked ? "（按本次售后范围）" : ""}</p>
              {segmentCandidates.map((person: Row) => <label key={person.account_id} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={affected.includes(person.account_id)} disabled={scopeLocked || busy || unknown}
                  onChange={event => {
                    const checked = event.target.checked
                    invalidate()
                    setAffected(previous => checked ? [...previous, person.account_id].sort((a,b) => a-b)
                      : previous.filter(id => id !== person.account_id))
                  }} />
                {person.name} · 原价份额 {amountToXaCoin(person.original_share)} {coinUnit()}
              </label>)}
            </div>}
            <Button
              type="button"
              variant="outline"
              disabled={busy || unknown || (segmentMode && !affected.length)}
              onClick={() => void preview()}
            >
              {busy ? "正在试算" : "读取最新试算"}
            </Button>
          </fieldset>
          <Feedback message={error} />
        </FormSection>
        <FormSection title="资金影响试算" className="bg-muted/15">
          {!quote && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {busy
                ? "正在读取最新试算…"
                : "读取最新试算后核对资金与权益处理。"}
            </p>
          )}
          {quote && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-4 rounded-lg border bg-muted/40 p-4">
                {(subsidy
                  ? [
                      ["agreed_total", "保留报酬合计"],
                      ["recovery_total", "需要追回"],
                      ["unpaid_total", "需要补付"],
                      ["platform_available", "平台可用余额"],
                    ]
                  : [
                      ["original_amount", "原订单金额"],
                      ["refunded_amount", "累计已退"],
                      ["remaining_amount", "剩余可退"],
                      ["refund_amount", "本次退款"],
                      ["remaining_after_refund", "退款后剩余"],
                    ]
                ).map(([key, label]) => (
                  <div key={key}>
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <p className="mt-1 font-semibold tabular-nums">
                      {quote[key] == null
                        ? "待核对"
                        : `${amountToXaCoin(quote[key])} ${coinUnit()}`}
                    </p>
                  </div>
                ))}
              </div>
              {subsidy && (
                <DataTable
                  rows={quote.shares || []}
                  columns={[
                    { key: "provider_name", label: "陪玩" },
                    ...(
                      [
                        ["agreed_income", "保留报酬"],
                        ["recovery_required", "追回"],
                        ["unpaid_income", "补付"],
                      ] as const
                    ).map(([key, label]) => ({
                      key,
                      label,
                      render: (row: Row) =>
                        `${amountToXaCoin(row[key])} ${coinUnit()}`,
                    })),
                  ]}
                />
              )}
              {segmentMode ? <>
                <p className="text-sm">每位陪玩的承担金额已向上取整；预计门店取整差额 {amountToXaCoin(quote.rounding_to_store)} {coinUnit()}。</p>
                <DataTable rowKey="account_id" rows={quote.burdens || []} columns={[
                  { key:"name", label:"陪玩" },
                  ...([["burden", "承担"], ["pending_income", "本单待结算"], ["wallet_amount", "钱包可用"],
                    ["deposit_amount", "押金"], ["shortfall", "仍缺"]] as const).map(([key, label]) => ({
                    key, label, render:(row: Row) => `${amountToXaCoin(row[key])} ${coinUnit()}`,
                  })),
                ]} />
                {!!quote.payment_sources?.length && <p className="text-sm">退回来源：{quote.payment_sources.map((item: Row) =>
                  `${({ PREPAID:"本金", GIFT:"赠送", COMPENSATION:"赔付" } as Row)[item.kind] || item.kind} ${amountToXaCoin(item.amount)} ${coinUnit()}`).join("、")}</p>}
                {quote.payment_source_note && <p className="text-sm">{quote.payment_source_note}</p>}
                <p className="text-sm">{quote.reason}。资金和押金可用性仍需执行前重新核对。</p>
              </> : <RecoveryTable rows={quote.recoveries || []} />}
              {!eligible && (
                <Feedback
                  message={
                    (segmentMode ? quote.reason : quote.evidence_error) ||
                    "当前方案不能提交；请核对追回缺口、收益来源或平台可用余额。"
                  }
                />
              )}
            </div>
          )}
        </FormSection>
      </div>
      <div className="space-y-2">
        <Label htmlFor="refund-reason">处理原因（至少两个字）</Label>
        <Textarea
          id="refund-reason"
          maxLength={255}
          value={reason}
          disabled={unknown}
          onChange={(event) => {
            setReason(event.target.value)
            setReviewed(false)
            setIntent(requestId("refund"))
          }}
        />
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-blue-600"
          checked={reviewed}
          onChange={(event) => setReviewed(event.target.checked)}
          disabled={!quote || !eligible || unknown}
        />
        已与相关参与方核对金额、收益追回、补付和权益处理方案
      </label>
      {unknown && (
        <Feedback message="提交结果未确认。请关闭后读取订单、钱包与退款记录核对；本次请求可能已完成，避免重复提交。" />
      )}
    </MutationDialog>
  )
}
function RecoveryTable({ rows }: { rows: Row[] }) {
  return (
    <DataTable
      title="各方收益追回与缺口"
      minWidth={680}
      rows={rows}
      rowKey="wallet_id"
      columns={[
        {
          key: "role",
          label: "账户",
          render: (row) => (
            <span className="whitespace-nowrap">
              {(
                { PLATFORM: "平台", PROVIDER: "陪玩", INVITER: "推荐人" } as Row
              )[row.role] || row.role}
              {row.account_id ? ` · #${row.account_id}` : ""}
            </span>
          ),
        },
        ...(
          [
            ["income_amount", "原收益"],
            ["held_amount", "冻结收益"],
            ["deposit_amount", "押金追回"],
            ["available_required", "余额需扣"],
            ["available_balance", "可用余额"],
            ["shortfall", "缺口"],
          ] as const
        ).map(([key, label]) => ({
          key,
          label,
          render: (row: Row) => `${amountToXaCoin(row[key])} ${coinUnit()}`,
        })),
        { key: "explanation", label: "核对说明" },
      ]}
    />
  )
}
