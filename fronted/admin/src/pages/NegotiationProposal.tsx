import { useEffect, useRef, useState } from "react"
import { getSession, getStoreSlug, toCoin, formatMoney } from "@xa/shared"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Feedback, MutationDialog, useLoad, type Row } from "@/components/business"
import { request, requestId } from "@/api/request"
import { xaCoinToAmount } from "@/lib/format"
import { coinUnit } from "@xa/shared"
import { useAdminAuth } from "@/lib/auth"
import { toast } from "sonner"

const endpoint = (id: number) => `/aftersales/${id}/negotiation-proposal/`

export function NegotiationProposalEntry({ ticket, open }: {ticket: Row; open: () => void}) {
  const [state, setState] = useState<"loading" | "available" | "hidden" | "error">("loading")
  useEffect(() => {
    let active = true
    request({url:endpoint(ticket.id), silent:true}).then(() => {if (active) setState("available")})
      .catch((error: any) => {if (active) setState(error.status === 404 ? "hidden" : "error")})
    return () => {active = false}
  }, [ticket.id])
  if (state === "available") return <Button size="sm" variant="outline" onClick={open}>协商方案</Button>
  if (state === "error") return <p role="alert" className="text-xs text-destructive">协商方案暂时无法读取，请刷新订单。</p>
  return null
}

type Pending = {request_id:string; expected_version:number; refund_units:number;
  provider_terms:{account_id:number; retained_units:number}[]; explanation:string}
function readPending(key: string): Pending | "invalid" | null {
  try {
    const value = sessionStorage.getItem(key)
    if (!value) return null
    const data = JSON.parse(value)
    return data && typeof data.request_id === "string" && Number.isSafeInteger(data.expected_version) &&
      Number.isSafeInteger(data.refund_units) && typeof data.explanation === "string" && Array.isArray(data.provider_terms) &&
      data.provider_terms.every((term:Row) => Number.isSafeInteger(term.account_id) && Number.isSafeInteger(term.retained_units))
      ? data : "invalid"
  } catch { return "invalid" }
}

