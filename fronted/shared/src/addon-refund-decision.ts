export type AddonRefundDecision = {
  paymentId: number
  proposalId: number
  accepted: boolean
}

export type AddonRefundProposal = {
  id: number
  payment_id: number
  addon_name: string
  amount_raw: number
  provider_burden_raw: number
  provider_income_raw: number
  provider_income_retained_raw: number
  explanation: string
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "SUPERSEDED"
  refund_executed: boolean
  decisions: { account_id: number; role: string; accepted: boolean | null; decided_at: string | null }[]
}

type DecisionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">
type DecisionClient = {
  get<T>(path: string): Promise<T>
  post<T>(path: string, payload: unknown): Promise<T>
}

export function addonRefundProposalPath(orderId: number, paymentId: number, proposalId: number) {
  return `/orders/orders/${orderId}/addons/payments/${paymentId}/refund-proposals/${proposalId}/`
}

export function readAddonRefundDecision(storage: DecisionStorage, key: string): AddonRefundDecision | "invalid" | null {
  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw)
    return Number.isSafeInteger(value?.paymentId) && value.paymentId > 0 &&
      Number.isSafeInteger(value?.proposalId) && value.proposalId > 0 && typeof value.accepted === "boolean"
      ? value : "invalid"
  } catch {
    return "invalid"
  }
}

export function ownAddonRefundDecision(proposal: AddonRefundProposal, accountId: number, role: "boss" | "provider") {
  const own = proposal.decisions?.filter(decision => decision.account_id === accountId && decision.role === role.toUpperCase())
  if (own?.length !== 1) throw new Error("本人确认记录暂无法核对，请联系客服。")
  return own[0].accepted
}

/** Reconcile the original decision before replaying its immutable payment/proposal target. */
export async function recordAddonRefundDecision({ client, storage, key, orderId, accountId, role, decision }: {
  client: DecisionClient
  storage: DecisionStorage
  key: string
  orderId: number
  accountId: number
  role: "boss" | "provider"
  decision: AddonRefundDecision
}) {
  const saved = readAddonRefundDecision(storage, key)
  if (saved === "invalid") throw new Error("原确认凭证无法读取，请联系客服核对。")
  if (saved && (saved.paymentId !== decision.paymentId || saved.proposalId !== decision.proposalId || saved.accepted !== decision.accepted))
    throw new Error("请先核对原方案和原决定。")
  storage.setItem(key, JSON.stringify(decision))
  const path = addonRefundProposalPath(orderId, decision.paymentId, decision.proposalId)
  let proposal = await client.get<AddonRefundProposal>(path)
  if (proposal.id !== decision.proposalId || proposal.payment_id !== decision.paymentId)
    throw new Error("原加购退款方案暂无法核对，请联系客服。")
  if (ownAddonRefundDecision(proposal, accountId, role) !== decision.accepted) {
    proposal = await client.post<AddonRefundProposal>(path, { accepted: decision.accepted })
    if (proposal.id !== decision.proposalId || proposal.payment_id !== decision.paymentId ||
        ownAddonRefundDecision(proposal, accountId, role) !== decision.accepted)
      throw new Error("决定提交结果尚待核对，请保留原方案重试。")
  }
  storage.removeItem(key)
  return proposal
}
