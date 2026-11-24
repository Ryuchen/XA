import { positiveId, validScope, verifyProposalScope, type AddonProposalTarget } from "./addon-first-proposal.ts"
type Data = Record<string, any>
type Signature = { account_id: number; role: string; accepted: true; decided_at: string }
export interface AddonExecutionIntent {
  target: AddonProposalTarget
  proposalId: number
  amountRaw: number
  explanation: string
  acceptedAt: string
  signatures: Signature[]
}
const time = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value))
const amount = (value: unknown): value is number => positiveId(value) && value <= 2147483647 && value % 10 === 0
function signatures(value: unknown, target: AddonProposalTarget): Signature[] {
  if (!Array.isArray(value) || value.length !== target.accounts.length + 1 ||
    value.some(item => !positiveId(item?.account_id) || item.accepted !== true || !time(item.decided_at)) ||
    new Set(value.map(item => item.account_id)).size !== value.length ||
    value.filter(item => item.role === "BOSS" && !target.accounts.includes(item.account_id)).length !== 1 ||
    !target.accounts.every(account => value.some(item => item.role === "PROVIDER" && item.account_id === account))) throw new Error("老板或受影响陪玩的已签决定待核对，暂不能执行。")
  return value.map(item => ({ account_id: item.account_id, role: item.role, accepted: true as const, decided_at: item.decided_at })).sort((a, b) => a.account_id - b.account_id)
}
function signed(proposal: Data, target: AddonProposalTarget) {
  verifyProposalScope(proposal, target)
  if (!validScope(target) || proposal.status !== "ACCEPTED" || !time(proposal.accepted_at) || !amount(proposal.amount_raw) || typeof proposal.explanation !== "string" || proposal.explanation.trim().length < 2) throw new Error("方案状态、已签金额或工单范围待核对，暂不能执行。")
  return signatures(proposal.decisions, target)
}
export function captureAddonExecution(proposal: Data, target: AddonProposalTarget): AddonExecutionIntent {
  const accepted = signed(proposal, target)
  if (proposal.execution_available !== true || proposal.refund_executed !== false) throw new Error("服务端未确认该方案当前可执行，请重新读取。")
  return { target, proposalId: proposal.id, amountRaw: proposal.amount_raw, explanation: proposal.explanation, acceptedAt: proposal.accepted_at, signatures: accepted }
}
export function sameSignedProposal(proposal: Data, intent: AddonExecutionIntent) {
  const current = signed(proposal, intent.target)
  if (proposal.id !== intent.proposalId || proposal.amount_raw !== intent.amountRaw || proposal.explanation !== intent.explanation ||
    proposal.accepted_at !== intent.acceptedAt || JSON.stringify(current) !== JSON.stringify(intent.signatures)) throw new Error("重读方案与原付款、方案或已签金额不一致，请核对原方案。")
  return proposal
}
export function parseAddonExecution(raw: string | null, current: AddonProposalTarget): AddonExecutionIntent | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw)
    if (validScope(value?.target) && value.target.orderId === current.orderId && value.target.paymentId === current.paymentId &&
      value.target.caseId === current.caseId && positiveId(value.proposalId) && amount(value.amountRaw) &&
      typeof value.explanation === "string" && value.explanation.trim().length >= 2 && time(value.acceptedAt) &&
      JSON.stringify(signatures(value.signatures, value.target)) === JSON.stringify(value.signatures)) return value
  } catch { /* Never replace an unreadable execution result. */ }
  throw new Error("原方案执行记录无法核对，请保留浏览器记录并联系管理员。")
}

