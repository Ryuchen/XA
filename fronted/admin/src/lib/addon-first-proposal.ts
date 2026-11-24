type Data = Record<string, any>
export interface AddonProposalTarget {
  orderId: number
  paymentId: number
  caseId: number
  caseRequestId: string
  accounts: number[]
  segments: number[]
}
export interface AddonFirstIntent {
  target: AddonProposalTarget
  payload: { create_only: true; case_id: number; case_request_id: string; request_id: string; amount_raw: number; explanation: string; affected_provider_account_ids: number[] }
}
export const positiveId = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0
export const validScopeIds = (value: unknown): value is number[] => Array.isArray(value) && value.length > 0 && value.every(positiveId) && new Set(value).size === value.length
const sameIds = (actual: unknown, expected: number[]) => Array.isArray(actual) && actual.length === expected.length &&
  validScopeIds(actual) && validScopeIds(expected) && expected.every(value => actual.includes(value))

export function integerCoinRefund(value: string): number | null {
  if (!/^[1-9]\d{0,8}$/.test(value)) return null
  const raw = BigInt(value) * 10n
  return raw <= 2147483647n ? Number(raw) : null
}

export function proposalsForCase(value: unknown, target: AddonProposalTarget): Data[] {
  if (!Array.isArray(value)) throw new Error("加购方案尚未正确读取，请重试。")
  return value.filter(row => row?.payment_id === target.paymentId && row.case_id === target.caseId)
}

export function firstProposalBlocker(rows: Data[], target: AddonProposalTarget): Data | undefined {
  return rows.find(row => row?.payment_id === target.paymentId &&
    (row.case_id === target.caseId && row.case_request_id === target.caseRequestId ||
      ["PENDING", "ACCEPTED"].includes(row.status) && row.refund_executed !== true))
}

export function verifyProposalScope(proposal: Data, target: AddonProposalTarget) {
  if (!positiveId(proposal?.id) || proposal.payment_id !== target.paymentId || proposal.case_id !== target.caseId || proposal.case_request_id !== target.caseRequestId ||
    !Array.isArray(proposal.participants) || !sameIds(proposal.participants.map((person: Data) => person.account_id), target.accounts) ||
    !proposal.participants.every((person: Data) => Array.isArray(person.share_plan)) ||
    !sameIds(proposal.participants.flatMap((person: Data) => person.share_plan.map((share: Data) => share.segment_id)), target.segments)) {
    throw new Error("方案范围与工单付款、陪玩或履约段不一致，请核对原工单。")
  }
  return proposal
}

export function parseFirstIntent(raw: string | null, current: AddonProposalTarget): AddonFirstIntent | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw)
    const target = value?.target
    const p = value?.payload
    if (target?.orderId === current.orderId && target.paymentId === current.paymentId && target.caseId === current.caseId &&
      positiveId(target.orderId) && positiveId(target.paymentId) && positiveId(target.caseId) && validScope(target) &&
      p?.create_only === true && p.case_id === target.caseId && p.case_request_id === target.caseRequestId &&
      typeof p.request_id === "string" && p.request_id.trim() === p.request_id && p.request_id.length > 0 && p.request_id.length <= 64 &&
      Number.isSafeInteger(p.amount_raw) && p.amount_raw > 0 && p.amount_raw <= 2147483647 && p.amount_raw % 10 === 0 &&
      typeof p.explanation === "string" && p.explanation.trim().length >= 2 && p.explanation.length <= 1000 &&
      sameIds(p.affected_provider_account_ids, target.accounts) &&
      Object.keys(p).sort().join(",") === "affected_provider_account_ids,amount_raw,case_id,case_request_id,create_only,explanation,request_id") return value
  } catch { /* Do not silently replace an unknown first creation. */ }
  throw new Error("原加购方案请求记录无法核对，请保留浏览器记录并联系管理员。")
}

export function validScope(target: AddonProposalTarget): boolean {
  return positiveId(target.orderId) && positiveId(target.paymentId) && positiveId(target.caseId) &&
    typeof target.caseRequestId === "string" && target.caseRequestId.trim().length > 0 && target.caseRequestId.length <= 64 &&
    validScopeIds(target.accounts) && validScopeIds(target.segments)
}

export function checkedFirstProposal(proposal: Data, intent: AddonFirstIntent) {
  verifyProposalScope(proposal, intent.target)
  const decisions = proposal.decisions
  const expected = intent.target.accounts
  const validDecisions = Array.isArray(decisions) && decisions.length === expected.length + 1 &&
    decisions.every((item: Data) => positiveId(item?.account_id) && [true, false, null].includes(item.accepted) &&
      (item.accepted === null || typeof item.decided_at === "string" && item.decided_at.length > 0)) &&
    new Set(decisions.map((item: Data) => item.account_id)).size === decisions.length &&
    decisions.filter((item: Data) => item.role === "BOSS" && !expected.includes(item.account_id)).length === 1 &&
    expected.every(account => decisions.some((item: Data) => item.role === "PROVIDER" && item.account_id === account))
  if (proposal.request_id !== intent.payload.request_id || proposal.amount_raw !== intent.payload.amount_raw ||
    proposal.explanation !== intent.payload.explanation || !validDecisions || !["PENDING", "ACCEPTED", "REJECTED", "SUPERSEDED"].includes(proposal.status)) throw new Error("方案回执与本人原请求、金额、说明或确认记录不一致，请保留原请求核对。")
  return proposal
}

export async function submitFirstProposal(intent: AddonFirstIntent, io: { recovering?: boolean; save: (value: AddonFirstIntent) => void; send: (value: AddonFirstIntent) => Promise<Data>; clear: () => void }) {
  io.save(intent)
  let data: Data
  try { data = await io.send(intent) }
  catch (cause) {
    const failure = cause as { status?: number; body?: { code?: number; msg?: string } } | null
    if (!io.recovering && failure?.status === 400 && failure.body?.code === 400 && typeof failure.body.msg === "string") {
      try { io.clear() } catch { /* Keep the original operation when storage is unavailable. */ }
    }
    throw cause
  }
  const proposal = checkedFirstProposal(data, intent)
  io.clear()
  return proposal
}
