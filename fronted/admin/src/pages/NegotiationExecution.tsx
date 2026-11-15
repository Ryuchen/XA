import { useEffect, useState } from "react"
import { coinUnit, formatMoney, getSession, getStoreSlug } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Feedback, MutationDialog, type Row } from "@/components/business"
import { request, requestId } from "@/api/request"
import { clearReview, reviewActive } from "@/lib/reviews"
import { toast } from "sonner"

type Intent = { proposal_id: number; client_request_id: string }
const path = (id: number) => `/aftersales/${id}/negotiation-execution/`
function storageKey(id: number) {
  return `xa:negotiation-refund:${getStoreSlug()}:${getSession("admin").user?.id}:${id}`
}
function readIntent(id: number): Intent | "invalid" | null {
  try {
    const saved = localStorage.getItem(storageKey(id))
    if (!saved) return null
    const value = JSON.parse(saved)
    return value && Number.isSafeInteger(value.proposal_id) && value.proposal_id > 0 &&
      typeof value.client_request_id === "string" && value.client_request_id.length > 0 &&
      value.client_request_id.length <= 64 ? value : "invalid"
  } catch { return "invalid" }
}

export function NegotiationExecutionEntry({ ticket, open }: { ticket: Row; open: (proposal: Row | null) => void }) {
  const [state, setState] = useState<"loading" | "ready" | "hidden" | "error">("loading")
  const [proposal, setProposal] = useState<Row | null>(null)
  const pending = readIntent(ticket.id)
  useEffect(() => {
    let active = true
    request<Row>({ url: path(ticket.id), silent: true }).then(result => {
      if (!active) return
      setProposal(result.data.proposal ? { ...result.data.proposal,
        execution_enabled: result.data.execution_enabled === true,
        zero_execution_enabled: result.data.zero_execution_enabled === true } : null)
      setState("ready")
    }).catch((error: any) => {
      if (active) {
        if (error.status === 404 && pending) { setProposal(null); setState("ready") }
        else setState(error.status === 404 ? "hidden" : "error")
      }
    })
    return () => { active = false }
  }, [ticket.id])
  if (state === "error") return <p role="alert" className="text-xs text-destructive">协商处理暂时无法核对，请稍后重试。</p>
  if (state !== "ready" || (!pending && (ticket.status !== "OPEN" || proposal?.status !== "ACCEPTED"))) return null
  return <Button size="sm" variant="outline" onClick={() => open(proposal)}>
    {pending ? "核对协商处理" : proposal?.refund_units === 0 ? "执行协商终止" : "执行协商退款"}
  </Button>
}

