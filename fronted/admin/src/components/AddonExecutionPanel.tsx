import { useId, useRef, useState } from "react"
import { coinUnit, getStoreSlug } from "@xa/shared"
import { errorMessage, request } from "@/api/request"
import { Feedback, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import type { AddonProposalTarget } from "@/lib/addon-first-proposal"
import { captureAddonExecution, checkAddonExecution, executeAddonExecution, parseAddonExecution, type AddonExecutionIntent } from "@/lib/addon-execution"

export default function AddonExecutionPanel({ target, proposals, loading, readError, blocked, onLoaded }: {
  target: AddonProposalTarget; proposals: Row[] | null; loading: boolean; readError?: string; blocked: boolean; onLoaded: (rows: Row[]) => void
}) {
  const auth = useAdminAuth()
  const key = `xa:store:${getStoreSlug()}:admin:addon-execute:${auth.profile.id}:${target.orderId}:${target.paymentId}:${target.caseId}`
  const [restored] = useState(() => {
    try { return { intent: parseAddonExecution(sessionStorage.getItem(key), target), error: "" } }
    catch (cause) { return { intent: null, error: errorMessage(cause) } }
  })
  const [pending, setPending] = useState<AddonExecutionIntent | null>(restored.intent)
  const [selected, setSelected] = useState<AddonExecutionIntent | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [error, setError] = useState(restored.error)
  const [receipt, setReceipt] = useState<Row | null>(null)
  const formId = useId()
  const current = pending || selected
  const canRefund = auth.hasPerm("order:refund")
  const candidates = (proposals || []).filter(row => row.payment_id === target.paymentId && row.case_id === target.caseId && row.case_request_id === target.caseRequestId && row.status === "ACCEPTED" && row.refund_executed !== true)
  function clear() { sessionStorage.removeItem(key); setPending(null) }
  function select(proposal: Row) {
    if (busy || blocked || loading || readError || pending || restored.error || !canRefund) return
    try { setSelected(captureAddonExecution(proposal, target)); setConfirmed(false); setError(""); setReceipt(null) }
    catch (cause) { setError(errorMessage(cause)) }
  }
  async function run(checkOnly = false) {
    if (!current || inFlight.current || restored.error) return
    if (!checkOnly && (!canRefund || !confirmed || !pending && (blocked || loading || !!readError))) {
      setError("请重新读取并核对同一已签方案及金额，再明确确认执行。")
      return
    }
    inFlight.current = true
    setBusy(true)
    setError("")
    const endpoint = `/orders/${current.target.orderId}/addons/payments/${current.target.paymentId}/refund-proposals/`
    const read = async () => {
      const rows = (await request({ url: endpoint })).data
      if (Array.isArray(rows)) onLoaded(rows)
      return rows
    }
    try {
      const value = checkOnly
        ? await checkAddonExecution(current, { read, clear })
        : await executeAddonExecution(current, {
          read, clear,
          save: value => { try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { throw new Error("无法保存原方案执行凭证，请检查浏览器存储后重试。") }; setPending(value) },
          send: async value => (await request({ url: `${endpoint}${value.proposalId}/execute/`, method: "POST", data: {} })).data,
        }, !!pending)
      setReceipt(value)
      setSelected(null)
      setConfirmed(false)
      try { await read() } catch { /* The matching refund receipt remains known even if list refresh fails. */ }
    } catch (cause) {
      setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause))
      setConfirmed(false)
    } finally { inFlight.current = false; setBusy(false) }
  }

  if (!candidates.length && !current && !receipt && !restored.error) return null
  return <section className="space-y-3 rounded-lg border p-4 text-sm">
    <h3 className="font-medium">执行指定已签加购方案</h3>
    <Feedback message={error} />
    {receipt && <div role="status" className="space-y-1">
      <p>已核对方案 #{receipt.id} 的退款：{amountToXaCoin(receipt.execution_receipt.amount_raw)} {coinUnit()}。</p>
      <p>退款 #{receipt.execution_receipt.refund_id} · 老板返款流水 #{receipt.execution_receipt.buyer_transaction_id} · {formatDateTime(receipt.execution_receipt.executed_at)}</p>
    </div>}
    {!current && canRefund && candidates.map(proposal => <Button key={proposal.id} type="button" size="sm" variant="outline" disabled={busy || blocked || loading || !!readError || !!restored.error || proposal.execution_available !== true || proposal.refund_executed !== false} onClick={() => select(proposal)}>核对并执行方案 #{proposal.id}</Button>)}
    {!current && candidates.some(row => row.execution_available !== true) && <p className="text-muted-foreground">已签字仍需服务端确认当前执行资格，请核对方案和资金记录。</p>}
    {current && <form aria-labelledby={`${formId}-title`} className="space-y-3" onSubmit={event => { event.preventDefault(); void run() }}>
      <h4 id={`${formId}-title`} className="font-medium">原方案 #{current.proposalId} · 原付款 #{current.target.paymentId}</h4>
      <p>售后 #{current.target.caseId} · 已签退款金额 {amountToXaCoin(current.amountRaw)} {coinUnit()} · {current.target.accounts.map(id => `陪玩账号 #${id}`).join("、")}</p>
      <p className="whitespace-pre-wrap break-words">已签说明：{current.explanation}</p>
      {pending && <p role="status">原方案执行结果尚待核对，已保留原付款、方案和已签金额。若上次未实际退款，本次重试可能首次执行；若已执行，则核对同一笔退款。只重试原方案，不另选目标。</p>}
      <div className="flex min-h-10 items-start gap-2"><input id={`${formId}-confirm`} name="confirm_execution" type="checkbox" required checked={confirmed} disabled={busy || !canRefund} className="mt-1 size-4" onChange={event => setConfirmed(event.target.checked)} /><Label htmlFor={`${formId}-confirm`}>我已核对原付款、方案和已签金额，确认执行该笔加购退款。{pending ? "原结果未明时，重试仍只使用同一已签方案。" : "提交前系统会再次读取并校验。"}</Label></div>
      <div className="flex flex-wrap gap-2">
        {pending ? <Button type="button" variant="outline" disabled={busy} onClick={() => void run(true)}>核对原方案退款凭证</Button> : <Button type="button" variant="ghost" disabled={busy} onClick={() => { setSelected(null); setConfirmed(false) }}>取消本次执行</Button>}
        <Button type="submit" disabled={busy || !canRefund || !pending && (blocked || loading || !!readError)}>{busy ? "正在核对…" : `${pending ? "按原方案重试" : "执行方案"} #${current.proposalId} · ${amountToXaCoin(current.amountRaw)} ${coinUnit()}`}</Button>
      </div>
    </form>}
  </section>
}
