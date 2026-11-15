import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { formatXaCoin } from "@xa/money"
import { ApiError, createApiClient } from "./client"
import { coinUnit, getStoreSlug } from "./store"
import { getSession } from "./session"
import { useRealtime } from "./realtime"

type Term = { account_id: number; retained_units: number; original_income_units: number; burden_units: number; additional_units: number }
type Proposal = { id: number; version: number; status: "PENDING" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";
  refund_units: number; provider_terms: Term[]; explanation: string; my_decision: boolean | null }
type PendingDecision = {id:number; accepted:boolean} | "invalid" | null

function readPending(key: string): PendingDecision {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw)
    return Number.isSafeInteger(value?.id) && value.id > 0 && typeof value.accepted === "boolean" ? value : "invalid"
  } catch {return "invalid"}
}

function valid(value: unknown): value is Proposal {
  const data = value as Proposal | null
  return !!data && Number.isSafeInteger(data.id) && data.id > 0 && Number.isSafeInteger(data.version) && data.version > 0 &&
    ["PENDING", "ACCEPTED", "REJECTED", "SUPERSEDED"].includes(data.status) &&
    Number.isSafeInteger(data.refund_units) && data.refund_units >= 0 && data.refund_units % 10 === 0 && typeof data.explanation === "string" &&
    Array.isArray(data.provider_terms) && data.provider_terms.length > 0 &&
    data.provider_terms.every(item => Number.isSafeInteger(item.account_id) && item.account_id > 0 &&
      [item.retained_units, item.original_income_units, item.burden_units, item.additional_units].every(n => Number.isSafeInteger(n) && n >= 0) &&
      item.retained_units === Math.max(item.original_income_units - item.burden_units, 0) &&
      item.additional_units === Math.max(item.burden_units - item.original_income_units, 0)) &&
    new Set(data.provider_terms.map(item => item.account_id)).size === data.provider_terms.length &&
    (data.my_decision === null || typeof data.my_decision === "boolean")
}

