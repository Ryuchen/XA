import type { PlatformProfile, PlatformStoreStatus } from "./platform-session.ts"

export const GUARANTEE_POLICY = "SA02_NOTICE_30D_V1"
export const CLOSURE_REASON_CATEGORIES = ["OPERATOR_REQUEST", "LICENSE_TERMINATION", "PLATFORM_DIRECTED", "OTHER"] as const

export type PlatformActor = { id: string; username: string; nickname: string }
export type GuaranteeContract = {
  id: string
  store_slug: string
  source_license_contract_id: string | null
  amount_due_fen: number
  version: number
  amount_received_fen: number
  amount_refunded_fen: number
  net_received_fen: number
  amount_remaining_fen: number
  status: "NO_REQUIREMENT" | "UNPAID" | "COVERED" | "PARTIAL"
  updated_at: string
  updated_by: PlatformActor
}
export type GuaranteeEvent = {
  id: string
  business_key: string
  client_request_id: string
  contract_version: number
  direction: "RECEIPT" | "REFUND"
  amount_fen: number
  occurred_at: string
  evidence_sha256: string
  evidence_ref: string
  compensates_event_id: string | null
  created_by: PlatformActor
  created_at: string
  channel_status: "NOT_REQUESTED"
}
export type PublicGuaranteeStatus = {
  status: "PENDING_REVIEW" | "NOT_COVERED" | "PARTIAL" | "COVERED"
  label: string
  currency: "CNY"
  amount_due_fen: number | null
  net_received_fen: number | null
  amount_remaining_fen: number | null
  record_basis: "OFFLINE_EVIDENCE"
}
export type GuaranteeSummary = { contract: GuaranteeContract | null; events: GuaranteeEvent[]; public_status: PublicGuaranteeStatus }
export type ClosureNotice = {
  id: string
  store_slug: string
  status: "NOTICE_ACTIVE" | "SETTLEMENT_PENDING" | "EVIDENCE_REVIEW" | "CLOCK_REVIEW"
  reason_category: (typeof CLOSURE_REASON_CATEGORIES)[number]
  reason: string
  applicant: PlatformActor
  started_at: string
  notice_policy_version: "SA02_NOTICE_30D_V1"
  notice_days: number
  earliest_settlement_at: string
  client_request_id: string
  clock_source: string
  clock_identity: string
  created_at: string
}
export type ClosureExpiryEvent = {
  id: string
  kind: "NOTICE_EXPIRED"
  reason: string
  actor: PlatformActor
  occurred_at: string
  client_request_id: string
  clock_source: string
  clock_identity: string
  created_at: string
}
export type ClosureSummary = {
  status: "NOT_STARTED" | "NOTICE_ACTIVE" | "SETTLEMENT_PENDING" | "EVIDENCE_REVIEW" | "CLOCK_REVIEW"
  notice: ClosureNotice | null
  expiry_event: ClosureExpiryEvent | null
  new_activity_allowed: boolean
  store_status: PlatformStoreStatus
}
export type ClosureStartResult = {
  closure_id: string
  started_at: string
  notice_policy_version: "SA02_NOTICE_30D_V1"
  notice_days: number
  earliest_settlement_at: string
  outcome: "CREATED" | "REPLAY" | "EXISTING"
  replayed: boolean
  already_started: boolean
}
export type ClosureExpiryResult = ClosureExpiryEvent & {
  status: "SETTLEMENT_PENDING"
  store_status: PlatformStoreStatus
  outcome: "CREATED" | "REPLAY" | "EXISTING"
  replayed: boolean
  already_confirmed: boolean
}

