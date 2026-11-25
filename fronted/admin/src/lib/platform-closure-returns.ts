import { sha256 } from "@noble/hashes/sha2.js"
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js"
import { platformGuaranteeClosureAccess, type PlatformWriteContext } from "./platform-guarantee-closure.ts"
import type { PlatformProfile } from "./platform-session.ts"

export const CLOSURE_RETURN_STATUSES = ["READY_TO_PREPARE", "REVIEW_REQUIRED", "FROZEN", "PAID", "RELEASED"] as const
export type ClosureReturnStatus = (typeof CLOSURE_RETURN_STATUSES)[number]
export type NullableUnits = number | null

export type ClosureReturnRow = {
  case_id: string | null
  account_id: number
  business_cycle: number
  account_label: string
  status: ClosureReturnStatus
  wallet_units: NullableUnits
  principal_units: NullableUnits
  gift_units: NullableUnits
  compensation_units: NullableUnits
  points_hundredths: NullableUnits
  frozen_units: NullableUnits
  refund_fen: NullableUnits
  paid_fen: NullableUnits
  version: number
  blocking_reasons: string[]
  updated_at: string
}

/** The SPEC fixes summary.complete, but not the names of count and total fields. */
export type ClosureReturnSummary = { complete: boolean; [key: string]: unknown }
export type ClosureReturnList = {
  list: ClosureReturnRow[]
  total: number
  page: number
  page_size: number
  has_more: boolean
  summary: ClosureReturnSummary
}
export type ClosureReturnEvent = Record<string, unknown> & { client_request_id?: string }
export type ClosureReturnDetail = {
  row: ClosureReturnRow
  events: ClosureReturnEvent[]
  channel_status: "NOT_REQUESTED"
}
export type ClosureReturnWriteResult = {
  row: ClosureReturnRow
  replayed: boolean
  wallet_balance: number
  wallet_frozen_units: number
  channel_status?: "NOT_REQUESTED"
}

export type ClosureReturnContext = PlatformWriteContext & { scopeSignature: string }
type ReturnTarget = Pick<ClosureReturnRow, "case_id" | "account_id" | "business_cycle" | "status" | "refund_fen">
type PrepareTarget = ReturnTarget & Pick<ClosureReturnRow,
  "wallet_units" | "principal_units" | "gift_units" | "compensation_units" | "frozen_units" | "paid_fen" | "blocking_reasons">
export type ClosureReturnPreparePayload = {
  account_id: number
  business_cycle: number
  expected_wallet_units: number
  expected_principal_units: number
  client_request_id: string
}
export type ClosureReturnPaymentPayload = {
  amount_fen: number
  external_reference: string
  proof_sha256: string
  occurred_at: string
  client_request_id: string
}
export type ClosureReturnReleasePayload = { reason: string; client_request_id: string }
export type ClosureReturnIntent = {
  version: 1
  storeId: number
  storeSlug: string
  actorId: string
  sessionId: string
  scopeSignature: string
  caseId: string | null
  accountId: number
  businessCycle: number
  client_request_id: string
} & (
  | { operation: "prepare"; payload: ClosureReturnPreparePayload }
  | { operation: "record-payment"; payload: ClosureReturnPaymentPayload }
  | { operation: "release"; payload: ClosureReturnReleasePayload }
)