/** A decision records only the displayed proposal; it never performs a refund. */
export function NegotiationProposalPanel({ role, orderId, names = {} }: {
  role: "boss" | "provider"; orderId: number; names?: Record<number, ReactNode>
}) {
  const api = useMemo(() => createApiClient(role), [role])
  const path = `/orders/orders/${orderId}/negotiation-proposal/`
  const key = `xa:negotiation-decision:${getStoreSlug()}:${role}:${getSession(role).user?.id}:${orderId}`
  const sequence = useRef(0)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [error, setError] = useState("")
  const [choice, setChoice] = useState<boolean | null>(null)
  const [unknown, setUnknown] = useState<PendingDecision>(() => readPending(key))
  const [busy, setBusy] = useState(false)
  async function load(pending: PendingDecision = unknown) {
    const current = ++sequence.current
    try {
      const data = await api.get<unknown>(path)
      if (current !== sequence.current) return
      if (data !== null && !valid(data)) throw new Error("方案内容不完整，请联系客服核对。")
      if (!data || !proposal || data.id !== proposal.id || data.status !== "PENDING" || data.my_decision !== null) setChoice(null)
      setProposal(data); setError("")
      if (pending && pending !== "invalid" && (!data || data.id !== pending.id || data.my_decision !== null)) {
        try {sessionStorage.removeItem(key)} catch { /* repeat remains safe */ }
        setUnknown(null)
      }
    } catch (cause) {
      if (current !== sequence.current) return
      if (cause instanceof ApiError && cause.status === 404) {
        setProposal(null); setError(pending ? "原方案暂时无法读取，请联系客户服务核对。" : ""); return
      }
      setError(cause instanceof Error ? cause.message : "方案暂时无法读取，请稍后重试。")
    }
  }
  useEffect(() => {
    const saved = readPending(key)
    setProposal(null); setChoice(null); setUnknown(saved); setError("")
    void load(saved)
    return () => { sequence.current += 1 }
  }, [api, path, key])
  const refresh = useRef<() => void>(() => {})
  const refreshWhenIdle = useRef(false)
  refresh.current = () => {
    if (busy) refreshWhenIdle.current = true
    else { refreshWhenIdle.current = false; void load(readPending(key)) }
  }
  useRealtime(role, event => {
    if (event.type === "reconnected" ||
        (event.type === "order_status_update" && Number(event.data?.id) === orderId)) refresh.current()
  })
  useEffect(() => {
    const resume = () => { if (!document.hidden) refresh.current() }
    window.addEventListener("focus", resume)
    document.addEventListener("visibilitychange", resume)
    return () => { window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume) }
  }, [key])
  useEffect(() => { if (!busy && refreshWhenIdle.current) refresh.current() }, [busy, key])
  async function submit(intent: {id:number; accepted:boolean}) {
    if (busy) return
    try {sessionStorage.setItem(key, JSON.stringify(intent))}
    catch {setError("无法保存确认凭证，请检查浏览器后重试。"); return}
    sequence.current += 1
    setBusy(true); setError("")
    try {
      const data = await api.post<unknown>(path, {proposal_id:intent.id, accepted:intent.accepted})
      if (!valid(data)) throw new Error("结果尚未确认，请重新核对。")
      try {sessionStorage.removeItem(key)} catch { /* repeat remains safe */ }
      setProposal(data); setChoice(null); setUnknown(null)
    } catch (cause) {
      if (!(cause instanceof ApiError) || !cause.status || cause.status >= 500) setUnknown(intent)
      else {try {sessionStorage.removeItem(key)} catch { /* no-op */ }; setUnknown(null); setChoice(null)}
      setError(cause instanceof Error ? cause.message : "提交结果未确认。")
    } finally { setBusy(false) }
  }
  if (!proposal && !error && !unknown) return null
  return <section className="space-y-3 rounded-lg border p-4 text-sm" aria-label="协商方案">
    <h3 className="font-semibold">协商方案 {proposal ? `· 第 ${proposal.version} 版` : ""}</h3>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {proposal && <>
      <p>拟退还：{formatXaCoin(proposal.refund_units)} {coinUnit()}</p>
      {proposal.provider_terms.map(term => <div key={term.account_id}>
        <p>{role === "provider" ? "本人" : names[term.account_id] || `陪玩 #${term.account_id}`}原待结算收益：{formatXaCoin(term.original_income_units)} {coinUnit()}；承担退款：{formatXaCoin(term.burden_units)} {coinUnit()}</p>
        <p>{role === "provider" ? "本人" : names[term.account_id] || `陪玩 #${term.account_id}`}拟保留收益：{formatXaCoin(term.retained_units)} {coinUnit()}（抵押金前）</p>
        {term.additional_units > 0 && <p>另需承担 {formatXaCoin(term.additional_units)} {coinUnit()}，将依次核对钱包和押金。</p>}
      </div>)}
      <p className="whitespace-pre-wrap break-words">协商说明：{proposal.explanation}</p>
      <p className="text-muted-foreground">{proposal.status === "ACCEPTED" ? "所需人员均已确认，等待客服处理。" :
        proposal.status === "REJECTED" ? "方案有异议，请联系客服。" : proposal.my_decision === true ? "已记录你的同意，等待其他人员确认。" :
        proposal.my_decision === false ? "已记录你的异议。" : "请核对方案后选择是否同意。"}确认只记录方案，不会自动退款或结束订单。</p>
      {proposal.status === "PENDING" && proposal.my_decision === null && !unknown && choice === null && <div className="flex flex-wrap gap-2">
        <button type="button" className="rounded-md border px-3 py-2" disabled={busy} onClick={() => setChoice(true)}>同意方案</button>
        <button type="button" className="rounded-md border px-3 py-2" disabled={busy} onClick={() => setChoice(false)}>提出异议</button>
      </div>}
      {proposal.status === "PENDING" && proposal.my_decision === null && choice !== null && !unknown && <div className="space-y-2 rounded-md bg-muted p-3" role="alert">
        <p>{choice ? "确认同意以上版本的方案？" : "确认对以上方案提出异议？"}</p>
        <div className="flex gap-2"><button type="button" className="rounded-md border px-3 py-2" disabled={busy}
          onClick={() => void submit({id:proposal.id, accepted:choice})}>{busy ? "提交中…" : "确认提交"}</button>
          <button type="button" className="rounded-md border px-3 py-2" disabled={busy} onClick={() => setChoice(null)}>返回</button></div>
      </div>}
    </>}
    {unknown === "invalid" ? <p role="alert">原确认凭证无法读取，请联系客户服务核对。</p> : unknown && <div className="space-y-2" role="status"><p>提交结果未确认，请重试同一决定或重新读取方案核对。</p>
      <button type="button" className="rounded-md border px-3 py-2" disabled={busy} onClick={() => void submit(unknown)}>重试原决定</button></div>}
    <button type="button" className="rounded-md border px-3 py-2" disabled={busy} onClick={() => void load()}>刷新方案</button>
  </section>
}