export type GuaranteeContractPayload = {
  amount_due_fen: number
  expected_version: number
  reason: string
  source_license_contract_id: string | null
}
export type GuaranteeEventPayload = {
  business_key: string
  client_request_id: string
  direction: "RECEIPT" | "REFUND"
  amount_fen: number
  occurred_at: string
  evidence_sha256: string
  evidence_ref: string
  compensates_event_id: string | null
}
export type ClosureStartPayload = { reason_category: (typeof CLOSURE_REASON_CATEGORIES)[number]; reason: string; client_request_id: string }
export type ClosureExpiryPayload = { reason: string; client_request_id: string }
export type PlatformWriteContext = { storeId: number; storeSlug: string; actorId: string; sessionId: string }
export type PlatformWriteIntent = {
  version: 1
  storeId: number
  storeSlug: string
  actorId: string
  sessionId: string
  client_request_id: string
} & (
  | { operation: "contract"; payload: GuaranteeContractPayload }
  | { operation: "guarantee-event"; payload: GuaranteeEventPayload }
  | { operation: "closure-start"; payload: ClosureStartPayload }
  | { operation: "closure-expiry"; payload: ClosureExpiryPayload }
)
export type PlatformWriteResult = GuaranteeContract | (GuaranteeEvent & { replayed: boolean }) | ClosureStartResult | ClosureExpiryResult

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256 = /^[0-9a-f]{64}$/
const REQUEST_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/
const EVIDENCE_REF = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/
const BANK_ACCOUNT = /(?<![A-Za-z0-9])(?:\d[ -]?){12,19}(?![A-Za-z0-9])/
const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const CONTRACT_KEYS = ["id", "store_slug", "source_license_contract_id", "amount_due_fen", "version", "amount_received_fen", "amount_refunded_fen", "net_received_fen", "amount_remaining_fen", "status", "updated_at", "updated_by"] as const
const EVENT_KEYS = ["id", "business_key", "client_request_id", "contract_version", "direction", "amount_fen", "occurred_at", "evidence_sha256", "evidence_ref", "compensates_event_id", "created_by", "created_at", "channel_status"] as const
const NOTICE_KEYS = ["id", "store_slug", "status", "reason_category", "reason", "applicant", "started_at", "notice_policy_version", "notice_days", "earliest_settlement_at", "client_request_id", "clock_source", "clock_identity", "created_at"] as const
const EXPIRY_KEYS = ["id", "kind", "reason", "actor", "occurred_at", "client_request_id", "clock_source", "clock_identity", "created_at"] as const

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
function text(value: unknown): value is string {
  return typeof value === "string"
}
function dateTime(value: unknown): value is string {
  return typeof value === "string" && ISO_DATE_TIME.test(value) && Number.isFinite(Date.parse(value))
}
function uuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value)
}
function requestId(value: unknown, maximum = 64): value is string {
  return typeof value === "string" && value.trim() === value && value.length <= maximum && REQUEST_KEY.test(value)
}
function invalid(message: string): never { throw new Error(message) }
function actor(value: unknown): PlatformActor {
  if (!record(value) || !exactKeys(value, ["id", "username", "nickname"]) || !text(value.id) || !value.id ||
      !text(value.username) || !text(value.nickname)) invalid("平台操作人审计字段待核")
  return { id: value.id, username: value.username, nickname: value.nickname }
}
function evidenceRef(value: unknown): value is string {
  return typeof value === "string" && EVIDENCE_REF.test(value) && !BANK_ACCOUNT.test(value)
}
function storeStatus(value: unknown): value is PlatformStoreStatus {
  return value === "PROVISIONING" || value === "CONFIGURING" || value === "ACTIVE" || value === "DISABLED"
}

export function platformGuaranteeClosureAccess(profile: PlatformProfile, storeSlug: string) {
  const grants = profile.grants.filter(grant => grant.scope_slug === "*" || grant.scope_slug === storeSlug)
  const may = (action: "guarantee:view" | "guarantee:configure" | "guarantee:record" | "closure:view" | "closure:manage") => grants.some(grant => {
    if (grant.role === "SUPER_ADMIN") return true
    if (grant.role !== "FINANCE") return false
    return action === "guarantee:view" || action === "guarantee:record" || action === "closure:view"
  })
  return {
    guaranteeView: may("guarantee:view"),
    guaranteeConfigure: may("guarantee:configure"),
    guaranteeRecord: may("guarantee:record"),
    closureView: may("closure:view"),
    closureManage: may("closure:manage"),
    scopeSignature: JSON.stringify([...grants].sort((a, b) => `${a.role}:${a.scope_slug}`.localeCompare(`${b.role}:${b.scope_slug}`))),
  }
}