export function NegotiationExecutionDialog({ ticket, proposal, onClose, onSaved }: {
  ticket: Row; proposal: Row | null; onClose: () => void; onSaved: () => void
}) {
  const [pending, setPending] = useState<Intent | "invalid" | null>(() => readIntent(ticket.id))
  const [reviewed, setReviewed] = useState(false)
  const [funding, setFunding] = useState<Row | null>(null)
  const [fundingError, setFundingError] = useState("")
  const [loading, setLoading] = useState(false)
  const matches = proposal && (!pending || (pending !== "invalid" && proposal.id === pending.proposal_id))
  useEffect(() => {
    if (pending || !matches || proposal?.status !== "ACCEPTED" || ticket.status !== "OPEN" || !proposal.execution_enabled ||
        (proposal.refund_units === 0 && !proposal.zero_execution_enabled)) return
    let active = true
    setLoading(true)
    request<Row>({ url: `/aftersales/${ticket.id}/negotiation-funding/`, params: { refund_units: proposal.refund_units } })
      .then(result => { if (active) setFunding(result.data) })
      .catch((error: any) => { if (active) setFundingError(error.message || "资金核对失败，请稍后重试。") })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [ticket.id, ticket.status, proposal?.id, pending])
  const ready = pending && pending !== "invalid" ||
    (!pending && matches && proposal?.status === "ACCEPTED" && ticket.status === "OPEN" && proposal.execution_enabled === true &&
      (proposal.refund_units > 0 || proposal.refund_units === 0 && proposal.zero_execution_enabled === true) &&
      funding?.book_amounts_sufficient === true && funding?.refund_units === proposal.refund_units)
  const zero = proposal?.refund_units === 0
  return <MutationDialog title={pending ? "核对协商处理" : zero ? "确认协商终止" : "确认执行协商退款"}
    description={pending && !proposal ? "请先核对原操作结果，系统只会使用原凭证重试。" : zero ? "核对双方已确认的方案。执行后终止受影响陪玩的服务，订单付款与约定收益保持不变。" :
      "核对双方已确认的方案。执行后退还老板余额，并按约定处理受影响陪玩的收益、钱包和押金。"}
    onClose={onClose} submitText={pending ? "查询并重试原请求" : zero ? "确认终止" : "确认退款"} submitVariant="destructive"
    disabled={!reviewed || !ready || loading}
    onSubmit={async () => {
      let intent = pending
      if (!intent) {
        if (!matches || !proposal || !funding?.book_amounts_sufficient) throw new Error("请先核对方案和资金。")
        intent = { proposal_id: proposal.id, client_request_id: requestId("negotiation-refund") }
        try { localStorage.setItem(storageKey(ticket.id), JSON.stringify(intent)) }
        catch { throw new Error("无法保存协商操作凭证，请检查浏览器存储。") }
        setPending(intent)
      }
      if (intent === "invalid") throw new Error("原操作凭证无法读取，请联系管理员核对。")
      if (pending) {
        const reviewKey = `/admin${path(ticket.id)}`
        if (reviewActive(reviewKey)) throw new Error("原请求仍在提交，请稍后核对。")
        const result = await request<Row>({ url: path(ticket.id), params: intent })
        if (result.data.status === "COMPLETED") {
          clearReview(reviewKey)
          localStorage.removeItem(storageKey(ticket.id)); toast.success(result.data.refund_units === 0 ? "协商终止已完成" : "协商退款已完成"); onSaved(); return
        }
        if (result.data.status !== "NOT_FOUND") throw new Error("协商处理结果待核对，请联系管理员。")
        clearReview(reviewKey)
      }
      const result = await request<Row>({ url: path(ticket.id), method: "POST", data: intent })
      if (result.data.status !== "COMPLETED") throw new Error("协商处理结果待核对，请联系管理员。")
      localStorage.removeItem(storageKey(ticket.id)); toast.success(result.data.refund_units === 0 ? "协商终止已完成" : "协商退款已完成"); onSaved()
    }}>
    {matches && proposal && <div className="space-y-1 rounded-md border p-3 text-sm">
      <p>已确认方案第 {proposal.version} 版 · 退款 {formatMoney(proposal.refund_units)} {coinUnit()}</p>
      <p>{proposal.explanation}</p>
      {(proposal.provider_terms || []).map((term: Row) => <p key={term.account_id}>
        {ticket.affected_provider_names?.[ticket.affected_provider_account_ids?.indexOf(term.account_id)] || `陪玩 #${term.account_id}`}：
        承担 {formatMoney(term.burden_units)}、保留 {formatMoney(term.retained_units)} {coinUnit()}
      </p>)}
    </div>}
    {loading && <p className="text-sm">正在核对资金来源…</p>}
    {!pending && zero && !proposal?.zero_execution_enabled && <Feedback message="零退款的协商终止暂未开放，请联系管理员处理。" />}
    {!pending && proposal && !proposal.execution_enabled && <Feedback message="追加协商处理暂未开放，请联系管理员处理。" />}
    <Feedback message={fundingError} />
    {funding && <div className="space-y-1 text-sm" role="status">
      {(funding.providers || []).map((row: Row) => <p key={row.account_id}>
        陪玩 #{row.account_id}：待结算 {formatMoney(row.pending_income_units)}、钱包 {formatMoney(row.wallet_units)}、
        押金 {formatMoney(row.deposit_units)}、缺口 {formatMoney(row.shortfall_units)} {coinUnit()}
      </p>)}
      <p>{funding.book_amounts_sufficient ? "账面金额可覆盖；实际执行时重新核验资金与凭证。" : "账面金额不足，补足后才能退款。"}</p>
    </div>}
    {pending === "invalid" ? <Feedback message="原协商操作凭证无法读取，请联系管理员核对。" /> : pending &&
      <p role="status" className="text-sm">上次结果未确认，将先查询并只重试原操作，不会重复处理。</p>}
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1 size-4"
      checked={reviewed} onChange={event => setReviewed(event.target.checked)} />
      已核对当事人、退款金额及保留收益，确认按该方案处理。
    </label>
  </MutationDialog>
}