export function verifiedAddonExecution(proposal: Data, intent: AddonExecutionIntent) {
  sameSignedProposal(proposal, intent)
  const proof = proposal.execution_receipt
  if (proposal.refund_executed !== true || !positiveId(proof?.refund_id) || !positiveId(proof.buyer_transaction_id) ||
    proof.amount_raw !== intent.amountRaw || !time(proof.executed_at)) throw new Error("原方案退款及返款流水凭证尚未核实，原执行记录继续保留。")
  return proposal
}
/** Read-only reconciliation does not require a locally saved attempt or the creator's request ID. */
export function readExecutedAddonProposal(proposal: Data, target: AddonProposalTarget) {
  const accepted = signed(proposal, target)
  return verifiedAddonExecution(proposal, { target, proposalId: proposal.id, amountRaw: proposal.amount_raw,
    explanation: proposal.explanation, acceptedAt: proposal.accepted_at, signatures: accepted })
}
/** Historical receipts get their scope from the authorized case history, never from the proposal alone. */
export function readAddonReceiptTarget(proposal: Data, current: AddonProposalTarget, history: unknown): AddonProposalTarget {
  if (proposal?.payment_id !== current.paymentId || proposal.case_id !== current.caseId ||
    typeof proposal.case_request_id !== "string" || !proposal.case_request_id.trim()) throw new Error("历史方案的原工单及付款归属待核对。")
  if (proposal.case_request_id === current.caseRequestId) {
    if (!validScope(current)) throw new Error("当前工单范围待核对。")
    verifyProposalScope(proposal, current)
    return current
  }
  if (!Array.isArray(history)) throw new Error("原工单历史尚未读取，历史退款凭证待核对。")
  const openings = history.filter(entry => entry?.status === "OPEN" && entry.client_request_id === proposal.case_request_id)
  if (openings.length !== 1 || openings[0].addon_payment_id !== current.paymentId ||
    !Array.isArray(openings[0].affected_provider_account_ids) || !Array.isArray(openings[0].affected_segment_ids)) throw new Error("历史方案缺少唯一且匹配原付款的开案记录。")
  const original: AddonProposalTarget = { ...current, caseRequestId: openings[0].client_request_id,
    accounts: [...openings[0].affected_provider_account_ids], segments: [...openings[0].affected_segment_ids] }
  if (!validScope(original)) throw new Error("原工单历史的人员及履约段范围待核对。")
  verifyProposalScope(proposal, original)
  return original
}
interface ExecutionIo {
  read: () => Promise<unknown>
  save: (intent: AddonExecutionIntent) => void
  send: (intent: AddonExecutionIntent) => Promise<Data>
  clear: () => void
}
function fromList(rows: unknown, intent: AddonExecutionIntent): Data {
  if (!Array.isArray(rows)) throw new Error("原付款的方案列表回执待核对。")
  const proposal = rows.find(row => row?.id === intent.proposalId)
  if (!proposal) throw new Error("当前未读到原方案，请保留原执行记录核对。")
  return sameSignedProposal(proposal, intent)
}
export async function checkAddonExecution(intent: AddonExecutionIntent, io: Pick<ExecutionIo, "read" | "clear">) {
  const proposal = verifiedAddonExecution(fromList(await io.read(), intent), intent)
  io.clear()
  return proposal
}
/** First writes always re-read. An explicit original-key retry can recover a guarded historical read. */
export async function executeAddonExecution(intent: AddonExecutionIntent, io: ExecutionIo, retrySubmitted = false) {
  let rows: unknown
  let guardedRead = false
  try { rows = await io.read() }
  catch (cause) {
    const status = (cause as { status?: number } | null)?.status
    if (!retrySubmitted || status === undefined || !([0, 400, 408, 429].includes(status) || status >= 500)) throw cause
    guardedRead = true
  }
  if (!guardedRead) {
    const proposal = fromList(rows, intent)
    if (proposal.refund_executed === true) {
      const verified = verifiedAddonExecution(proposal, intent)
      io.clear()
      return verified
    }
    if (proposal.execution_available !== true || proposal.refund_executed !== false) throw new Error("服务端未确认原方案当前可执行，暂不能提交。")
  }
  io.save(intent)
  const proposal = verifiedAddonExecution(await io.send(intent), intent)
  io.clear()
  return proposal
}