function parseContract(value: unknown, expectedStore?: string): GuaranteeContract {
  if (!record(value) || !exactKeys(value, CONTRACT_KEYS) || !uuid(value.id) || !STORE_SLUG.test(String(value.store_slug)) ||
      (expectedStore !== undefined && value.store_slug !== expectedStore) ||
      !(value.source_license_contract_id === null || uuid(value.source_license_contract_id)) ||
      !integer(value.amount_due_fen) || !integer(value.version, true) || !integer(value.amount_received_fen) ||
      !integer(value.amount_refunded_fen) || !integer(value.net_received_fen) ||
      (typeof value.amount_remaining_fen !== "number" || !Number.isSafeInteger(value.amount_remaining_fen)) ||
      !dateTime(value.updated_at) || !["NO_REQUIREMENT", "UNPAID", "COVERED", "PARTIAL"].includes(String(value.status))) {
    invalid("保证金合同金额、版本或门店身份待核")
  }
  if (value.amount_received_fen - value.amount_refunded_fen !== value.net_received_fen ||
      value.amount_due_fen - value.net_received_fen !== value.amount_remaining_fen ||
      value.net_received_fen < 0 || value.net_received_fen > value.amount_due_fen) invalid("保证金净额或剩余金额不守恒")
  const expectedStatus = value.amount_due_fen === 0 && value.net_received_fen === 0 ? "NO_REQUIREMENT"
    : value.net_received_fen === 0 ? "UNPAID" : value.net_received_fen === value.amount_due_fen ? "COVERED" : "PARTIAL"
  if (value.status !== expectedStatus) invalid("保证金合同状态与金额不一致")
  return {
    id: value.id, store_slug: value.store_slug as string, source_license_contract_id: value.source_license_contract_id,
    amount_due_fen: value.amount_due_fen, version: value.version, amount_received_fen: value.amount_received_fen,
    amount_refunded_fen: value.amount_refunded_fen, net_received_fen: value.net_received_fen,
    amount_remaining_fen: value.amount_remaining_fen, status: value.status as GuaranteeContract["status"], updated_at: value.updated_at, updated_by: actor(value.updated_by),
  }
}
function parseEvent(value: unknown): GuaranteeEvent {
  if (!record(value) || !exactKeys(value, EVENT_KEYS) || !uuid(value.id) || !requestId(value.business_key, 80) ||
      !requestId(value.client_request_id) || !integer(value.contract_version, true) ||
      !(value.direction === "RECEIPT" || value.direction === "REFUND") || !integer(value.amount_fen, true) ||
      !dateTime(value.occurred_at) || typeof value.evidence_sha256 !== "string" || !SHA256.test(value.evidence_sha256) ||
      !evidenceRef(value.evidence_ref) || !(value.compensates_event_id === null || uuid(value.compensates_event_id)) ||
      !dateTime(value.created_at) || value.channel_status !== "NOT_REQUESTED") invalid("保证金收退事件或凭证证据异常，待核")
  return {
    id: value.id, business_key: value.business_key, client_request_id: value.client_request_id,
    contract_version: value.contract_version, direction: value.direction, amount_fen: value.amount_fen,
    occurred_at: value.occurred_at, evidence_sha256: value.evidence_sha256, evidence_ref: value.evidence_ref,
    compensates_event_id: value.compensates_event_id, created_by: actor(value.created_by), created_at: value.created_at,
    channel_status: "NOT_REQUESTED",
  }
}
function parsePublicStatus(value: unknown): PublicGuaranteeStatus {
  const keys = ["status", "label", "currency", "amount_due_fen", "net_received_fen", "amount_remaining_fen", "record_basis"]
  if (!record(value) || !exactKeys(value, keys) || !["PENDING_REVIEW", "NOT_COVERED", "PARTIAL", "COVERED"].includes(String(value.status)) ||
      !text(value.label) || value.currency !== "CNY" || value.record_basis !== "OFFLINE_EVIDENCE") invalid("保证金公开投影结构待核")
  if (value.status === "PENDING_REVIEW") {
    if (value.amount_due_fen !== null || value.net_received_fen !== null || value.amount_remaining_fen !== null) invalid("待核保证金不得推造金额")
  } else {
    if (!integer(value.amount_due_fen) || !integer(value.net_received_fen) || !integer(value.amount_remaining_fen) ||
        value.amount_due_fen - value.net_received_fen !== value.amount_remaining_fen) invalid("保证金公开金额待核")
    if ((value.status === "NOT_COVERED" && value.net_received_fen !== 0) ||
        (value.status === "PARTIAL" && (value.net_received_fen <= 0 || value.net_received_fen >= value.amount_due_fen)) ||
        (value.status === "COVERED" && (value.net_received_fen === 0 || value.net_received_fen !== value.amount_due_fen))) invalid("保证金公开状态与金额不一致")
  }
  return {
    status: value.status as PublicGuaranteeStatus["status"], label: value.label, currency: "CNY", amount_due_fen: value.amount_due_fen,
    net_received_fen: value.net_received_fen, amount_remaining_fen: value.amount_remaining_fen, record_basis: "OFFLINE_EVIDENCE",
  }
}
export function parseGuaranteeSummary(value: unknown, expected: { storeSlug: string }): GuaranteeSummary {
  if (!record(value) || !exactKeys(value, ["contract", "events", "public_status"]) || !Array.isArray(value.events)) invalid("保证金摘要结构待核")
  const contract = value.contract === null ? null : parseContract(value.contract, expected.storeSlug)
  if (contract === null && value.events.length) invalid("无合同门店不得出现收退事件")
  const ids = new Set<string>(), businessKeys = new Set<string>(), requestIds = new Set<string>()
  const events = value.events.map(parseEvent)
  let received = 0, refunded = 0
  events.forEach((event, index) => {
    if (ids.has(event.id) || businessKeys.has(event.business_key) || requestIds.has(event.client_request_id) ||
        (contract && event.contract_version > contract.version) || (index > 0 && Date.parse(events[index - 1].created_at) > Date.parse(event.created_at))) {
      invalid("保证金事件重复、顺序或合同版本异常")
    }
    ids.add(event.id); businessKeys.add(event.business_key); requestIds.add(event.client_request_id)
    if (event.direction === "RECEIPT") received += event.amount_fen
    else refunded += event.amount_fen
    if (event.compensates_event_id) {
      const original = events.find(item => item.id === event.compensates_event_id)
      if (!original || original.direction === event.direction || original.amount_fen !== event.amount_fen ||
          Date.parse(original.created_at) > Date.parse(event.created_at)) invalid("补偿事件与原事件不匹配")
    }
  })
  if (contract && (contract.amount_received_fen !== received || contract.amount_refunded_fen !== refunded)) invalid("合同汇总与原始收退事件不一致")
  return { contract, events, public_status: parsePublicStatus(value.public_status) }
}