export function NegotiationProposalEditor({ticket, onClose, onSaved}: {ticket:Row; onClose:() => void; onSaved:() => void}) {
  const auth = useAdminAuth()
  const key = `xa:negotiation:${getStoreSlug()}:${getSession("admin").user?.id}:${ticket.id}`
  const [pending, setPending] = useState<Pending | "invalid" | null>(() => readPending(key))
  const [refund, setRefund] = useState("")
  const [explanation, setExplanation] = useState("")
  const [initialized, setInitialized] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState("")
  const [funding, setFunding] = useState<Row | null>(null)
  const [fundingLoading, setFundingLoading] = useState(false)
  const [fundingError, setFundingError] = useState("")
  const fundingSequence = useRef(0)
  const loaded = useLoad<Row>(async () => (await request<Row>({url:endpoint(ticket.id)})).data, [ticket.id])
  const ids: number[] = Array.isArray(ticket.affected_provider_account_ids) ? ticket.affected_provider_account_ids.map(Number) : []
  const names: string[] = Array.isArray(ticket.affected_provider_names) ? ticket.affected_provider_names : []
  const validRefund = /^(0|[1-9]\d*)$/.test(refund) && Number.isSafeInteger(xaCoinToAmount(refund)) && xaCoinToAmount(refund) <= 2147483647
  const refundUnits = validRefund ? xaCoinToAmount(refund) : null
  const estimate = useLoad<Row | null>(async () => refundUnits === null ? null : (await request<Row>({
    url:`/aftersales/${ticket.id}/negotiation-preview/`, params:{refund_units:refundUnits}})).data, [ticket.id, refundUnits])
  useEffect(() => {
    if (!loaded.data || initialized) return
    const current = loaded.data.proposal
    if (current) {
      setRefund(String(toCoin(current.refund_units)))
      setExplanation(current.explanation || "")
    }
    setInitialized(true)
  }, [loaded.data, initialized])
  const previous = loaded.data?.proposal
  const idsValid = ids.length > 0 && ids.every(id => Number.isSafeInteger(id) && id > 0) && new Set(ids).size === ids.length
  const locked = !!pending || loaded.loading || !!loaded.error || !loaded.data
  const terms: Row[] = estimate.data?.refund_units === refundUnits && Array.isArray(estimate.data?.provider_terms)
    ? estimate.data.provider_terms : []
  const readOnlyFollowup = loaded.data?.followup_preview_only === true || estimate.data?.read_only === true
  const valuesValid = validRefund && idsValid && !estimate.loading && !estimate.error && terms.length === ids.length &&
    ids.every(id => terms.some(term => term.account_id === id && Number.isSafeInteger(term.retained_units))) && explanation.trim().length >= 2
  async function checkFunding() {
    if (refundUnits === null || fundingLoading) return
    const sequence = ++fundingSequence.current
    setFundingLoading(true); setFundingError(""); setFunding(null)
    try {
      const response = await request<Row>({url:`/aftersales/${ticket.id}/negotiation-funding/`, params:{refund_units:refundUnits}})
      if (fundingSequence.current === sequence) setFunding(response.data)
    } catch (error:any) {if (fundingSequence.current === sequence) setFundingError(error.message || "资金核对暂时不可用，请稍后重试。")}
    finally {if (fundingSequence.current === sequence) setFundingLoading(false)}
  }
  async function send(payload: Pending) {
    await request({url:endpoint(ticket.id), method:"POST", data:payload})
    try { sessionStorage.removeItem(key) } catch { /* retry remains idempotent */ }
    setPending(null); toast.success("方案已登记，等待当事人确认"); onSaved()
  }
  async function retry() {
    if (!pending || pending === "invalid" || retrying) return
    setRetrying(true); setRetryError("")
    try { await send(pending); onClose() }
    catch (error: any) {
      setRetryError(error.message || "结果仍未确认，请稍后重试。")
      if (error.status && error.status < 500) {
        try { sessionStorage.removeItem(key) } catch { /* no-op */ }
        setPending(null); void loaded.reload(); void estimate.reload()
      }
    } finally { setRetrying(false) }
  }
  return <MutationDialog title="协商方案" description={readOnlyFollowup
    ? "本次仅能核对金额，追加方案、当事人确认与退款尚未开放。"
    : loaded.data?.execution_available
    ? "填写拟退款，系统按陪玩承担规则计算保留收益。双方确认后，由有退款权限的人员核对并执行。"
    : "填写拟退款，系统按陪玩承担规则计算保留收益。双方同意只记录方案；实际退款暂未开放。"}
    onClose={onClose} submitText={readOnlyFollowup ? "暂不可提交" : undefined}
    disabled={readOnlyFollowup || !!pending || loaded.loading || !!loaded.error || !loaded.data || !valuesValid}
    onSubmit={async () => {
      const currentRefundUnits = xaCoinToAmount(refund)
      const providerTerms = terms.map(term => ({account_id:term.account_id, retained_units:term.retained_units}))
      if (![currentRefundUnits, ...providerTerms.map(item => item.retained_units)].every(value => Number.isSafeInteger(value) && value >= 0 && value <= 2147483647))
        throw new Error("金额超出可用范围，请重新填写。")
      const payload:Pending = {request_id:requestId("negotiation"), expected_version:loaded.data!.version,
        refund_units:currentRefundUnits, provider_terms:providerTerms, explanation:explanation.trim()}
      try {sessionStorage.setItem(key, JSON.stringify(payload))} catch {throw new Error("无法保存操作凭证，请检查浏览器后重试。")}
      try {await send(payload)} catch (error:any) {
        if (!error.status || error.status >= 500) setPending(payload)
        else {try {sessionStorage.removeItem(key)} catch { /* no-op */ }; void loaded.reload(); void estimate.reload()}
        throw error
      }
    }}>
    <Feedback message={loaded.error} retry={loaded.reload} />
    {loaded.loading && <p>正在读取方案…</p>}
    {previous && <p className="text-sm">当前第 {previous.version} 版：{({PENDING:"等待确认", ACCEPTED:"已确认", REJECTED:"有异议", SUPERSEDED:"已替换"} as Record<string,string>)[previous.status] || "待核对"}。保存新方案会要求重新确认。</p>}
    {previous?.decisions?.length > 0 && <ul className="space-y-1 text-sm" aria-label="各方确认状态">
      {previous.decisions.map((decision:Row) => <li key={`${decision.role}-${decision.account_id}`}>
        {decision.role === "BOSS" ? "老板" : names[ids.indexOf(Number(decision.account_id))] || `陪玩 #${decision.account_id}`}：
        {decision.accepted === true ? "已同意" : decision.accepted === false ? "有异议" : "等待确认"}
      </li>)}
    </ul>}
    {!idsValid && <Feedback message="售后范围不完整，请返回订单核对。" />}
    <label className="block space-y-1 text-sm">拟退款（整数{coinUnit()}）<Input value={refund} inputMode="numeric" disabled={locked} onChange={event => {
      fundingSequence.current++; setFunding(null); setFundingError(""); setFundingLoading(false); setRefund(event.target.value)
    }} /></label>
    {refundUnits !== null && estimate.loading && <p className="text-sm">正在计算陪玩承担和保留收益…</p>}
    {refundUnits !== null && <Feedback message={estimate.error} retry={estimate.reload} />}
    {readOnlyFollowup && Number.isSafeInteger(estimate.data?.remaining_refundable_units) &&
      <p role="status" className="text-sm">剩余可退 {formatMoney(estimate.data!.remaining_refundable_units)} {coinUnit()}；本次测算不冻结资金。</p>}
    {terms.map(term => <div key={term.account_id} className="rounded-md border p-3 text-sm">
      <p className="font-medium">{names[ids.indexOf(Number(term.account_id))] || `陪玩 #${term.account_id}`}</p>
      <p>原待结算收益 {formatMoney(term.original_income_units)} {coinUnit()} · 承担 {formatMoney(term.burden_units)} {coinUnit()}</p>
      <p>拟保留收益 {formatMoney(term.retained_units)} {coinUnit()}（抵押金前）</p>
      {term.additional_units > 0 && <p>另需从钱包及押金核对 {formatMoney(term.additional_units)} {coinUnit()}；具体资金是否足够仍待核验。</p>}
    </div>)}
    {terms.length > 0 && Number(estimate.data?.rounding_to_store_units) > 0 &&
      <p className="text-sm">陪玩分摊取整差额：{formatMoney(estimate.data!.rounding_to_store_units)} {coinUnit()}，归门店。</p>}
    {auth.hasPerm("order:refund") && refundUnits !== null && terms.length > 0 && <div className="space-y-2 rounded-md border p-3 text-sm">
      <Button type="button" size="sm" variant="outline" disabled={fundingLoading} onClick={() => void checkFunding()}>
        {fundingLoading ? "正在核对…" : "核对资金来源"}
      </Button>
      <Feedback message={fundingError} />
      {funding?.refund_units === refundUnits && <div className="space-y-1" role="status">
        {funding.providers?.map((row:Row) => <p key={row.account_id}>
          {names[ids.indexOf(Number(row.account_id))] || `陪玩 #${row.account_id}`}：待结算收益 {formatMoney(row.pending_income_units)}、钱包 {formatMoney(row.wallet_units)}、押金 {formatMoney(row.deposit_units)}、缺口 {formatMoney(row.shortfall_units)} {coinUnit()}
        </p>)}
        <p>{funding.book_amounts_sufficient ? "账面金额可覆盖本次承担。" : "账面金额不足，需补足后再处理。"}此结果仅供核对，未冻结资金；执行前仍须重新核验钱包、押金及流水。</p>
      </div>}
    </div>}
    <label className="block space-y-1 text-sm">协商说明<Textarea value={explanation} maxLength={1000} disabled={locked || readOnlyFollowup} onChange={event => setExplanation(event.target.value)} /></label>
    {pending === "invalid" ? <Feedback message="原操作凭证无法读取，请关闭并核对现有方案后联系管理员。" /> : pending && <div className="space-y-2" role="status"><p>上次提交结果未确认，请重试原方案后再修改。</p><Button type="button" variant="outline" disabled={retrying} onClick={() => void retry()}>{retrying ? "正在核对…" : "重试原方案"}</Button><Feedback message={retryError} /></div>}
  </MutationDialog>
}