export const CLOSURE_RETURN_ROW_KEYS = [
  "case_id", "account_id", "business_cycle", "account_label", "status", "wallet_units",
  "principal_units", "gift_units", "compensation_units", "points_hundredths", "frozen_units",
  "refund_fen", "paid_fen", "version", "blocking_reasons", "updated_at",
] as const
const LIST_KEYS = ["list", "total", "page", "page_size", "has_more", "summary"] as const
const INTENT_KEYS = [
  "version", "storeId", "storeSlug", "actorId", "sessionId", "scopeSignature", "caseId",
  "accountId", "businessCycle", "client_request_id", "operation", "payload",
] as const
const REQUEST_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/
const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const SHA256 = /^[0-9a-f]{64}$/
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const BANK_ACCOUNT = /(?<![A-Za-z0-9])(?:\d[ -]?){12,19}(?![A-Za-z0-9])/
const FORBIDDEN_EVENT_KEYS = new Set([
  "bank_account", "bank_account_number", "full_bank_account", "proof_body", "proof_content",
  "credential", "credentials", "full_external_reference",
])

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value).sort(), expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}
function integer(value: unknown, positive = false): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && (positive ? value > 0 : value >= 0)
}
function nullableInteger(value: unknown): value is number | null {
  return value === null || integer(value)
}
function requestId(value: unknown): value is string {
  return typeof value === "string" && REQUEST_KEY.test(value)
}
function caseId(value: unknown): value is string {
  return requestId(value)
}
function dateTime(value: unknown): value is string {
  return typeof value === "string" && ISO_DATE_TIME.test(value) && Number.isFinite(Date.parse(value))
}
function invalid(message: string): never { throw new Error(message) }
function safeExternalReference(value: unknown): value is string {
  return typeof value === "string" && value.trim() === value && SAFE_EXTERNAL_REFERENCE.test(value) &&
    !BANK_ACCOUNT.test(value) && !value.includes("://")
}
function status(value: unknown): value is ClosureReturnStatus {
  return CLOSURE_RETURN_STATUSES.includes(value as ClosureReturnStatus)
}
export function closureReturnStatusPresentation(value: ClosureReturnStatus) {
  const presentation = {
    READY_TO_PREPARE: { label: "可准备冻结", tone: "primary", readOnly: "" },
    REVIEW_REQUIRED: { label: "待人工复核", tone: "warning", readOnly: "" },
    FROZEN: { label: "已冻结待实付", tone: "warning", readOnly: "" },
    PAID: { label: "已登记实付", tone: "success", readOnly: "只读，不可解除" },
    RELEASED: { label: "历史已解除", tone: "muted", readOnly: "历史已解除，只读" },
  } as const
  return presentation[value]
}

export function parseClosureReturnRow(value: unknown): ClosureReturnRow {
  if (!record(value) || !exactKeys(value, CLOSURE_RETURN_ROW_KEYS) ||
      !(value.case_id === null || caseId(value.case_id)) || !integer(value.account_id, true) ||
      !integer(value.business_cycle, true) || typeof value.account_label !== "string" ||
      !value.account_label.trim() || value.account_label.length > 200 || !status(value.status) ||
      !nullableInteger(value.wallet_units) || !nullableInteger(value.principal_units) ||
      !nullableInteger(value.gift_units) || !nullableInteger(value.compensation_units) ||
      !nullableInteger(value.points_hundredths) || !nullableInteger(value.frozen_units) ||
      !nullableInteger(value.refund_fen) || !nullableInteger(value.paid_fen) ||
      !integer(value.version) || !Array.isArray(value.blocking_reasons) ||
      !value.blocking_reasons.every(reason => typeof reason === "string" && !!reason.trim() && reason.length <= 500) ||
      !dateTime(value.updated_at) ||
      ((value.status === "FROZEN" || value.status === "PAID" || value.status === "RELEASED") && value.case_id === null)) {
    invalid("余额返还账户行、账号周期或金额字段待核")
  }
  return {
    case_id: value.case_id, account_id: value.account_id, business_cycle: value.business_cycle,
    account_label: value.account_label, status: value.status, wallet_units: value.wallet_units,
    principal_units: value.principal_units, gift_units: value.gift_units,
    compensation_units: value.compensation_units, points_hundredths: value.points_hundredths,
    frozen_units: value.frozen_units, refund_fen: value.refund_fen, paid_fen: value.paid_fen,
    version: value.version, blocking_reasons: [...value.blocking_reasons], updated_at: value.updated_at,
  }
}

export function parseClosureReturnList(value: unknown, expected?: { page: number; pageSize: number }): ClosureReturnList {
  if (!record(value) || !exactKeys(value, LIST_KEYS) || !Array.isArray(value.list) ||
      !integer(value.total) || !integer(value.page, true) || !integer(value.page_size, true) ||
      typeof value.has_more !== "boolean" || !record(value.summary) || typeof value.summary.complete !== "boolean" ||
      value.list.length > value.page_size || value.total < value.list.length ||
      (expected && (value.page !== expected.page || value.page_size !== expected.pageSize))) {
    invalid("余额返还分页、汇总或完整性字段待核")
  }
  const list = value.list.map(parseClosureReturnRow)
  const identities = new Set<string>()
  for (const row of list) {
    const identity = `${row.account_id}:${row.business_cycle}`
    if (identities.has(identity)) invalid("余额返还清单出现重复账号周期")
    identities.add(identity)
  }
  return {
    list, total: value.total, page: value.page, page_size: value.page_size,
    has_more: value.has_more, summary: { ...value.summary, complete: value.summary.complete },
  }
}