function parseNotice(value: unknown, expectedStore: string): ClosureNotice {
  if (!record(value) || !exactKeys(value, NOTICE_KEYS) || !uuid(value.id) || value.store_slug !== expectedStore ||
      !["NOTICE_ACTIVE", "SETTLEMENT_PENDING", "EVIDENCE_REVIEW", "CLOCK_REVIEW"].includes(String(value.status)) ||
      !CLOSURE_REASON_CATEGORIES.includes(value.reason_category as (typeof CLOSURE_REASON_CATEGORIES)[number]) ||
      !text(value.reason) || !value.reason.trim() || value.reason.length > 500 || !dateTime(value.started_at) ||
      value.notice_policy_version !== GUARANTEE_POLICY || !integer(value.notice_days, true) || value.notice_days < 30 ||
      !dateTime(value.earliest_settlement_at) || !requestId(value.client_request_id) || !text(value.clock_source) || !value.clock_source ||
      !text(value.clock_identity) || !value.clock_identity || !dateTime(value.created_at)) invalid("闭店公示字段、政策版本或门店身份待核")
  if (Date.parse(value.earliest_settlement_at) !== Date.parse(value.started_at) + value.notice_days * 86400000) invalid("闭店公示时长与到期时点不一致")
  return {
    id: value.id, store_slug: value.store_slug, status: value.status as ClosureNotice["status"],
    reason_category: value.reason_category as ClosureNotice["reason_category"], reason: value.reason,
    applicant: actor(value.applicant), started_at: value.started_at, notice_policy_version: "SA02_NOTICE_30D_V1",
    notice_days: value.notice_days, earliest_settlement_at: value.earliest_settlement_at,
    client_request_id: value.client_request_id, clock_source: value.clock_source, clock_identity: value.clock_identity,
    created_at: value.created_at,
  }
}
function parseExpiry(value: unknown): ClosureExpiryEvent {
  if (!record(value) || !exactKeys(value, EXPIRY_KEYS) || !uuid(value.id) || value.kind !== "NOTICE_EXPIRED" ||
      !text(value.reason) || !value.reason.trim() || value.reason.length > 500 || !dateTime(value.occurred_at) ||
      !requestId(value.client_request_id) || !text(value.clock_source) || !value.clock_source ||
      !text(value.clock_identity) || !value.clock_identity || !dateTime(value.created_at)) invalid("闭店到期事件审计字段待核")
  return {
    id: value.id, kind: "NOTICE_EXPIRED", reason: value.reason, actor: actor(value.actor), occurred_at: value.occurred_at,
    client_request_id: value.client_request_id, clock_source: value.clock_source, clock_identity: value.clock_identity, created_at: value.created_at,
  }
}
export function parseClosureSummary(value: unknown, expected: { storeSlug: string }): ClosureSummary {
  if (!record(value) || !exactKeys(value, ["status", "notice", "expiry_event", "new_activity_allowed", "store_status"]) ||
      !["NOT_STARTED", "NOTICE_ACTIVE", "SETTLEMENT_PENDING", "EVIDENCE_REVIEW", "CLOCK_REVIEW"].includes(String(value.status)) ||
      typeof value.new_activity_allowed !== "boolean" || !storeStatus(value.store_status)) invalid("闭店状态响应待核")
  const notice = value.notice === null ? null : parseNotice(value.notice, expected.storeSlug)
  const expiry = value.expiry_event === null ? null : parseExpiry(value.expiry_event)
  if (notice === null) {
    if (value.status !== "NOT_STARTED" || expiry !== null || value.new_activity_allowed !== true) invalid("未发起闭店状态不一致")
  } else if (notice.status !== value.status || value.new_activity_allowed !== false ||
             (value.status === "NOTICE_ACTIVE" && expiry !== null) ||
             (expiry && Date.parse(expiry.occurred_at) < Date.parse(notice.earliest_settlement_at))) invalid("闭店公示与到期证据状态不一致")
  return { status: value.status as ClosureSummary["status"], notice, expiry_event: expiry, new_activity_allowed: value.new_activity_allowed, store_status: value.store_status }
}

