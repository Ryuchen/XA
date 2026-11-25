export type AddonRefundRole = "boss" | "provider"

export type AddonRefundDecision = {
  paymentId: number
  proposalId: number
  accepted: boolean
}

export type PendingAddonRefundDecision = AddonRefundDecision & {
  caseId?: number
  scopeKey?: string
}

export type AddonRefundProposalTarget = {
  payment_id: number
  proposal_id: number
  status: string
  my_accepted: boolean | null
}

export type AddonRefundProposal = {
  id: number
  order_id?: number
  payment_id: number
  case_id?: number
  addon_name: string
  amount_raw: number
  provider_burden_raw: number
  provider_income_retained_raw: number
  explanation: string
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "SUPERSEDED"
  refund_executed: boolean
  decisions: { account_id: number; role: string; accepted: boolean | null; decided_at: string | null }[]
  participants?: {
    account_id: number
    burden_raw?: number
    share_plan?: {
      segment_id?: unknown
      basis?: unknown
      refund_basis?: unknown
      burden?: unknown
    }[]
  }[]
}

export type ProposalScopeRow = {
  accountId: number
  segmentId: number
  basisRaw: number
  refundBasisRaw: number
  burdenRaw: number
}

type DecisionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">
type DecisionClient = {
  get<T>(path: string): Promise<T>
  post<T>(path: string, payload: unknown): Promise<T>
}

const positiveId = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0
const nonnegativeInteger = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) >= 0
const authenticationRejected = (cause: unknown) =>
  !!cause && typeof cause === "object" && "status" in cause &&
  (cause.status === 401 || cause.status === 403)

export function addonRefundProposalPath(orderId: number, paymentId: number, proposalId: number) {
  return `/orders/orders/${orderId}/addons/payments/${paymentId}/refund-proposals/${proposalId}/`
}

export function readPendingAddonRefundDecision(
  storage: DecisionStorage,
  key: string,
): PendingAddonRefundDecision | "invalid" | null {
  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw)
    if (!positiveId(value?.paymentId) || !positiveId(value?.proposalId) ||
        typeof value?.accepted !== "boolean" ||
        (value.caseId !== undefined && !positiveId(value.caseId)) ||
        (value.scopeKey !== undefined && (typeof value.scopeKey !== "string" || !value.scopeKey)))
      return "invalid"
    return value
  } catch {
    return "invalid"
  }
}

export function proposalScope(
  proposal: AddonRefundProposal,
  role: AddonRefundRole,
  accountId: number,
): ProposalScopeRow[] {
  if (!Array.isArray(proposal.participants)) throw new Error("方案受影响履约段暂无法核对，请联系客服。")
  const participants = role === "provider"
    ? proposal.participants.filter(row => row.account_id === accountId)
    : proposal.participants
  if (!participants.length) throw new Error("本方案没有可核对的本人履约段，请联系客服。")
  const accounts = new Set<number>()
  const segments = new Set<string>()
  const result: ProposalScopeRow[] = []
  for (const participant of participants) {
    if (!positiveId(participant.account_id) || accounts.has(participant.account_id))
      throw new Error("方案陪玩归属暂无法核对，请联系客服。")
    accounts.add(participant.account_id)
    if (!Array.isArray(participant.share_plan) || !participant.share_plan.length)
      throw new Error("方案受影响履约段暂无法核对，请联系客服。")
    for (const share of participant.share_plan) {
      if (!positiveId(share.segment_id) || !nonnegativeInteger(share.basis) ||
          !nonnegativeInteger(share.refund_basis) || !nonnegativeInteger(share.burden))
        throw new Error("方案履约段金额暂无法核对，请联系客服。")
      const key = `${participant.account_id}:${share.segment_id}`
      if (segments.has(key)) throw new Error("方案履约段重复，请联系客服核对。")
      segments.add(key)
      result.push({
        accountId: participant.account_id,
        segmentId: share.segment_id,
        basisRaw: share.basis,
        refundBasisRaw: share.refund_basis,
        burdenRaw: share.burden,
      })
    }
  }
  return result.sort((left, right) => left.accountId - right.accountId || left.segmentId - right.segmentId)
}

