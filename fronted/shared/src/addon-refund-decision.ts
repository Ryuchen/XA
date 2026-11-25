export type AddonRefundDecision = {
  paymentId: number
  proposalId: number
  accepted: boolean
}

export type AddonRefundProposalTarget = {
  payment_id: number
  proposal_id: number
  status: string
  my_accepted: boolean | null
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

export async function loadAddonRefundProposalTargets(client: Pick<DecisionClient, "get">, orderId: number) {
  const targets = await client.get<AddonRefundProposalTarget[]>(`/orders/orders/${orderId}/addons/refund-proposals/`)
  if (!Array.isArray(targets)) throw new Error("加购退款方案暂无法读取，请重试。")
  return targets
}

export async function loadAddonRefundProposal(client: Pick<DecisionClient, "get">, orderId: number,
  paymentId: number, proposalId: number, accountId: number, role: "boss" | "provider") {
  const proposal = await client.get<AddonRefundProposal>(addonRefundProposalPath(orderId, paymentId, proposalId))
  if (!proposal || proposal.id !== proposalId || proposal.payment_id !== paymentId ||
      !["PENDING", "ACCEPTED", "REJECTED", "SUPERSEDED"].includes(proposal.status))
    throw new Error("原加购退款方案暂无法核对，请联系客服。")
  ownAddonRefundDecision(proposal, accountId, role)
  return proposal
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
  if (own?.length !== 1 || (own[0].accepted !== null && typeof own[0].accepted !== "boolean"))
    throw new Error("本人确认记录暂无法核对，请联系客服。")
  return own[0].accepted
}

export function addonRefundAmountsKnown(proposal: AddonRefundProposal, role: "boss" | "provider") {
  return [proposal.amount_raw, ...(role === "provider" ? [proposal.provider_burden_raw, proposal.provider_income_retained_raw] : [])]
    .every(value => Number.isSafeInteger(value) && value >= 0)
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
  let proposal = await loadAddonRefundProposal(client, orderId, decision.paymentId, decision.proposalId, accountId, role)
  const recorded = ownAddonRefundDecision(proposal, accountId, role)
  if (recorded !== decision.accepted) {
    if (typeof recorded === "boolean") {
      storage.removeItem(key)
      throw new Error("本人已记录相反决定，请核对方案或联系客服。")
    }
    if (proposal.status !== "PENDING") {
      storage.removeItem(key)
      throw new Error("原方案已结束或被替换，请查看当前方案。")
    }
    if (!addonRefundAmountsKnown(proposal, role))
      throw new Error("方案金额暂无法核对，请联系客服。")
    proposal = await client.post<AddonRefundProposal>(path, { accepted: decision.accepted })
    if (proposal.id !== decision.proposalId || proposal.payment_id !== decision.paymentId ||
        ownAddonRefundDecision(proposal, accountId, role) !== decision.accepted)
      throw new Error("决定提交结果尚待核对，请保留原方案重试。")
  }
  storage.removeItem(key)
  return proposal
}