function baseIntent(context: PlatformWriteContext, id: string) {
  if (!integer(context.storeId, true) || !STORE_SLUG.test(context.storeSlug) || !context.actorId || !context.sessionId ||
      context.sessionId.length > 128 || !requestId(id)) invalid("平台写入门店、账号或请求编号无效")
  return { version: 1 as const, storeId: context.storeId, storeSlug: context.storeSlug, actorId: context.actorId, sessionId: context.sessionId, client_request_id: id }
}
export function createContractIntent(context: PlatformWriteContext, form: { amountDueFen: number; expectedVersion: number; reason: string; sourceLicenseContractId: string | null }, id: string): PlatformWriteIntent {
  if (!integer(form.amountDueFen) || !integer(form.expectedVersion) || !text(form.reason) || !form.reason.trim() || form.reason.trim().length > 200 ||
      !(form.sourceLicenseContractId === null || uuid(form.sourceLicenseContractId))) invalid("合同金额、版本或变更原因无效")
  return { ...baseIntent(context, id), operation: "contract", payload: { amount_due_fen: form.amountDueFen, expected_version: form.expectedVersion, reason: form.reason.trim(), source_license_contract_id: form.sourceLicenseContractId } }
}
export function createGuaranteeEventIntent(context: PlatformWriteContext, form: { businessKey: string; direction: "RECEIPT" | "REFUND"; amountFen: number; occurredAt: string; evidenceSha256: string; evidenceRef: string; compensatesEventId: string | null }, id: string): PlatformWriteIntent {
  const occurred = new Date(form.occurredAt)
  if (!requestId(form.businessKey, 80) || !(form.direction === "RECEIPT" || form.direction === "REFUND") || !integer(form.amountFen, true) ||
      !dateTime(form.occurredAt) || !Number.isFinite(occurred.getTime()) || typeof form.evidenceSha256 !== "string" || !SHA256.test(form.evidenceSha256.trim().toLowerCase()) ||
      !evidenceRef(form.evidenceRef.trim()) || !(form.compensatesEventId === null || uuid(form.compensatesEventId))) invalid("收退金额、发生时间或凭证摘要与安全引用无效")
  return { ...baseIntent(context, id), operation: "guarantee-event", payload: {
    business_key: form.businessKey, client_request_id: id, direction: form.direction, amount_fen: form.amountFen,
    occurred_at: occurred.toISOString(), evidence_sha256: form.evidenceSha256.trim().toLowerCase(), evidence_ref: form.evidenceRef.trim(), compensates_event_id: form.compensatesEventId,
  } }
}
export function createClosureStartIntent(context: PlatformWriteContext, form: { reasonCategory: (typeof CLOSURE_REASON_CATEGORIES)[number]; reason: string }, id: string): PlatformWriteIntent {
  if (!CLOSURE_REASON_CATEGORIES.includes(form.reasonCategory) || !text(form.reason) || !form.reason.trim() || form.reason.trim().length > 500) invalid("闭店原因类别或原因无效")
  return { ...baseIntent(context, id), operation: "closure-start", payload: { reason_category: form.reasonCategory, reason: form.reason.trim(), client_request_id: id } }
}
export function createClosureExpiryIntent(context: PlatformWriteContext, reason: string, id: string): PlatformWriteIntent {
  if (!text(reason) || !reason.trim() || reason.trim().length > 500) invalid("到期确认原因无效")
  return { ...baseIntent(context, id), operation: "closure-expiry", payload: { reason: reason.trim(), client_request_id: id } }
}

