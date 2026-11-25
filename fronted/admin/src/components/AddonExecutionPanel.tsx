import { useId, useRef, useState } from "react"
import { coinUnit, getStoreSlug } from "@xa/shared"
import { errorMessage, request } from "@/api/request"
import { Feedback, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { useAdminAuth } from "@/lib/auth"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import type { AddonProposalTarget } from "@/lib/addon-first-proposal"
import { captureAddonExecution, checkExactAddonExecution, executeAddonExecution, lookupExactAddonReceipt, parseAddonExecution, resumeAddonExecution, type AddonExecutionIntent } from "@/lib/addon-execution"
import { positiveId } from "@/lib/addon-first-proposal"

type ExactReceiptState = "idle" | "loading" | "empty" | "not-executed" | "unknown" | "verified"

const exactReceiptText: Record<ExactReceiptState, string> = {
  idle: "",
  loading: "正在只读查询原退款凭证…",
  empty: "空：未读到退款凭证，无法判断是否已退款，也不能据此执行。",
  "not-executed": "未执行：服务端确认尚未记录原方案退款。此查询结果不表示当前可以执行。",
  unknown: "未知：已有退款记录，但返款流水凭证待核对；不能按已退款展示，也不能据此执行。",
  verified: "已验证：退款、金额与老板返款流水已按原订单、付款、方案及工单核对。",
}

function ExactReceiptReadout({ state }: { state: ExactReceiptState }) {
  if (state === "idle") return null
  return <p role="status" className={`rounded-md border p-3 text-sm ${state === "unknown" || state === "empty" ? "border-border bg-muted/50" : "bg-muted/30"}`}>{exactReceiptText[state]}</p>
}

function AddonExactReceiptLookup({ target, history }: { target: AddonProposalTarget; history: unknown }) {
  const auth = useAdminAuth()
  const id = useId()
  const [number, setNumber] = useState("")
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [error, setError] = useState("")
  const [readState, setReadState] = useState<ExactReceiptState>("idle")
  const [receipt, setReceipt] = useState<Row | null>(null)
  if (!auth.hasPerm("order:view") && !auth.hasPerm("order:refund")) return null
  async function lookup() {
    if (inFlight.current) return
    const proposalId = Number(number)
    if (!/^[1-9]\d*$/.test(number) || !positiveId(proposalId)) { setError("请输入已知的正整数原方案编号。"); setReadState("idle"); return }
    inFlight.current = true; setBusy(true); setError(""); setReadState("loading"); setReceipt(null)
    try {
      const data = (await request({ url: `/orders/${target.orderId}/addons/payments/${target.paymentId}/refund-proposals/${proposalId}/execution-receipt/` })).data
      if (data == null) { setReadState("empty"); return }
      const outcome = lookupExactAddonReceipt(data, proposalId, target, history)
      setReadState(outcome.status)
      if (outcome.status === "verified") setReceipt(outcome.receipt)
    } catch (cause) { setReadState("idle"); setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  return <section className="space-y-3 rounded-lg border p-4 text-sm">
    <h3 id={`${id}-title`} className="font-medium">按已知原方案号只读核对退款</h3>
    <p id={`${id}-hint`} className="text-xs text-muted-foreground">固定本工单的订单 #{target.orderId}、付款 #{target.paymentId}。列表无法读取时仍可核对已知原方案号；这里只查询退款凭证，不会执行退款。</p>
    <form aria-labelledby={`${id}-title`} className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); void lookup() }}>
      <div className="space-y-2"><Label htmlFor={`${id}-number`}>已知原方案编号</Label><Input id={`${id}-number`} name="proposal_id" inputMode="numeric" required pattern="[1-9][0-9]*" maxLength={16} value={number} disabled={busy} aria-describedby={`${id}-hint`} onChange={event => { setNumber(event.target.value); setReceipt(null); setReadState("idle"); setError("") }} /></div>
      <Button type="submit" variant="outline" disabled={busy}>{busy ? "正在查询…" : "只读查询原退款"}</Button>
    </form>
    <Feedback message={error} />
    <ExactReceiptReadout state={readState} />
    {receipt && <div role="status" className="space-y-1"><p>方案 #{receipt.id} · 退款 #{receipt.execution_receipt.refund_id} · 老板返款流水 #{receipt.execution_receipt.buyer_transaction_id}</p><p>服务端凭证实退金额 {amountToXaCoin(receipt.execution_receipt.amount_raw)} {coinUnit()} · {formatDateTime(receipt.execution_receipt.executed_at)}</p></div>}
  </section>
}

export default function AddonExecutionPanel({ target, history, proposals, loading, readError, blocked, onLoaded }: {
  target: AddonProposalTarget; history?: unknown; proposals: Row[] | null; loading: boolean; readError?: string; blocked: boolean; onLoaded: (rows: Row[]) => void
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
  const [readState, setReadState] = useState<ExactReceiptState>("idle")
  const formId = useId()
  const current = pending || selected
  const canRefund = auth.hasPerm("order:refund")
  const candidates = (proposals || []).filter(row => row.payment_id === target.paymentId && row.case_id === target.caseId && row.case_request_id === target.caseRequestId && row.status === "ACCEPTED" && row.refund_executed !== true)
  function clear() { sessionStorage.removeItem(key); setPending(null) }
  function select(proposal: Row) {
    if (busy || blocked || loading || readError || pending || restored.error || !canRefund) return
    try { setSelected(captureAddonExecution(proposal, target)); setConfirmed(false); setError(""); setReceipt(null); setReadState("idle") }
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
    setReadState(pending || checkOnly ? "loading" : "idle")
    const endpoint = `/orders/${current.target.orderId}/addons/payments/${current.target.paymentId}/refund-proposals/`
    const read = async () => {
      const rows = (await request({ url: endpoint })).data
      if (Array.isArray(rows)) onLoaded(rows)
      return rows
    }
    const readExact = async () => (await request({ url: `${endpoint}${current.proposalId}/execution-receipt/` })).data
    try {
      const io = { read, readExact, clear,
        save: (value: AddonExecutionIntent) => { try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { throw new Error("无法保存原方案执行凭证，请检查浏览器存储后重试。") }; setPending(value) },
        send: async (value: AddonExecutionIntent) => (await request({ url: `${endpoint}${value.proposalId}/execute/`, method: "POST", data: {} })).data }
      let value: Row
      if (checkOnly || pending) {
        const outcome = checkOnly ? await checkExactAddonExecution(current, io) : await resumeAddonExecution(current, io)
        if (outcome.status !== "verified") { setReadState(outcome.status); setConfirmed(false); return }
        value = outcome.receipt
      } else value = await executeAddonExecution(current, io)
      setReadState("verified")
      setReceipt(value)
      setSelected(null)
      setConfirmed(false)
      if (!checkOnly && !pending) {
        try { await read() } catch { /* The matching refund receipt remains known even if list refresh fails. */ }
      }
    } catch (cause) {
      setReadState("idle")
      setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause))
      setConfirmed(false)
    } finally { inFlight.current = false; setBusy(false) }
  }

  const showExecution = candidates.length > 0 || !!current || !!receipt || !!restored.error
  return <>
    <AddonExactReceiptLookup target={target} history={history} />
    {showExecution && <section className="space-y-3 rounded-lg border p-4 text-sm">
    <h3 className="font-medium">执行指定已签加购方案</h3>
    <Feedback message={error} />
    <ExactReceiptReadout state={readState} />
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
  </section>}
  </>
}