function parseEvent(value: unknown): ClosureReturnEvent {
  if (!record(value) || (Object.hasOwn(value, "client_request_id") && !requestId(value.client_request_id))) {
    invalid("余额返还事件或请求编号待核")
  }
  for (const key of Object.keys(value)) if (FORBIDDEN_EVENT_KEYS.has(key)) invalid("余额返还事件包含不得公开的完整银行资料或凭证正文")
  if (Object.hasOwn(value, "external_reference") && !safeExternalReference(value.external_reference)) {
    invalid("余额返还事件的外部编号未脱敏")
  }
  if (Object.hasOwn(value, "proof_sha256") &&
      (typeof value.proof_sha256 !== "string" || !SHA256.test(value.proof_sha256))) {
    invalid("余额返还事件凭证摘要待核")
  }
  return { ...value } as ClosureReturnEvent
}

/** Detail uses the same flattened account row as the write receipts, plus events and channel_status. */
export function parseClosureReturnDetail(value: unknown, expected?: { caseId: string; accountId: number; businessCycle: number }): ClosureReturnDetail {
  if (!record(value) || !exactKeys(value, [...CLOSURE_RETURN_ROW_KEYS, "events", "channel_status"]) ||
      !Array.isArray(value.events) || value.channel_status !== "NOT_REQUESTED") {
    invalid("余额返还详情、事件或渠道状态待核")
  }
  const row = parseClosureReturnRow(Object.fromEntries(CLOSURE_RETURN_ROW_KEYS.map(key => [key, value[key]])))
  if (row.case_id === null || (expected && (row.case_id !== expected.caseId ||
      row.account_id !== expected.accountId || row.business_cycle !== expected.businessCycle))) {
    invalid("余额返还详情与原门店账号周期或 case 不一致")
  }
  return { row, events: value.events.map(parseEvent), channel_status: "NOT_REQUESTED" }
}

export function closureReturnsAccess(profile: PlatformProfile, storeSlug: string) {
  const access = platformGuaranteeClosureAccess(profile, storeSlug)
  return { view: access.closureView, manage: access.closureManage, scopeSignature: access.scopeSignature }
}

function baseIntent(context: ClosureReturnContext, target: ReturnTarget, id: string) {
  if (!integer(context.storeId, true) || !STORE_SLUG.test(context.storeSlug) || !context.actorId ||
      !context.sessionId || context.sessionId.length > 128 || !context.scopeSignature ||
      !integer(target.account_id, true) || !integer(target.business_cycle, true) || !requestId(id) ||
      !(target.case_id === null || caseId(target.case_id))) {
    invalid("余额返还门店、账号周期、case 或请求编号无效")
  }
  return {
    version: 1 as const, storeId: context.storeId, storeSlug: context.storeSlug, actorId: context.actorId,
    sessionId: context.sessionId, scopeSignature: context.scopeSignature, caseId: target.case_id,
    accountId: target.account_id, businessCycle: target.business_cycle, client_request_id: id,
  }
}
function purePrincipalReady(row: PrepareTarget) {
  return row.status === "READY_TO_PREPARE" && row.blocking_reasons.length === 0 &&
    integer(row.wallet_units, true) && row.wallet_units === row.principal_units &&
    row.refund_fen === row.principal_units && row.gift_units === 0 && row.compensation_units === 0 &&
    row.frozen_units === 0 && row.paid_fen === 0
}

export function createClosureReturnPrepareIntent(context: ClosureReturnContext, row: PrepareTarget, id: string): ClosureReturnIntent {
  if (!purePrincipalReady(row)) invalid("仅全部余额均为可核验本金且无赠送、赔付或冻结的账户可准备返还")
  return {
    ...baseIntent(context, { ...row, case_id: null }, id), operation: "prepare", payload: {
      account_id: row.account_id, business_cycle: row.business_cycle,
      expected_wallet_units: row.wallet_units!, expected_principal_units: row.principal_units!, client_request_id: id,
    },
  }
}