export function parsePlatformWriteIntent(raw: string | null, context: PlatformWriteContext): PlatformWriteIntent | null {
  if (raw === null) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (!record(value) || !exactKeys(value, ["version", "storeId", "storeSlug", "actorId", "sessionId", "client_request_id", "operation", "payload"]) ||
        value.version !== 1 || value.storeId !== context.storeId || value.storeSlug !== context.storeSlug || value.actorId !== context.actorId ||
        value.sessionId !== context.sessionId || !record(value.payload)) invalid("invalid")
    const id = value.client_request_id
    if (typeof id !== "string") invalid("invalid")
    let parsed: PlatformWriteIntent
    if (value.operation === "contract") {
      const p = value.payload
      parsed = createContractIntent(context, { amountDueFen: p.amount_due_fen as number, expectedVersion: p.expected_version as number, reason: p.reason as string, sourceLicenseContractId: p.source_license_contract_id as string | null }, id)
    } else if (value.operation === "guarantee-event") {
      const p = value.payload
      parsed = createGuaranteeEventIntent(context, { businessKey: p.business_key as string, direction: p.direction as "RECEIPT" | "REFUND", amountFen: p.amount_fen as number, occurredAt: p.occurred_at as string, evidenceSha256: p.evidence_sha256 as string, evidenceRef: p.evidence_ref as string, compensatesEventId: p.compensates_event_id as string | null }, id)
    } else if (value.operation === "closure-start") {
      const p = value.payload
      parsed = createClosureStartIntent(context, { reasonCategory: p.reason_category as ClosureStartPayload["reason_category"], reason: p.reason as string }, id)
    } else if (value.operation === "closure-expiry") {
      parsed = createClosureExpiryIntent(context, value.payload.reason as string, id)
    } else invalid("invalid")
    if (JSON.stringify(parsed.payload) !== JSON.stringify(value.payload) || parsed.client_request_id !== id) invalid("invalid")
    return parsed
  } catch { throw new Error("保证金或闭店待核对请求无法解析，已阻止新写入；请保留浏览器记录并联系平台管理员。") }
}

