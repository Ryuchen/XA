import { useEffect, useId, useState } from "react"
import { coinUnit } from "@xa/shared"
import { errorMessage, request } from "@/api/request"
import { Feedback, type Row } from "@/components/business"
import {
  normalizeStaffSettlementBreakdown,
  settlementMoney,
  type SettlementBreakdownView,
  type StaffSettlementBreakdownView,
} from "@/lib/order-settlement-breakdown"

const statusText: Record<string, string> = {
  REVIEW: "待核对",
  PENDING: "待入账",
  PARTIAL: "部分入账",
  FROZEN: "已入账冻结",
  POSTED: "已入账",
}

function Amount({ label, value }: { label: string; value: number | null }) {
  return <div>
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="mt-1 tabular-nums">{settlementMoney(value)} {value === null ? "" : coinUnit()}</p>
  </div>
}

function Unknowns({ items }: { items: SettlementBreakdownView["unknownItems"] }) {
  if (!items.length) return null
  return <ul className="space-y-1 rounded-md bg-muted/50 p-3 text-xs leading-5 text-muted-foreground">
    {items.map((item, index) => <li key={`${item.code || "unknown"}:${item.segmentId || ""}:${item.paymentId || ""}:${index}`}>
      {item.message}{item.code ? `（${item.code}）` : ""}
    </li>)}
  </ul>
}

function ProviderBreakdown({ provider }: { provider: SettlementBreakdownView }) {
  return <article className="space-y-3 rounded-lg border p-4" aria-label={`陪玩 ${provider.scope || "未知"} 的逐段收益`}>
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h4 className="font-medium">陪玩 · {provider.scope || "范围待核对"}</h4>
        <p className="mt-1 text-xs text-muted-foreground">账号 #{provider.providerAccountId || "—"} · {provider.status ? statusText[provider.status] : "结算状态待核对"}</p>
      </div>
      {provider.generatedAt && <p className="text-xs text-muted-foreground">读取时间 {provider.generatedAt}</p>}
    </div>
    {provider.state === "empty" ? <p role="status" className="text-sm text-muted-foreground">没有可展示的逐段收益记录；仅保留服务端合计。</p> : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Amount label="主单净收益" value={provider.totals.mainNet} />
      <Amount label="加购净收益" value={provider.totals.addonNet} />
      <Amount label="待入账" value={provider.totals.pending} />
      <Amount label="已入账（含冻结）" value={provider.totals.posted} />
      <Amount label="其中冻结" value={provider.totals.frozen} />
      <Amount label="收益总额" value={provider.totals.total} />
    </div>}
    {provider.lines.map(line => <div key={line.key} className="space-y-2 rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><h5 className="font-medium">{line.title}</h5>{line.detail && <p className="mt-1 text-xs text-muted-foreground">{line.detail}</p>}</div>
        <p className="text-xs text-muted-foreground">{line.status ? statusText[line.status] : "状态待核对"}</p>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Amount label="净收益" value={line.net} />
        <Amount label="待入账" value={line.pending} />
        <Amount label="冻结" value={line.frozen} />
        <Amount label="已入账" value={line.posted} />
      </div>
      {line.transactions.length > 0 && <ul className="space-y-1 text-xs text-muted-foreground">
        {line.transactions.map(tx => <li key={tx.key}>流水 {tx.transactionNo || tx.key} · {tx.txType || "类型待核对"} · {settlementMoney(tx.amountRaw)} {coinUnit()}</li>)}
      </ul>}
      <Unknowns items={line.unknownItems} />
    </div>)}
    <Unknowns items={provider.unknownItems} />
  </article>
}

export default function OrderSettlementBreakdown({ orderId, revision, enabled = true }: {
  orderId: number
  revision: Row
  enabled?: boolean
}) {
  const titleId = useId()
  const [view, setView] = useState<StaffSettlementBreakdownView | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    setView(null)
    setError("")
    setLoading(true)
    request({ url: `/orders/${orderId}/settlement-breakdown/`, signal: controller.signal })
      .then(result => setView(normalizeStaffSettlementBreakdown(result.data, orderId)))
      .catch(cause => {
        if (!controller.signal.aborted) setError(errorMessage(cause))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [orderId, revision, enabled])

  if (!enabled) return null
  return <section className="space-y-3 rounded-xl border p-4" aria-labelledby={titleId} data-testid="order-settlement-breakdown">
    <div>
      <h3 id={titleId} className="font-semibold">逐段收益</h3>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">按本订单实际参与的陪玩分别展示。已入账包含冻结金额；各陪玩收益不合并为门店利润。</p>
    </div>
    {loading && <p role="status" className="text-sm text-muted-foreground">正在读取逐段收益…</p>}
    <Feedback message={error} />
    {view?.state === "unsupported" || view?.state === "mismatch"
      ? <p role="alert" className="rounded-md border p-3 text-sm">{view.unknownItems[0]?.message}</p>
      : view?.state === "empty"
        ? <p role="status" className="text-sm text-muted-foreground">订单尚未关联可读取的陪玩收益范围，未展示金额。</p>
        : view?.providers.map(provider => <ProviderBreakdown key={provider.providerAccountId || provider.scope || "unknown"} provider={provider} />)}
    {view && <Unknowns items={view.unknownItems} />}
  </section>
}