export function createClosureReturnPaymentIntent(context: ClosureReturnContext, row: ReturnTarget, form: {
  amountFen: number
  externalReference: string
  proofSha256: string
  occurredAt: string
}, id: string): ClosureReturnIntent {
  const reference = form.externalReference.trim(), digest = form.proofSha256.trim().toLowerCase()
  if (row.status !== "FROZEN" || row.case_id === null || !integer(row.refund_fen, true) ||
      !integer(form.amountFen, true) || form.amountFen !== row.refund_fen ||
      !safeExternalReference(reference) || !SHA256.test(digest) || !dateTime(form.occurredAt)) {
    invalid("实付必须等于原可退本金，且外部编号、凭证摘要和实际发生时间无效")
  }
  return {
    ...baseIntent(context, row, id), operation: "record-payment", payload: {
      amount_fen: form.amountFen, external_reference: reference, proof_sha256: digest,
      occurred_at: new Date(form.occurredAt).toISOString(), client_request_id: id,
    },
  }
}

export function createClosureReturnReleaseIntent(context: ClosureReturnContext, row: ReturnTarget, reason: string, id: string): ClosureReturnIntent {
  if (row.status !== "FROZEN" || row.case_id === null || typeof reason !== "string" ||
      !reason.trim() || reason.trim().length > 500) invalid("仅未实付冻结单可凭有效原因解除")
  return {
    ...baseIntent(context, row, id), operation: "release",
    payload: { reason: reason.trim(), client_request_id: id },
  }
}

export function parseClosureReturnIntent(raw: string | null, context: ClosureReturnContext): ClosureReturnIntent | null {
  if (raw === null) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (!record(value) || !exactKeys(value, INTENT_KEYS) || value.version !== 1 ||
        value.storeId !== context.storeId || value.storeSlug !== context.storeSlug ||
        value.actorId !== context.actorId || value.sessionId !== context.sessionId ||
        value.scopeSignature !== context.scopeSignature || !record(value.payload) ||
        typeof value.client_request_id !== "string" || !integer(value.accountId, true) ||
        !integer(value.businessCycle, true) || !(value.caseId === null || caseId(value.caseId))) invalid("invalid")
    const target: ReturnTarget = {
      case_id: value.caseId, account_id: value.accountId, business_cycle: value.businessCycle,
      status: value.operation === "prepare" ? "READY_TO_PREPARE" : "FROZEN",
      refund_fen: value.operation === "prepare" ? value.payload.expected_principal_units as number
        : value.operation === "record-payment" ? value.payload.amount_fen as number : 1,
    }
    let parsed: ClosureReturnIntent
    if (value.operation === "prepare") {
      const p = value.payload
      parsed = createClosureReturnPrepareIntent(context, {
        ...target, case_id: null, wallet_units: p.expected_wallet_units as number,
        principal_units: p.expected_principal_units as number, gift_units: 0, compensation_units: 0,
        frozen_units: 0, paid_fen: 0, blocking_reasons: [],
      }, value.client_request_id)
    } else if (value.operation === "record-payment") {
      const p = value.payload
      parsed = createClosureReturnPaymentIntent(context, target, {
        amountFen: p.amount_fen as number, externalReference: p.external_reference as string,
        proofSha256: p.proof_sha256 as string, occurredAt: p.occurred_at as string,
      }, value.client_request_id)
    } else if (value.operation === "release") {
      parsed = createClosureReturnReleaseIntent(context, target, value.payload.reason as string, value.client_request_id)
    } else invalid("invalid")
    if (JSON.stringify(parsed.payload) !== JSON.stringify(value.payload) || parsed.caseId !== value.caseId ||
        parsed.accountId !== value.accountId || parsed.businessCycle !== value.businessCycle) invalid("invalid")
    return parsed
  } catch { throw new Error("余额返还待核请求无法解析或授权范围已变化，已阻止新写入；请重新读取服务端状态。") }
}