export function parseContractResult(value: unknown, intent: Extract<PlatformWriteIntent, { operation: "contract" }>) {
  const contract = parseContract(value, intent.storeSlug)
  const expected = intent.payload.expected_version + 1
  if (contract.version !== expected || contract.amount_due_fen !== intent.payload.amount_due_fen ||
      contract.source_license_contract_id !== intent.payload.source_license_contract_id) invalid("合同回执与原请求版本、金额或来源不一致")
  return contract
}
export function parseGuaranteeEventResult(value: unknown, intent: Extract<PlatformWriteIntent, { operation: "guarantee-event" }>) {
  if (!record(value) || !exactKeys(value, [...EVENT_KEYS, "replayed"]) || typeof value.replayed !== "boolean") invalid("收退事件回执结构待核")
  const event = parseEvent(Object.fromEntries(EVENT_KEYS.map(key => [key, value[key]])))
  const p = intent.payload
  if (event.client_request_id !== intent.client_request_id || event.business_key !== p.business_key || event.direction !== p.direction ||
      event.amount_fen !== p.amount_fen || Date.parse(event.occurred_at) !== Date.parse(p.occurred_at) || event.evidence_sha256 !== p.evidence_sha256 ||
      event.evidence_ref !== p.evidence_ref || event.compensates_event_id !== p.compensates_event_id) invalid("收退事件回执与原请求不一致")
  return { ...event, replayed: value.replayed }
}
export function parseClosureStartResult(value: unknown): ClosureStartResult {
  const keys = ["closure_id", "started_at", "notice_policy_version", "notice_days", "earliest_settlement_at", "outcome", "replayed", "already_started"]
  if (!record(value) || !exactKeys(value, keys) || !uuid(value.closure_id) || !dateTime(value.started_at) ||
      value.notice_policy_version !== GUARANTEE_POLICY || value.notice_days !== 30 || !dateTime(value.earliest_settlement_at) ||
      !["CREATED", "REPLAY", "EXISTING"].includes(String(value.outcome)) || typeof value.replayed !== "boolean" || typeof value.already_started !== "boolean" ||
      Date.parse(value.earliest_settlement_at) !== Date.parse(value.started_at) + 30 * 86400000) invalid("闭店发起回执结构或公示时长待核")
  const outcome = value.outcome as ClosureStartResult["outcome"]
  if (value.replayed !== (outcome === "REPLAY") || value.already_started !== (outcome === "EXISTING")) invalid("闭店发起结果标记不一致")
  return { closure_id: value.closure_id, started_at: value.started_at, notice_policy_version: "SA02_NOTICE_30D_V1", notice_days: 30, earliest_settlement_at: value.earliest_settlement_at, outcome, replayed: value.replayed, already_started: value.already_started }
}
export function parseClosureExpiryResult(value: unknown, intent?: Extract<PlatformWriteIntent, { operation: "closure-expiry" }>): ClosureExpiryResult {
  if (!record(value) || !exactKeys(value, [...EXPIRY_KEYS, "status", "store_status", "outcome", "replayed", "already_confirmed"]) ||
      value.status !== "SETTLEMENT_PENDING" || !storeStatus(value.store_status) || !["CREATED", "REPLAY", "EXISTING"].includes(String(value.outcome)) ||
      typeof value.replayed !== "boolean" || typeof value.already_confirmed !== "boolean") invalid("闭店到期回执结构或门店状态待核")
  const event = parseExpiry(Object.fromEntries(EXPIRY_KEYS.map(key => [key, value[key]])))
  const outcome = value.outcome as ClosureExpiryResult["outcome"]
  if (value.replayed !== (outcome === "REPLAY") || value.already_confirmed !== (outcome === "EXISTING")) invalid("闭店到期结果标记不一致")
  if (intent && outcome !== "EXISTING" && (event.client_request_id !== intent.client_request_id || event.reason !== intent.payload.reason)) invalid("闭店到期回执与原请求不一致")
  return { ...event, status: "SETTLEMENT_PENDING", store_status: value.store_status, outcome, replayed: value.replayed, already_confirmed: value.already_confirmed }
}

