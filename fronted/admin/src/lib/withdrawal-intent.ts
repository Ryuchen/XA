import { sha256 } from "@noble/hashes/sha2.js"
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js"

export const withdrawalActionLabels = {
  approve: "平台终审",
  paid: "登记实际打款",
  "mark-unknown": "标记付款待核实",
  reject: "驳回提现",
  cancel: "取消提现",
}

export type WithdrawalAction = keyof typeof withdrawalActionLabels
export type WithdrawalIntent = { store: string; rowId: number; kind: WithdrawalAction; requestId: string; fingerprint?: string }
export type WithdrawalAuditEvent = {
  request_id: string; actor_id: string; action: WithdrawalAction; to_status: string; audit_status: string
}

export function withdrawalIntentKey(actor: string, sessionId: string) {
  return `xa:platform-withdrawal:pending:${actor}:${sessionId}`
}

export function withdrawalFingerprint(key: string, intent: WithdrawalIntent, payload: Record<string, string | boolean | number>) {
  const fields = Object.keys(payload).sort().map(name => [name, payload[name]])
  const content = JSON.stringify(["platform-withdrawal:v1", key, intent.store, intent.rowId, intent.kind, intent.requestId, fields])
  return bytesToHex(sha256(utf8ToBytes(content)))
}

export const withdrawalContentMismatch = "输入与原操作内容不一致，请核对原内容后再提交。"

function storedWithdrawalIntent({ store, rowId, kind, requestId, fingerprint }: WithdrawalIntent): WithdrawalIntent {
  return { store, rowId, kind, requestId, fingerprint }
}

export function readWithdrawalIntents(key: string): WithdrawalIntent[] {
  const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? "[]")
  if (!Array.isArray(value) || !value.every((item: WithdrawalIntent) => item
      && typeof item.store === "string"
      && /^[a-z][a-z0-9-]{0,47}$/.test(item.store)
      && Number.isSafeInteger(item.rowId) && item.rowId > 0
      && Object.hasOwn(withdrawalActionLabels, item.kind)
      && typeof item.requestId === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(item.requestId)
      && (item.fingerprint === undefined || (typeof item.fingerprint === "string" && /^[a-f0-9]{64}$/.test(item.fingerprint))))) {
    throw new Error("浏览器中的原操作记录无法读取，请先核对提现处理记录。")
  }
  return value.map(storedWithdrawalIntent)
}

export function rememberWithdrawalIntent(key: string, intent: WithdrawalIntent): WithdrawalIntent[] {
  const existing = readWithdrawalIntents(key)
  const previous = existing.find(item => item.store === intent.store && item.rowId === intent.rowId)
  if (previous && (previous.requestId !== intent.requestId || previous.kind !== intent.kind)) {
    throw new Error("这笔申请已有待核对操作，请先查询原操作。")
  }
  if (previous) {
    if (previous.fingerprint !== intent.fingerprint) throw new Error(withdrawalContentMismatch)
    return existing
  }
  const next = [...existing, storedWithdrawalIntent(intent)]
  try { sessionStorage.setItem(key, JSON.stringify(next)) }
  catch { throw new Error("浏览器无法保存原操作编号，本次未提交。") }
  return next
}

export function forgetWithdrawalIntent(key: string, intent: WithdrawalIntent): WithdrawalIntent[] {
  const next = readWithdrawalIntents(key).filter(item => !sameWithdrawalIntent(item, intent))
  if (next.length) sessionStorage.setItem(key, JSON.stringify(next))
  else sessionStorage.removeItem(key)
  return next
}

export function sameWithdrawalIntent(left: WithdrawalIntent, right: WithdrawalIntent) {
  return left.store === right.store && left.rowId === right.rowId && left.kind === right.kind && left.requestId === right.requestId
}

export function findWithdrawalReceipt(events: WithdrawalAuditEvent[], intent: WithdrawalIntent, actor: string) {
  return events.find(event => event.actor_id === actor && event.request_id === intent.requestId && (
    event.action === intent.kind || (intent.kind === "paid" && event.action === "mark-unknown"
      && event.to_status === "PAYMENT_UNKNOWN")
  ))
}