function rowFromReceipt(value: Record<string, unknown>) {
  return parseClosureReturnRow(Object.fromEntries(CLOSURE_RETURN_ROW_KEYS.map(key => [key, value[key]])))
}
function writeReceipt(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!record(value) || !exactKeys(value, keys) || typeof value.replayed !== "boolean" ||
      !integer(value.wallet_balance) || !integer(value.wallet_frozen_units)) invalid("余额返还写入回执或钱包结果待核")
  return value
}
function verifyReceiptIdentity(result: ClosureReturnWriteResult, intent: ClosureReturnIntent) {
  if (result.row.account_id !== intent.accountId || result.row.business_cycle !== intent.businessCycle ||
      (intent.operation !== "prepare" && result.row.case_id !== intent.caseId)) {
    invalid("余额返还回执与原门店账号周期或 case 不一致")
  }
  return result
}
export function parseClosureReturnPrepareResult(value: unknown, intent: Extract<ClosureReturnIntent, { operation: "prepare" }>) {
  const receipt = writeReceipt(value, [...CLOSURE_RETURN_ROW_KEYS, "replayed", "wallet_balance", "wallet_frozen_units"])
  const result: ClosureReturnWriteResult = {
    row: rowFromReceipt(receipt), replayed: receipt.replayed as boolean,
    wallet_balance: receipt.wallet_balance as number, wallet_frozen_units: receipt.wallet_frozen_units as number,
  }
  if (result.row.status !== "FROZEN" || result.row.case_id === null ||
      result.row.principal_units !== intent.payload.expected_principal_units) invalid("准备冻结回执未形成匹配的全额本金冻结单")
  return verifyReceiptIdentity(result, intent)
}
export function parseClosureReturnPaymentResult(value: unknown, intent: Extract<ClosureReturnIntent, { operation: "record-payment" }>) {
  const receipt = writeReceipt(value, [...CLOSURE_RETURN_ROW_KEYS, "replayed", "wallet_balance", "wallet_frozen_units", "channel_status"])
  if (receipt.channel_status !== "NOT_REQUESTED") invalid("实付登记不得表示银行或支付渠道成功")
  const result: ClosureReturnWriteResult = {
    row: rowFromReceipt(receipt), replayed: receipt.replayed as boolean,
    wallet_balance: receipt.wallet_balance as number, wallet_frozen_units: receipt.wallet_frozen_units as number,
    channel_status: "NOT_REQUESTED",
  }
  if (result.row.status !== "PAID" || result.row.paid_fen !== intent.payload.amount_fen) invalid("实付回执金额或状态与原请求不一致")
  return verifyReceiptIdentity(result, intent)
}
export function parseClosureReturnReleaseResult(value: unknown, intent: Extract<ClosureReturnIntent, { operation: "release" }>) {
  const receipt = writeReceipt(value, [...CLOSURE_RETURN_ROW_KEYS, "replayed", "wallet_balance", "wallet_frozen_units"])
  const result: ClosureReturnWriteResult = {
    row: rowFromReceipt(receipt), replayed: receipt.replayed as boolean,
    wallet_balance: receipt.wallet_balance as number, wallet_frozen_units: receipt.wallet_frozen_units as number,
  }
  if ((result.row.status !== "READY_TO_PREPARE" && result.row.status !== "RELEASED") ||
      result.wallet_frozen_units !== 0) invalid("解除回执不是最新可准备行或历史已解除行，或仍保留冻结金额")
  return verifyReceiptIdentity(result, intent)
}

export function closureReturnAllowedActions(row: ClosureReturnRow, canManage: boolean, blocked = false): Array<"prepare" | "record-payment" | "release"> {
  if (!canManage || blocked) return []
  if (purePrincipalReady(row)) return ["prepare"]
  if (row.status === "FROZEN" && row.case_id !== null && integer(row.refund_fen, true)) return ["record-payment", "release"]
  return []
}

