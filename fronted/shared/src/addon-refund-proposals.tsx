import { useMemo, useRef, useState } from "react"
import { formatXaCoin } from "@xa/money"
import { createApiClient } from "./client"
import { getStoreSlug, coinUnit } from "./store"
import { useSession } from "./session"
import { useResource } from "./hooks"
import { useRealtime } from "./realtime"
import {
  addonRefundProposalPath,
  ownAddonRefundDecision,
  readAddonRefundDecision,
  recordAddonRefundDecision,
  type AddonRefundDecision,
  type AddonRefundProposal,
} from "./addon-refund-decision"

type ProposalTarget = { payment_id: number; proposal_id: number; status: string; my_accepted: boolean | null }
const storage = {
  getItem: (key: string) => sessionStorage.getItem(key),
  setItem: (key: string, value: string) => sessionStorage.setItem(key, value),
  removeItem: (key: string) => sessionStorage.removeItem(key),
}
const statuses = { PENDING: "等待确认", ACCEPTED: "所需人员已同意", REJECTED: "已有人员提出异议", SUPERSEDED: "已有更新方案" }
const buttonClass = "min-h-11 rounded-md border px-3 py-2 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
const amount = (value: number) => Number.isSafeInteger(value) ? `${formatXaCoin(value)} ${coinUnit()}` : "待核对"

/** Participants can consent to a specific paid add-on proposal; execution remains a staff action. */
export function AddonRefundProposals({ role, orderId }: { role: "boss" | "provider"; orderId: number }) {
  const session = useSession(role)
  if (!session.user) return null
  const scope = `${getStoreSlug()}:${role}:${session.user.id}:${orderId}`
  return <ScopedAddonRefundProposals key={`${scope}:${session.revision}`} role={role} orderId={orderId} accountId={Number(session.user.id)} scope={scope} />
}

