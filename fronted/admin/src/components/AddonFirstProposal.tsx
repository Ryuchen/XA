import { useId, useRef, useState } from "react"
import { coinUnit, getStoreSlug } from "@xa/shared"
import { errorMessage, request, requestId } from "@/api/request"
import { Feedback, Status, useLoad, type Row } from "@/components/business"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { amountToXaCoin, formatDateTime } from "@/lib/format"
import { checkedFirstProposal, firstProposalBlocker, integerCoinRefund, parseFirstIntent, positiveId, proposalsForCase, submitFirstProposal, validScope, type AddonFirstIntent, type AddonProposalTarget } from "@/lib/addon-first-proposal"
import { useAdminAuth } from "@/lib/auth"
import AddonExecutionPanel from "@/components/AddonExecutionPanel"
import { readAddonReceiptTarget, readExecutedAddonProposal } from "@/lib/addon-execution"

const money = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0 ? `${amountToXaCoin(Number(value))} ${coinUnit()}` : "待核对"

function AddonExecutionReceipt({ proposal, target, history }: { proposal: Row; target: AddonProposalTarget; history: unknown }) {
  if (proposal.refund_executed !== true) return null
  try {
    const original = readAddonReceiptTarget(proposal, target, history)
    const proof = readExecutedAddonProposal(proposal, original).execution_receipt
    return <dl className="grid gap-2 rounded-md bg-muted/30 p-3 sm:grid-cols-2">
      <div><dt className="text-xs text-muted-foreground">已核对退款编号</dt><dd>#{proof.refund_id}</dd></div>
      <div><dt className="text-xs text-muted-foreground">老板返款流水</dt><dd>#{proof.buyer_transaction_id}</dd></div>
      <div><dt className="text-xs text-muted-foreground">实退金额</dt><dd>{money(proof.amount_raw)}</dd></div>
      <div><dt className="text-xs text-muted-foreground">退款时间</dt><dd>{formatDateTime(proof.executed_at)}</dd></div>
    </dl>
  } catch { return <Feedback message="该方案退款及老板返款流水凭证待核对，请保留原方案并重新读取。" /> }
}