export type ClosureReturnRecovery = {
  status: "NOT_FOUND" | "FOUND" | "CONFLICT" | "ALREADY_EXISTS" | "NEEDS_AUDIT"
  message?: string
}
function compareEvent(intent: ClosureReturnIntent, event: ClosureReturnEvent): "MATCH" | "CONFLICT" | "UNKNOWN" {
  const expectedKind = intent.operation === "prepare" ? "PREPARED"
    : intent.operation === "record-payment" ? "PAYMENT_RECORDED" : "RELEASED"
  if (event.kind !== expectedKind) return "CONFLICT"
  if (!record(event.detail)) return "UNKNOWN"
  if (event.platform_actor_id !== intent.actorId) return "CONFLICT"
  const source = event.detail
  if (intent.operation === "prepare") {
    const keys = ["account_id", "business_cycle", "principal_units", "refund_fen", "wallet_before"]
    if (!keys.every(key => Object.hasOwn(source, key))) return "UNKNOWN"
    return source.account_id === intent.payload.account_id &&
      source.business_cycle === intent.payload.business_cycle &&
      source.principal_units === intent.payload.expected_principal_units &&
      source.refund_fen === intent.payload.expected_principal_units &&
      source.wallet_before === intent.payload.expected_wallet_units ? "MATCH" : "CONFLICT"
  }
  if (intent.operation === "record-payment") {
    const keys = ["amount_fen", "external_reference_sha256", "proof_sha256", "occurred_at"]
    if (!keys.every(key => Object.hasOwn(source, key))) return "UNKNOWN"
    if (!dateTime(source.occurred_at)) return "UNKNOWN"
    const normalized = intent.payload.external_reference.normalize("NFKC").trim().toLowerCase()
    const referenceDigest = bytesToHex(sha256(utf8ToBytes(normalized)))
    return source.amount_fen === intent.payload.amount_fen &&
      source.external_reference_sha256 === referenceDigest &&
      source.proof_sha256 === intent.payload.proof_sha256 &&
      Date.parse(source.occurred_at) === Date.parse(intent.payload.occurred_at) ? "MATCH" : "CONFLICT"
  }
  if (!Object.hasOwn(source, "reason")) return "UNKNOWN"
  return source.reason === intent.payload.reason ? "MATCH" : "CONFLICT"
}
function unchangedByAnotherRequest(intent: ClosureReturnIntent, row: ClosureReturnRow): ClosureReturnRecovery | null {
  if (intent.operation === "prepare" && row.status === "FROZEN") return { status: "ALREADY_EXISTS", message: "该账号周期已由其他请求冻结，本次不重复建立返还单。" }
  if (intent.operation === "record-payment" && row.status === "PAID") return { status: "ALREADY_EXISTS", message: "该 case 已登记其他实付请求，本次不重复登记。" }
  if (intent.operation === "record-payment" && row.status === "RELEASED") return { status: "CONFLICT", message: "该历史 case 已解除，禁止再登记实付。" }
  if (intent.operation === "release" && row.status === "PAID") return { status: "CONFLICT", message: "该 case 已实付，禁止解除。" }
  if (intent.operation === "release" && row.status !== "FROZEN") return { status: "ALREADY_EXISTS", message: "该冻结单已解除或状态已变化，本次不重复解除。" }
  return null
}
export async function recoverClosureReturnWrite(intent: ClosureReturnIntent, io: {
  findCase: (intent: Extract<ClosureReturnIntent, { operation: "prepare" }>) => Promise<ClosureReturnDetail | null>
  readCase: (caseId: string) => Promise<ClosureReturnDetail>
}): Promise<ClosureReturnRecovery> {
  let detail: ClosureReturnDetail | null
  if (intent.operation === "prepare") detail = await io.findCase(intent)
  else {
    if (!intent.caseId) return { status: "CONFLICT", message: "原请求缺少 case，已停止恢复。" }
    detail = await io.readCase(intent.caseId)
  }
  if (!detail) return { status: "NOT_FOUND" }
  if (detail.row.account_id !== intent.accountId || detail.row.business_cycle !== intent.businessCycle ||
      (intent.caseId && detail.row.case_id !== intent.caseId)) {
    return { status: "CONFLICT", message: "只读详情不属于原账号周期或 case，已停止重放。" }
  }
  const matches = detail.events.filter(event => event.client_request_id === intent.client_request_id)
  if (matches.length > 1) return { status: "CONFLICT", message: "原请求号对应多个事件，必须人工核对。" }
  if (!matches.length) return unchangedByAnotherRequest(intent, detail.row) || { status: "NOT_FOUND" }
  const comparison = compareEvent(intent, matches[0])
  if (comparison === "CONFLICT") return { status: "CONFLICT", message: "原请求号对应的事件内容不同，本次未覆盖或重发。" }
  if (comparison === "UNKNOWN") return { status: "NEEDS_AUDIT", message: "详情未提供可逐字段复核的原请求内容，不能仅凭请求号认定恢复成功。" }
  return { status: "FOUND", message: "已按原请求号及完整内容找到不可变事件，未重复发送写入。" }
}