export function scopeRowKey(row: ProposalScopeRow) {
  return `${row.accountId}:${row.segmentId}`
}

export function proposalScopeKey(rows: ProposalScopeRow[]) {
  if (!rows.length) throw new Error("方案受影响履约段为空，请联系客服。")
  return rows
    .map(row => `${row.accountId}:${row.segmentId}:${row.basisRaw}:${row.refundBasisRaw}:${row.burdenRaw}`)
    .sort()
    .join("|")
}

export function scopeSelectionComplete(rows: ProposalScopeRow[], selected: ReadonlySet<string>) {
  return rows.length > 0 && rows.every(row => selected.has(scopeRowKey(row))) && selected.size === rows.length
}

export function ownAddonRefundDecision(
  proposal: AddonRefundProposal,
  accountId: number,
  role: AddonRefundRole,
) {
  const own = proposal.decisions?.filter(decision => decision.account_id === accountId && decision.role === role.toUpperCase())
  if (own?.length !== 1 || (own[0].accepted !== null && typeof own[0].accepted !== "boolean"))
    throw new Error("本人确认记录暂无法核对，请联系客服。")
  return own[0].accepted
}

function addonRefundAmounts(proposal: AddonRefundProposal, role: AddonRefundRole) {
  return [proposal.amount_raw, ...(role === "provider" ? [proposal.provider_burden_raw, proposal.provider_income_retained_raw] : [])]
}

function addonRefundReceiptStatusMatches(proposal: AddonRefundProposal, accepted: boolean) {
  if (!Array.isArray(proposal.decisions) || proposal.decisions.some(row =>
      row.accepted !== null && typeof row.accepted !== "boolean")) return false
  if (!accepted) return proposal.status === "REJECTED"
  if (proposal.decisions.some(row => row.accepted === false)) return false
  return proposal.status === (proposal.decisions.some(row => row.accepted === null) ? "PENDING" : "ACCEPTED")
}

export function addonRefundAmountsKnown(proposal: AddonRefundProposal, role: AddonRefundRole) {
  return addonRefundAmounts(proposal, role).every(value => nonnegativeInteger(value))
}

export async function loadAddonRefundProposalTargets(client: Pick<DecisionClient, "get">, orderId: number) {
  const targets = await client.get<AddonRefundProposalTarget[]>(`/orders/orders/${orderId}/addons/refund-proposals/`)
  if (!Array.isArray(targets) || targets.some(row => !row || !positiveId(row.payment_id) || !positiveId(row.proposal_id) ||
      typeof row.status !== "string" || !(row.my_accepted === null || typeof row.my_accepted === "boolean")) ||
      new Set(targets.map(row => `${row.payment_id}:${row.proposal_id}`)).size !== targets.length)
    throw new Error("加购退款方案暂无法读取，请重试。")
  return targets
}

function validateAddonRefundProposal(proposal: AddonRefundProposal, orderId: number, paymentId: number, proposalId: number): asserts proposal is AddonRefundProposal & { case_id: number } {
  if (!proposal || proposal.id !== proposalId || proposal.payment_id !== paymentId ||
      proposal.order_id !== undefined && proposal.order_id !== orderId ||
      !positiveId(proposal.case_id) || typeof proposal.refund_executed !== "boolean" ||
      !["PENDING", "ACCEPTED", "REJECTED", "SUPERSEDED"].includes(proposal.status))
    throw new Error("原加购退款方案暂无法核对，请联系客服。")
}

export async function loadAddonRefundProposal(
  client: Pick<DecisionClient, "get">,
  orderId: number,
  paymentId: number,
  proposalId: number,
  accountId: number,
  role: AddonRefundRole,
) {
  const proposal = await client.get<AddonRefundProposal>(addonRefundProposalPath(orderId, paymentId, proposalId))
  validateAddonRefundProposal(proposal, orderId, paymentId, proposalId)
  ownAddonRefundDecision(proposal, accountId, role)
  return proposal
}

/**
 * Preserve the immutable payment, proposal, decision and fulfillment scope across uncertain results.
 * The current user decision endpoint accepts only `accepted`; its idempotency key is the account and
 * proposal. A case-opening client_request_id requires the backend contract described in the handoff.
 */
