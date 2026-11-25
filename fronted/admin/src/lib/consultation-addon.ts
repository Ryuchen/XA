type RecordData = Record<string, any>
const id = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0
export interface AddonCaseIntent {
  session_id: number
  payload: { order_id: number; chat_message_id: number; addon_payment_id: number; affected_provider_account_ids: number[]; affected_segment_ids: number[]; reason: string; client_request_id: string }
}

export function returnedAddonScope(payment: RecordData, rows: unknown): { provider: number; segment: number; version: number } {
  const unavailable = () => new Error("该笔付款的已确认转回段待核对，请从订单核对接替记录。")
  if (payment.paid !== true || !id(payment.payment_id) || !id(payment.provider_account_id) || !id(payment.segment_id) || !Array.isArray(rows) || !rows.length) throw unavailable()
  if (rows.some(row => !row || row.payment_id !== payment.payment_id || row.status !== "ACCEPTED" || !id(row.version))) throw unavailable()
  const accepted = rows.filter(row => row.status === "ACCEPTED" && row.accepted_at).sort((a, b) => b.version - a.version)
  const latest = accepted[0]
  if (accepted.length !== 2 || accepted.some(row => row.kind !== "STANDARD") || !id(latest?.version) || latest !== rows[0] ||
    latest.target_provider_account_id !== payment.provider_account_id || accepted[1].source_provider_account_id !== payment.provider_account_id ||
    latest.source_provider_account_id !== accepted[1].target_provider_account_id || latest.version <= accepted[1].version ||
    latest.source_provider_account_id === latest.target_provider_account_id) throw unavailable()
  if (!accepted.every(confirmed => {
    const decisions = confirmed.decisions
    const current = Array.isArray(confirmed.shares) ? confirmed.shares.filter((share: RecordData) => share.is_current === true) : []
    return current.length === 1 && id(current[0].segment_id) && current[0].provider_account_id === confirmed.target_provider_account_id &&
    Number.isSafeInteger(current[0].basis_raw) && current[0].basis_raw > 0 &&
    Array.isArray(decisions) && decisions.length === 3 && !decisions.some(item => !item || item.accepted !== true || !item.decided_at) &&
    decisions.filter(item => item.role === "BOSS").length === 1 &&
    [confirmed.source_provider_account_id, confirmed.target_provider_account_id].every(account => decisions.some(item => item.role === "PROVIDER" && item.account_id === account))
  })) throw unavailable()
  if (!Array.isArray(latest.shares)) throw unavailable()
  const current = latest.shares.filter((share: RecordData) => share.is_current === true)
  if (current.length !== 1 || !id(current[0].segment_id) || current[0].provider_account_id !== payment.provider_account_id ||
    !Number.isSafeInteger(current[0].basis_raw) || current[0].basis_raw <= 0 || current[0].segment_id === payment.segment_id) throw unavailable()
  return { provider: current[0].provider_account_id, segment: current[0].segment_id, version: latest.version }
}

export function parseAddonIntent(raw: string | null, session: number, message: number, order: number): AddonCaseIntent | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw)
    const body = value?.payload
    if (value?.session_id === session && body?.order_id === order && body.chat_message_id === message &&
      id(body.addon_payment_id) && Array.isArray(body.affected_provider_account_ids) && body.affected_provider_account_ids.length === 1 && id(body.affected_provider_account_ids[0]) &&
      Array.isArray(body.affected_segment_ids) && body.affected_segment_ids.length === 1 && id(body.affected_segment_ids[0]) &&
      typeof body.reason === "string" && body.reason.trim().length >= 2 && body.reason.length <= 1000 &&
      typeof body.client_request_id === "string" && body.client_request_id.trim() && body.client_request_id.length <= 64 &&
      Object.keys(body).sort().join(",") === "addon_payment_id,affected_provider_account_ids,affected_segment_ids,chat_message_id,client_request_id,order_id,reason") return value
  } catch { /* Do not replace an unreadable pending request. */ }
  throw new Error("原加购售后请求记录无法核对，请保留浏览器记录并联系管理员。")
}

export function checkedAddonCase(intent: AddonCaseIntent, receipt: RecordData) {
  const p = intent.payload
  const same = (actual: unknown, expected: number[]) => Array.isArray(actual) && actual.length === expected.length && actual.every((value, index) => value === expected[index])
  const history = Array.isArray(receipt?.history) ? receipt.history.find((entry: RecordData) => entry.client_request_id === p.client_request_id) : null
  const current = receipt?.client_request_id === p.client_request_id
  if (!id(receipt?.id) || !["OPEN", "RESOLVED", "REFUNDED", "PARTIALLY_REFUNDED"].includes(receipt.status) || receipt.order !== p.order_id ||
    (current && (receipt.addon_payment !== p.addon_payment_id || receipt.reason !== p.reason ||
    !same(receipt.affected_provider_account_ids, p.affected_provider_account_ids) || !same(receipt.affected_segment_ids, p.affected_segment_ids))) ||
    !history || history.consultation_message_id !== p.chat_message_id || history.consultation_session_id !== intent.session_id || history.addon_payment_id !== p.addon_payment_id ||
    history.reason !== p.reason || !same(history.affected_provider_account_ids, p.affected_provider_account_ids) || !same(history.affected_segment_ids, p.affected_segment_ids)) throw new Error("工单回执与原咨询、付款或履约段不一致，请保留原请求核对。")
  return receipt
}

export async function submitAddonCase(intent: AddonCaseIntent, io: { recovering?: boolean; save: (value: AddonCaseIntent) => void; clear: () => void; send: (value: AddonCaseIntent) => Promise<RecordData> }) {
  io.save(intent)
  let receipt: RecordData
  try { receipt = await io.send(intent) }
  catch (cause) {
    const failure = cause as { status?: number; body?: { code?: number; msg?: string } } | null
    if (!io.recovering && failure?.status === 400 && failure.body?.code === 400 && typeof failure.body.msg === "string") {
      try { io.clear() } catch { /* Preserve the original intent if storage fails. */ }
    }
    throw cause
  }
  checkedAddonCase(intent, receipt)
  io.clear()
  return receipt
}