function ScopedAddonRefundProposals({ role, orderId, accountId, scope }: {
  role: "boss" | "provider"; orderId: number; accountId: number; scope: string
}) {
  const api = useMemo(() => createApiClient(role), [role])
  const key = `xa:addon-refund-decision:${scope}`
  const [choice, setChoice] = useState<AddonRefundDecision | null>(null)
  const [busy, setBusy] = useState(false)
  const [issue, setIssue] = useState("")
  const [notice, setNotice] = useState("")
  const submitting = useRef(false)
  const load = useResource(async () => {
    const targets = await api.get<ProposalTarget[]>(`/orders/orders/${orderId}/addons/refund-proposals/`)
    if (!Array.isArray(targets)) throw new Error("加购退款方案暂无法读取，请重试。")
    const pending = readAddonRefundDecision(storage, key)
    const located = new Map(targets.map(target => [`${target.payment_id}:${target.proposal_id}`, target]))
    if (pending && pending !== "invalid") located.set(`${pending.paymentId}:${pending.proposalId}`, {
      payment_id: pending.paymentId, proposal_id: pending.proposalId, status: "", my_accepted: null,
    })
    const proposals = await Promise.all([...located.values()].map(async target => {
      const proposal = await api.get<AddonRefundProposal>(addonRefundProposalPath(orderId, target.payment_id, target.proposal_id))
      if (proposal.id !== target.proposal_id || proposal.payment_id !== target.payment_id)
        throw new Error("加购付款与退款方案不一致，请联系客服。")
      ownAddonRefundDecision(proposal, accountId, role)
      return proposal
    }))
    return { proposals, pending }
  }, [api, orderId, key])
  const snapshot = load.data
  const pending = snapshot?.pending
  const error = issue || load.error?.message
  useRealtime(role, event => {
    if (!submitting.current && (event.type === "reconnected" ||
        (event.type === "order_status_update" && Number(event.data?.id) === orderId))) void load.reload()
  })

  async function submit(decision: AddonRefundDecision) {
    if (submitting.current || load.loading || load.error) return
    submitting.current = true
    setBusy(true)
    setIssue("")
    setNotice("")
    try {
      const proposal = await recordAddonRefundDecision({ client: api, storage, key, orderId, accountId, role, decision })
      setChoice(null)
      setNotice(proposal.refund_executed ? "原决定已核对。退款已由客服执行，实际到账请查看钱包流水。" : "确认已记录，退款尚未执行。")
    } catch (cause) {
      setIssue(cause instanceof Error ? cause.message : "确认结果尚待核对，请重试原决定。")
    } finally {
      await load.reload()
      submitting.current = false
      setBusy(false)
    }
  }

  if (snapshot && !snapshot.proposals.length && !pending && !error && !load.loading) return null
  return <section className="space-y-3 rounded-lg border p-4 text-sm" aria-label="加购退款方案">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold">加购退款方案</h3>
      <button type="button" className={buttonClass} disabled={busy || load.loading} onClick={() => { setChoice(null); setIssue(""); void load.reload() }}>刷新方案</button>
    </div>
    {load.loading && <p role="status" className="text-muted-foreground">正在读取加购退款方案…</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {snapshot?.proposals.map(proposal => {
      const own = ownAddonRefundDecision(proposal, accountId, role)
      const selected = choice?.paymentId === proposal.payment_id && choice.proposalId === proposal.id ? choice : null
      const amountsKnown = [proposal.amount_raw, ...(role === "provider" ? [proposal.provider_burden_raw, proposal.provider_income_retained_raw] : [])]
        .every(value => Number.isSafeInteger(value) && value >= 0)
      return <article key={`${proposal.payment_id}:${proposal.id}`} className="space-y-2 rounded-md border p-3">
        <h4 className="font-medium">{proposal.addon_name} · 付款 {proposal.payment_id} · 方案 {proposal.id}</h4>
        <p>{statuses[proposal.status] || "方案状态待核对"}</p>
        <p>方案退款金额 · {amount(proposal.amount_raw)}</p>
        {role === "provider" && <>
          <p>本人承担 · {amount(proposal.provider_burden_raw)}</p>
          <p>{proposal.refund_executed ? "本人加购收益" : "本人拟保留加购收益"} · {amount(proposal.provider_income_retained_raw)}</p>
        </>}
        <p className="whitespace-pre-wrap break-words">{proposal.explanation}</p>
        <p className="text-muted-foreground">{own === true ? "你的同意已记录。" : own === false ? "你的异议已记录。" : "请核对本方案后选择是否同意。"}{proposal.refund_executed ? "退款已由客服执行，实际到账请查看钱包流水。" : "退款尚未执行。"}</p>
        {!amountsKnown && <p role="alert" className="text-destructive">方案金额暂无法核对，请联系客服。</p>}
        {proposal.status === "PENDING" && own === null && amountsKnown && !pending && !selected && <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error} onClick={() => { setIssue(""); setChoice({ paymentId: proposal.payment_id, proposalId: proposal.id, accepted: true }) }}>同意方案</button>
          <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error} onClick={() => { setIssue(""); setChoice({ paymentId: proposal.payment_id, proposalId: proposal.id, accepted: false }) }}>提出异议</button>
        </div>}
        {selected && proposal.status === "PENDING" && own === null && amountsKnown && !pending && <form
          action={`/api${addonRefundProposalPath(orderId, proposal.payment_id, proposal.id)}`} method="post"
          aria-label="确认本次加购退款决定" className="space-y-2 rounded-md bg-muted p-3"
          onSubmit={event => { event.preventDefault(); void submit(selected) }}
        >
          <p>确认{selected.accepted ? "同意" : "对"}付款 {proposal.payment_id} 的方案 {proposal.id}{selected.accepted ? "？" : "提出异议？"}决定记录后请联系客服修改方案。</p>
          <p className="text-muted-foreground">本次操作只记录决定，退款由客服另行执行。</p>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={buttonClass} disabled={busy || load.loading || !!load.error} aria-busy={busy}>{busy ? "正在记录…" : "确认提交"}</button>
            <button type="button" className={buttonClass} disabled={busy} onClick={() => setChoice(null)}>返回修改</button>
          </div>
        </form>}
      </article>
    })}
    {pending === "invalid" ? <p role="alert" className="text-destructive">原确认凭证无法读取，请联系客服核对。</p> : pending && <div className="space-y-2 rounded-md border p-3">
      <p>原付款 {pending.paymentId}、方案 {pending.proposalId} 的{pending.accepted ? "同意" : "异议"}结果待核对，将先读取本人决定。</p>
      <button type="button" className={buttonClass} disabled={busy || load.loading || !!load.error} onClick={() => void submit(pending)}>{busy ? "正在核对…" : "核对原决定"}</button>
    </div>}
    {notice && <p role="status" className="text-muted-foreground">{notice}</p>}
  </section>
}