export function closureReturnWriteFailure(cause: unknown): { kind: "denied" | "conflict" | "rejected" | "unknown"; clear: boolean } {
  const status = (cause as { status?: number } | null)?.status || 0
  if (status === 403) return { kind: "denied", clear: true }
  if (status === 409) return { kind: "conflict", clear: true }
  if (status === 400 || status === 401 || status === 404) return { kind: "rejected", clear: true }
  return { kind: "unknown", clear: false }
}
export async function submitClosureReturnWrite(intent: ClosureReturnIntent, io: {
  save: (intent: ClosureReturnIntent) => void
  send: (intent: ClosureReturnIntent) => Promise<ClosureReturnWriteResult>
  clear: (expected: ClosureReturnIntent) => void
}): Promise<ClosureReturnWriteResult> {
  io.save(intent)
  try {
    const result = await io.send(intent)
    io.clear(intent)
    return result
  } catch (cause) {
    if (closureReturnWriteFailure(cause).clear) {
      try { io.clear(intent) } catch { /* Preserve a concurrent newer record. */ }
    }
    throw cause
  }
}
export function closureReturnWriteMessage(cause: unknown, recovering = false) {
  const error = cause as { code?: string; message?: string } | null
  if (error?.code === "REQUEST_ID_CONFLICT") return "请求编号已用于其他内容，原记录未被覆盖；请刷新并核对冲突。"
  const failure = closureReturnWriteFailure(cause)
  if (failure.kind === "denied") return `${error?.message || "平台权限已拒绝"}；旧快照和写入能力已清除。`
  if (failure.kind === "conflict") return `${error?.message || "账户、钱包或 case 状态已变化"}；已保留服务端状态并刷新，本次不重复写入。`
  if (failure.kind === "unknown") return `${error?.message || "操作结果未知"}；原请求号和完整内容已保存，请先只读恢复原 case，不换号重发。`
  return error?.message || (recovering ? "原号重试未完成，请重新只读核对" : "操作未完成，请核对后重试")
}

export type ClosureReturnStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">
export function closureReturnStorageKey(context: ClosureReturnContext) {
  return `xa:fronted:platform:closure-returns:${context.actorId}:${context.sessionId}:${context.storeId}:${encodeURIComponent(context.scopeSignature)}`
}
export function readStoredClosureReturnIntent(storage: ClosureReturnStorage, key: string, context: ClosureReturnContext) {
  return parseClosureReturnIntent(storage.getItem(key), context)
}
export function storeClosureReturnIntent(storage: ClosureReturnStorage, key: string, intent: ClosureReturnIntent) {
  try { storage.setItem(key, JSON.stringify(intent)) }
  catch { throw Object.assign(new Error("浏览器无法保存原请求，本次未发送；请允许会话存储后重试"), { localCode: "STORAGE_UNAVAILABLE" }) }
}
export function clearStoredClosureReturnIntent(storage: ClosureReturnStorage, key: string, expected: ClosureReturnIntent) {
  const raw = storage.getItem(key)
  if (raw !== null && raw !== JSON.stringify(expected)) return false
  storage.removeItem(key)
  return true
}
export function sameClosureReturnScope(intent: ClosureReturnIntent, context: ClosureReturnContext) {
  return intent.storeId === context.storeId && intent.storeSlug === context.storeSlug &&
    intent.actorId === context.actorId && intent.sessionId === context.sessionId &&
    intent.scopeSignature === context.scopeSignature
}
export function sameClosureReturnCase(intent: ClosureReturnIntent, row: Pick<ClosureReturnRow, "case_id" | "account_id" | "business_cycle">) {
  return intent.accountId === row.account_id && intent.businessCycle === row.business_cycle &&
    (intent.caseId === null || intent.caseId === row.case_id)
}

export function closureReturnsViewMode(state: {
  denied: boolean
  hasSnapshot: boolean
  loading: boolean
  error: string
  rowCount: number
}) {
  if (state.denied) return "denied" as const
  if (state.hasSnapshot && state.error) return "stale" as const
  if (!state.hasSnapshot && state.loading) return "loading" as const
  if (!state.hasSnapshot && state.error) return "error" as const
  if (state.hasSnapshot && state.rowCount === 0) return "empty" as const
  return "ready" as const
}
export function closureReturnUnits(value: number | null) {
  return value === null ? "待核" : new Intl.NumberFormat("zh-CN").format(value)
}
export function closureReturnPoints(value: number | null) {
  return value === null ? "待核" : `${new Intl.NumberFormat("zh-CN").format(value)}（百分位）`
}