export default function AddonFirstProposal({ orderId, ticket }: { orderId: number; ticket: Row }) {
  const auth = useAdminAuth()
  const [open, setOpen] = useState(false)
  const target: AddonProposalTarget = { orderId, paymentId: ticket.addon_payment, caseId: ticket.id, caseRequestId: ticket.client_request_id || "", accounts: ticket.affected_provider_account_ids || [], segments: ticket.affected_segment_ids || [] }
  const storageKey = `xa:store:${getStoreSlug()}:admin:addon-first:${auth.profile.id}:${orderId}:${target.paymentId}:${target.caseId}`
  const [restored] = useState(() => {
    try { return { intent: parseFirstIntent(sessionStorage.getItem(storageKey), target), error: "" } }
    catch (cause) { return { intent: null, error: errorMessage(cause) } }
  })
  const [pending, setPending] = useState<AddonFirstIntent | null>(restored.intent)
  const [amount, setAmount] = useState("")
  const [explanation, setExplanation] = useState("")
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [error, setError] = useState(restored.error)
  const [receipt, setReceipt] = useState<Row | null>(null)
  const formId = useId()
  const endpoint = `/orders/${orderId}/addons/payments/${target.paymentId}/refund-proposals/`
  const result = useLoad<Row[] | null>(async () => {
    if (!open || !positiveId(target.paymentId)) return null
    const rows = (await request({ url: endpoint })).data
    proposalsForCase(rows, target)
    return rows
  },
  [open, endpoint, target.caseId, ticket.client_request_id])
  const rows = result.data ? proposalsForCase(result.data, target) : null
  const blocker = result.data ? firstProposalBlocker(result.data, target) : undefined
  const canCreate = ticket.status === "OPEN" && auth.hasPerm("order:refund") && validScope(target) &&
    !!result.data && !result.loading && !result.error && !blocker && !restored.error
  function clearPending() { sessionStorage.removeItem(storageKey); setPending(null) }
  function failure(cause: unknown) { setError((cause as { body?: { msg?: string } } | null)?.body?.msg || errorMessage(cause)) }
  async function submit() {
    if (!auth.hasPerm("order:refund")) { setError("当前没有登记退款方案权限，请先读取并核对原方案。"); return }
    if (inFlight.current || restored.error || (!pending && !canCreate)) return
    const raw = integerCoinRefund(amount)
    if (!pending && (raw === null || explanation.trim().length < 2)) { setError("请填写正整数店币金额和至少 2 字的协商说明。"); return }
    const intent: AddonFirstIntent = pending || { target, payload: { create_only: true, case_id: target.caseId, case_request_id: target.caseRequestId,
      request_id: requestId("addon-first"), amount_raw: raw!, explanation: explanation.trim(), affected_provider_account_ids: [...target.accounts] } }
    inFlight.current = true
    setBusy(true)
    setError("")
    try {
      const proposal = await submitFirstProposal(intent, {
        recovering: !!pending,
        save: value => { try { sessionStorage.setItem(storageKey, JSON.stringify(value)) } catch { throw new Error("无法保存原方案请求，请检查浏览器存储后重试。") }; setPending(value) },
        clear: clearPending,
        send: async value => (await request({ url: endpoint, method: "POST", data: value.payload })).data,
      })
      setReceipt(proposal)
      await result.reload()
    } catch (cause) { failure(cause); await result.reload() }
    finally { inFlight.current = false; setBusy(false) }
  }
  async function checkOriginal() {
    if (!pending || inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError("")
    try {
      const all = (await request({ url: endpoint })).data
      const originalRows = proposalsForCase(all, pending.target)
      result.setData(all)
      const found = originalRows.find(proposal => proposal.request_id === pending.payload.request_id)
      if (!found) { setError("尚未读到本人原号的匹配方案，原请求继续保留。请核对已有方案，不要另生成新号。"); return }
      const proposal = checkedFirstProposal(found, pending)
      clearPending()
      setReceipt(proposal)
    } catch (cause) { failure(cause) }
    finally { inFlight.current = false; setBusy(false) }
  }
  return <>
    <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>加购退款方案 · 付款 #{target.paymentId}</Button>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-[800px]">
        <DialogHeader><DialogTitle>售后 #{ticket.id} · 指定加购退款方案</DialogTitle><DialogDescription>订单 #{orderId} · 原付款 #{target.paymentId}。创建方案只登记协商内容，双方确认与实际退款分别核对。</DialogDescription></DialogHeader>
        <Feedback message={result.error} retry={result.reload} />
        <Feedback message={error} />
        <Button type="button" size="sm" variant="outline" disabled={result.loading || busy} onClick={() => void result.reload()}>{result.loading ? "正在读取…" : "重新读取方案"}</Button>
        {!positiveId(target.paymentId) && <Feedback message="指定加购付款编号无法核对，请返回订单刷新。" />}
        {result.loading && !result.data && <p className="text-sm text-muted-foreground">正在读取本工单的加购方案…</p>}
        {rows?.length === 0 && <p className="text-sm text-muted-foreground">当前未查到本工单与该付款关联的方案。</p>}
        {receipt && <p role="status" className="rounded-md border p-3 text-sm">已核对本人原请求，方案 #{receipt.id} 当前为 <Status value={receipt.status} />。创建方案不代表完成退款。</p>}
        {pending ? <div className="space-y-3 rounded-md border p-4 text-sm">
          <p>原方案请求尚未取得匹配回执，金额、说明和请求号已保留。</p>
          <p>售后 #{pending.target.caseId} · 付款 #{pending.target.paymentId} · 拟退 {money(pending.payload.amount_raw)}</p>
          <p className="whitespace-pre-wrap break-words">原说明：{pending.payload.explanation}</p>
          <p className="break-all text-xs text-muted-foreground">原请求号：{pending.payload.request_id}</p>
          <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={busy} onClick={() => void checkOriginal()}>按原号读取方案</Button><Button type="button" disabled={busy || !auth.hasPerm("order:refund")} onClick={() => void submit()}>按原号重试首建</Button></div>
        </div> : canCreate && !receipt ? <form aria-labelledby={`${formId}-title`} className="space-y-4 rounded-lg border p-4" onSubmit={event => { event.preventDefault(); void submit() }}>
          <h3 id={`${formId}-title`} className="font-medium">创建本轮首份加购退款方案</h3>
          <p className="text-sm">绑定陪玩账号 {target.accounts.map(id => `#${id}`).join("、")} · 履约段 {target.segments.map(id => `#${id}`).join("、")}</p>
          <div className="space-y-2"><Label htmlFor={`${formId}-amount`}>拟退款金额（整数{coinUnit()}，必填）</Label><Input id={`${formId}-amount`} name="amount" value={amount} inputMode="numeric" required pattern="[1-9][0-9]*" maxLength={9} disabled={busy} onChange={event => { setAmount(event.target.value); setError("") }} /></div>
          <div className="space-y-2"><Label htmlFor={`${formId}-explanation`}>协商说明（必填）</Label><p id={`${formId}-hint`} className="text-xs text-muted-foreground">2–1000 字。提交后等待老板与受影响陪玩确认。</p><Textarea id={`${formId}-explanation`} name="explanation" value={explanation} required minLength={2} maxLength={1000} disabled={busy} aria-describedby={`${formId}-hint`} onChange={event => { setExplanation(event.target.value); setError("") }} /></div>
          <Button type="submit" disabled={busy}>创建首份待确认方案</Button>
        </form> : null}
        {ticket.status === "OPEN" && auth.hasPerm("order:refund") && !validScope(target) && <Feedback message="原工单轮次、陪玩或履约段缺失，暂不能创建方案，请返回订单核对。" />}
        {blocker && !pending && <p className="text-sm text-muted-foreground">该付款已有方案 #{blocker.id}（售后 #{blocker.case_id}），请核对已有记录。</p>}
        {rows?.map(proposal => <article key={proposal.id} className="space-y-3 rounded-lg border p-4 text-sm">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">加购方案 #{proposal.id}</h3><Status value={proposal.status} /></div>
          <p>原付款 #{proposal.payment_id} · 售后 #{proposal.case_id} · {formatDateTime(proposal.created_at)}</p>
          <p>拟退老板：{money(proposal.amount_raw)}。实际退款：{proposal.refund_executed === true ? "退款记录存在，请核对下方凭证" : proposal.refund_executed === false ? "尚未执行" : "待核对"}。</p>
          <AddonExecutionReceipt proposal={proposal} target={target} history={ticket.history} />
          <p className="whitespace-pre-wrap break-words">协商说明：{proposal.explanation || "待核对"}</p>
          {Array.isArray(proposal.participants) && <ul className="space-y-1">{proposal.participants.map((person: Row) => <li key={person.account_id}>陪玩账号 #{person.account_id} · 责任段 {Array.isArray(person.share_plan) ? person.share_plan.map((share: Row) => `#${share.segment_id}`).join("、") : "待核对"} · 拟承担 {money(person.burden_raw)}</li>)}</ul>}
          {Array.isArray(proposal.decisions) && <ul className="space-y-1" aria-label={`方案 ${proposal.id} 各方签字`}>{proposal.decisions.map((decision: Row) => <li key={`${decision.role}:${decision.account_id}`}>{decision.role === "BOSS" ? "老板" : "陪玩"}账号 #{decision.account_id}：{decision.accepted === true ? "已同意" : decision.accepted === false ? "有异议" : decision.accepted === null ? "等待确认" : "待核对"}</li>)}</ul>}
        </article>)}
        <AddonExecutionPanel target={target} history={ticket.history} proposals={result.data} loading={result.loading} readError={result.error} blocked={busy || !!pending || !!restored.error} onLoaded={result.setData} />
      </DialogContent>
    </Dialog>
  </>
}