export async function recoverPlatformWrite(intent: PlatformWriteIntent, io: {
  guarantee: () => Promise<GuaranteeSummary>
  closure: () => Promise<ClosureSummary>
}): Promise<{ status: "NOT_FOUND" | "ALREADY_EXISTS" | "NEEDS_AUDIT" | "FOUND" | "CONFLICT"; message?: string }> {
  if (intent.operation === "contract") {
    const summary = await io.guarantee(), contract = summary.contract, p = intent.payload
    if (!contract || contract.version === p.expected_version) return { status: "NOT_FOUND" }
    if (contract.version === p.expected_version + 1 && contract.amount_due_fen === p.amount_due_fen &&
        contract.source_license_contract_id === p.source_license_contract_id) {
      return { status: "NEEDS_AUDIT", message: "检测到目标合同版本，但合同端点不返回请求编号或变更原因；必须核对平台审计，不能据此认定原请求已恢复。" }
    }
    return { status: "CONFLICT", message: "合同当前版本或可见字段与原请求不一致，已停止重放。" }
  }
  if (intent.operation === "guarantee-event") {
    const summary = await io.guarantee(), matches = summary.events.filter(event => event.client_request_id === intent.client_request_id)
    if (!matches.length) return { status: "NOT_FOUND" }
    if (matches.length !== 1) return { status: "CONFLICT", message: "原请求号对应多个收退事件。" }
    try { parseGuaranteeEventResult({ ...matches[0], replayed: true }, intent) }
    catch { return { status: "CONFLICT", message: "原请求号对应的收退内容不同。" } }
    return { status: "FOUND" }
  }
  const summary = await io.closure()
  if (intent.operation === "closure-start") {
    if (!summary.notice) return { status: "NOT_FOUND" }
    if (summary.notice.client_request_id !== intent.client_request_id) return { status: "ALREADY_EXISTS", message: "门店已有其他闭店公示，本次不会重置倒计时。" }
    if (summary.notice.reason !== intent.payload.reason || summary.notice.reason_category !== intent.payload.reason_category ||
        summary.notice.notice_policy_version !== GUARANTEE_POLICY || summary.notice.notice_days !== 30) return { status: "CONFLICT", message: "原请求号对应的闭店申请内容不同。" }
    return { status: "FOUND" }
  }
  if (!summary.notice) return { status: "CONFLICT", message: "门店尚未发起闭店公示，不能恢复到点确认。" }
  if (!summary.expiry_event) return { status: "NOT_FOUND" }
  if (summary.expiry_event.client_request_id !== intent.client_request_id) return { status: "ALREADY_EXISTS", message: "公示到期已由其他请求确认，本次不会新增事件。" }
  if (summary.expiry_event.reason !== intent.payload.reason) return { status: "CONFLICT", message: "原请求号对应的到期确认原因不同。" }
  return { status: "FOUND" }
}

export function platformWriteFailure(cause: unknown, recovering = false): { kind: "denied" | "conflict" | "rejected" | "unknown"; clear: boolean } {
  const status = (cause as { status?: number } | null)?.status || 0
  if (status === 409) return { kind: "conflict", clear: true }
  if (status === 400) return { kind: "rejected", clear: true }
  if (status === 403) return { kind: "denied", clear: !recovering }
  if (!recovering && (status === 401 || status === 404)) return { kind: "rejected", clear: true }
  return { kind: "unknown", clear: false }
}
export async function submitPlatformWrite<T extends PlatformWriteResult>(intent: PlatformWriteIntent, io: {
  save: (value: PlatformWriteIntent) => void
  send: (value: PlatformWriteIntent) => Promise<T>
  clear: () => void
}, options: { recovering?: boolean } = {}): Promise<T> {
  io.save(intent)
  try { const result = await io.send(intent); io.clear(); return result }
  catch (cause) {
    if (platformWriteFailure(cause, options.recovering).clear) { try { io.clear() } catch { /* Keep the original record. */ } }
    throw cause
  }
}
export function platformWriteMessage(cause: unknown, recovering = false) {
  const error = cause as { code?: string; message?: string } | null
  const failure = platformWriteFailure(cause, recovering)
  if (error?.code === "GUARANTEE_REVISION_CONFLICT") return "合同版本已变化，本次未修改；请刷新合同后重新确认。"
  if (error?.code === "REQUEST_ID_CONFLICT") return "请求编号已用于其他内容，本次未覆盖原记录；请刷新并核对。"
  if (error?.code === "CLOSURE_NOTICE_ACTIVE") return "30 天公示期尚未届满，本次未确认到期。"
  if (failure.kind === "denied") return `${error?.message || "平台权限已拒绝"}${recovering ? "；原请求仍保留，恢复授权后继续核对。" : ""}`
  if (failure.kind === "unknown") return `${error?.message || "操作结果未知"}；已保存原请求号和完整内容，请先只读恢复，不换号重发。`
  return error?.message || "操作未完成，请核对后重试"
}
export function platformSnapshotMode(state: { hasSnapshot: boolean; loading: boolean; error: string }) {
  if (state.hasSnapshot && state.error) return "stale" as const
  if (!state.hasSnapshot && state.loading) return "loading" as const
  if (!state.hasSnapshot && state.error) return "error" as const
  return "ready" as const
}