export async function recordAddonRefundDecision({ client, storage, key, orderId, accountId, role, decision, scopeKey }: {
  client: DecisionClient
  storage: DecisionStorage
  key: string
  orderId: number
  accountId: number
  role: AddonRefundRole
  decision: AddonRefundDecision
  scopeKey?: string
}) {
  if (!positiveId(decision.paymentId) || !positiveId(decision.proposalId) || typeof decision.accepted !== "boolean")
    throw new Error("原加购退款决定暂无法核对，请联系客服。")
  const saved = readPendingAddonRefundDecision(storage, key)
  if (saved === "invalid") throw new Error("原确认凭证无法读取，请联系客服核对。")
  if (saved && (saved.paymentId !== decision.paymentId || saved.proposalId !== decision.proposalId || saved.accepted !== decision.accepted))
    throw new Error("请先核对原付款、原方案和原决定。")
  if (saved?.scopeKey && scopeKey && saved.scopeKey !== scopeKey)
    throw new Error("原决定的受影响履约段已变化，请联系客服核对。")
  if (!saved && !scopeKey)
    throw new Error("请先核对原方案列出的受影响履约段。")

  const pending: PendingAddonRefundDecision = { ...decision, ...(scopeKey ? { scopeKey } : {}) }
  if (!saved) storage.setItem(key, JSON.stringify(pending))
  const path = addonRefundProposalPath(orderId, decision.paymentId, decision.proposalId)
  let proposal: AddonRefundProposal
  try {
    proposal = await loadAddonRefundProposal(client, orderId, decision.paymentId, decision.proposalId, accountId, role)
  } catch (cause) {
    if (!saved && authenticationRejected(cause)) storage.removeItem(key)
    throw cause
  }
  if (saved?.caseId !== undefined && saved.caseId !== proposal.case_id)
    throw new Error("原售后工单轮次已变化，请保留原付款、原方案和原决定。")

  const recorded = ownAddonRefundDecision(proposal, accountId, role)
  if (recorded === decision.accepted) {
    storage.removeItem(key)
    return proposal
  }
  if (typeof recorded === "boolean") {
    storage.removeItem(key)
    throw new Error("本人已记录相反决定，请核对方案或联系客服。")
  }
  if (proposal.status !== "PENDING" || proposal.refund_executed) {
    storage.removeItem(key)
    throw new Error("原方案已结束或被替换，请查看当前方案。")
  }
  if (!addonRefundAmountsKnown(proposal, role))
    throw new Error("方案金额暂无法核对，请联系客服。")
  if (!scopeKey || proposalScopeKey(proposalScope(proposal, role, accountId)) !== scopeKey)
    throw new Error("原方案受影响履约段已变化，请保留原决定并联系客服核对。")

  const confirmedTarget: PendingAddonRefundDecision = { ...decision, caseId: proposal.case_id, scopeKey }
  storage.setItem(key, JSON.stringify(confirmedTarget))
  const requestedAmounts = addonRefundAmounts(proposal, role)
  try {
    proposal = await client.post<AddonRefundProposal>(path, { accepted: decision.accepted })
  } catch (cause) {
    if (!saved && authenticationRejected(cause)) storage.removeItem(key)
    throw cause
  }
  try {
    validateAddonRefundProposal(proposal, orderId, decision.paymentId, decision.proposalId)
    if (proposal.case_id !== confirmedTarget.caseId || proposal.refund_executed ||
        !addonRefundAmountsKnown(proposal, role) ||
        addonRefundAmounts(proposal, role).some((value, index) => value !== requestedAmounts[index]) ||
        !addonRefundReceiptStatusMatches(proposal, decision.accepted) ||
        proposalScopeKey(proposalScope(proposal, role, accountId)) !== scopeKey ||
        ownAddonRefundDecision(proposal, accountId, role) !== decision.accepted)
      throw new Error("receipt mismatch")
  } catch {
    throw new Error("决定提交结果尚待核对，请保留原付款、原方案和原履约段重试。")
  }
  storage.removeItem(key)
  return proposal
}
