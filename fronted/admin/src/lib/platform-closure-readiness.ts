export type ClosureReadinessStatus = "BLOCKED" | "UNKNOWN"
export type ClosureReadinessDomainStatus = "CLEAR" | "BLOCKED" | "UNKNOWN"
export type ClosureReadinessBlocker = {
  code: string
  resource: string
  reference: string
}
export type ClosureReadinessTotals = {
  walletUnits: number | null
  principalUnits: number | null
  giftUnits: number | null
  compensationUnits: number | null
  pointsHundredths: number | null
  frozenUnits: number | null
  refundFen: number | null
  paidFen: number | null
}
export type ClosureReadinessMetric = { code: string; count: number | null }
export type ClosureReadiness = {
  status: ClosureReadinessStatus
  complete: boolean
  canFinalize: false
  observedAt: string
  snapshotSha256: string
  funds: {
    complete: boolean
    status: ClosureReadinessDomainStatus
    accountCount: number
    completedCount: number
    zeroBalanceCount: number
    totals: ClosureReadinessTotals | null
    blockers: ClosureReadinessBlocker[]
    factsSha256: string
  }
  obligations: {
    complete: boolean
    status: ClosureReadinessDomainStatus
    metrics: ClosureReadinessMetric[]
    blockers: ClosureReadinessBlocker[]
    factsSha256: string
  }
  policy: {
    complete: true
    feeRules: "PENDING"
    finalWriteBarrier: "UNAVAILABLE"
    blockers: ClosureReadinessBlocker[]
  }
  blockingReasons: string[]
}

export type ClosureReadinessScope = {
  storeId: number
  storeSlug: string
  actorId: string
  sessionId: string
  scopeSignature: string
}
export type ClosureReadinessSnapshot = { data: ClosureReadiness }
export type ClosureReadinessState = {
  scopeKey: string
  snapshot: ClosureReadinessSnapshot | null
  loading: boolean
  stale: boolean
  error: Error | null
}

const TOP_LEVEL_KEYS = [
  "status", "complete", "can_finalize", "observed_at", "snapshot_sha256",
  "funds", "obligations", "policy", "blocking_reasons",
] as const
const FUNDS_KEYS = [
  "complete", "status", "account_count", "completed_count", "zero_balance_count",
  "totals", "blockers", "facts_sha256",
] as const
const TOTAL_KEYS = [
  "wallet_units", "principal_units", "gift_units", "compensation_units",
  "points_hundredths", "frozen_units", "refund_fen", "paid_fen",
] as const
const OBLIGATION_KEYS = ["complete", "status", "metrics", "blockers", "facts_sha256"] as const
const POLICY_KEYS = ["complete", "fee_rules", "final_write_barrier", "blockers"] as const
const BLOCKER_KEYS = ["code", "resource", "reference"] as const
const METRIC_KEYS = ["code", "count"] as const
const SHA256 = /^[0-9a-f]{64}$/
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_CODE = /^[A-Z][A-Z0-9_]{0,79}$/
const SAFE_RESOURCE = /^[a-z][a-z0-9_-]{0,63}$/
const SAFE_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const BANK_ACCOUNT = /(?<![A-Za-z0-9])(?:\d[ -]?){12,19}(?![A-Za-z0-9])/
const numberFormatter = new Intl.NumberFormat("zh-CN")

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
function invalid(): never {
  throw new SyntaxError("闭店结清预检响应待核，已停止展示")
}
function digest(value: unknown): value is string {
  return typeof value === "string" && SHA256.test(value)
}
function dateTime(value: unknown): value is string {
  return typeof value === "string" && ISO_DATE_TIME.test(value) && Number.isFinite(Date.parse(value))
}
function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
}
function nullableInteger(value: unknown): value is number | null {
  return value === null || nonNegativeInteger(value)
}
function domainStatus(value: unknown): value is ClosureReadinessDomainStatus {
  return value === "CLEAR" || value === "BLOCKED" || value === "UNKNOWN"
}
function safeReference(value: unknown): value is string {
  return typeof value === "string" && SAFE_REFERENCE.test(value) &&
    !BANK_ACCOUNT.test(value) && !value.includes("://")
}
function parseBlockers(value: unknown): ClosureReadinessBlocker[] {
  if (!Array.isArray(value)) invalid()
  return value.map(item => {
    if (!record(item) || !exactKeys(item, BLOCKER_KEYS) ||
        typeof item.code !== "string" || !SAFE_CODE.test(item.code) ||
        typeof item.resource !== "string" || !SAFE_RESOURCE.test(item.resource) ||
        !safeReference(item.reference)) invalid()
    return { code: item.code, resource: item.resource, reference: item.reference }
  })
}
function parseTotals(value: unknown): ClosureReadinessTotals {
  if (!record(value) || !exactKeys(value, TOTAL_KEYS) || !TOTAL_KEYS.every(key => nullableInteger(value[key]))) invalid()
  return {
    walletUnits: value.wallet_units as number | null,
    principalUnits: value.principal_units as number | null,
    giftUnits: value.gift_units as number | null,
    compensationUnits: value.compensation_units as number | null,
    pointsHundredths: value.points_hundredths as number | null,
    frozenUnits: value.frozen_units as number | null,
    refundFen: value.refund_fen as number | null,
    paidFen: value.paid_fen as number | null,
  }
}
function parseFunds(value: unknown): ClosureReadiness["funds"] {
  if (!record(value) || !exactKeys(value, FUNDS_KEYS) || typeof value.complete !== "boolean" ||
      !domainStatus(value.status) || !nonNegativeInteger(value.account_count) ||
      !nonNegativeInteger(value.completed_count) || !nonNegativeInteger(value.zero_balance_count) ||
      value.completed_count > value.account_count || value.zero_balance_count > value.account_count ||
      !digest(value.facts_sha256) || value.complete !== (value.status !== "UNKNOWN")) invalid()
  if (value.complete && value.totals === null || !value.complete && value.totals !== null) invalid()
  const blockers = parseBlockers(value.blockers)
  if (value.status === "CLEAR" && (blockers.length > 0 || value.account_count === 0)) invalid()
  return {
    complete: value.complete,
    status: value.status,
    accountCount: value.account_count,
    completedCount: value.completed_count,
    zeroBalanceCount: value.zero_balance_count,
    totals: value.totals === null ? null : parseTotals(value.totals),
    blockers,
    factsSha256: value.facts_sha256,
  }
}
function parseObligations(value: unknown): ClosureReadiness["obligations"] {
  if (!record(value) || !exactKeys(value, OBLIGATION_KEYS) || typeof value.complete !== "boolean" ||
      !domainStatus(value.status) || !digest(value.facts_sha256) ||
      value.complete !== (value.status !== "UNKNOWN") || !Array.isArray(value.metrics)) invalid()
  const codes = new Set<string>()
  const metrics = value.metrics.map(item => {
    if (!record(item) || !exactKeys(item, METRIC_KEYS) || typeof item.code !== "string" ||
        !SAFE_CODE.test(item.code) || !nullableInteger(item.count) || codes.has(item.code) ||
        (value.complete && item.count === null)) invalid()
    codes.add(item.code)
    return { code: item.code, count: item.count }
  })
  const blockers = parseBlockers(value.blockers)
  if (value.status === "CLEAR" && blockers.length > 0) invalid()
  return { complete: value.complete, status: value.status, metrics, blockers, factsSha256: value.facts_sha256 }
}
function parsePolicy(value: unknown): ClosureReadiness["policy"] {
  if (!record(value) || !exactKeys(value, POLICY_KEYS) || value.complete !== true ||
      value.fee_rules !== "PENDING" || value.final_write_barrier !== "UNAVAILABLE") invalid()
  const blockers = parseBlockers(value.blockers)
  const codes = new Set(blockers.map(item => item.code))
  if (!blockers.length || !codes.has("FEE_RULES_PENDING") || !codes.has("FINAL_WRITE_BARRIER_UNAVAILABLE")) invalid()
  return { complete: true, feeRules: "PENDING", finalWriteBarrier: "UNAVAILABLE", blockers }
}
function parseReasons(value: unknown): string[] {
  if (!Array.isArray(value)) invalid()
  const reasons = new Set<string>()
  for (const reason of value) {
    if (typeof reason !== "string" || !SAFE_CODE.test(reason) || reasons.has(reason)) invalid()
    reasons.add(reason)
  }
  return [...reasons]
}

export function parseClosureReadiness(value: unknown): ClosureReadiness {
  if (!record(value) || !exactKeys(value, TOP_LEVEL_KEYS) ||
      (value.status !== "BLOCKED" && value.status !== "UNKNOWN") ||
      typeof value.complete !== "boolean" || value.can_finalize !== false ||
      !dateTime(value.observed_at) || !digest(value.snapshot_sha256)) invalid()
  const funds = parseFunds(value.funds)
  const obligations = parseObligations(value.obligations)
  const policy = parsePolicy(value.policy)
  const complete = funds.complete && obligations.complete && policy.complete
  if (value.complete !== complete || value.status !== (complete ? "BLOCKED" : "UNKNOWN")) invalid()
  const blockingReasons = parseReasons(value.blocking_reasons)
  const reasons = new Set(blockingReasons)
  if (!reasons.has("FEE_RULES_PENDING") || !reasons.has("FINAL_WRITE_BARRIER_UNAVAILABLE") ||
      (funds.status !== "CLEAR" && !reasons.has("FUNDS_REVIEW_REQUIRED")) ||
      (obligations.status !== "CLEAR" && !reasons.has("BUSINESS_OBLIGATIONS")) ||
      (!complete && !reasons.has("READ_INCOMPLETE")) || (complete && reasons.has("READ_INCOMPLETE"))) invalid()
  return {
    status: value.status,
    complete,
    canFinalize: false,
    observedAt: value.observed_at,
    snapshotSha256: value.snapshot_sha256,
    funds,
    obligations,
    policy,
    blockingReasons,
  }
}

export function closureReadinessPath(storeId: number) {
  if (!Number.isSafeInteger(storeId) || storeId <= 0) throw new Error("门店 ID 无效，无法读取闭店预检")
  return `store-management/${storeId}/closure/settlement-readiness` as const
}

function cancellationError() {
  const error = new Error("闭店预检读取已取消")
  error.name = "AbortError"
  return error
}

/** Abort settles this wait; the injected request may still finish and is deliberately ignored. */
export function requestClosureReadiness(storeId: number, io: {
  signal: AbortSignal
  request: (path: ReturnType<typeof closureReadinessPath>, signal: AbortSignal) => Promise<unknown> | unknown
}): Promise<ClosureReadiness> {
  const path = closureReadinessPath(storeId)
  if (io.signal.aborted) return Promise.reject(cancellationError())
  return new Promise((resolve, reject) => {
    let settled = false
    const cleanup = () => io.signal.removeEventListener("abort", onAbort)
    const finish = (callback: () => void) => {
      if (settled) return
      settled = true
      cleanup()
      callback()
    }
    const onAbort = () => finish(() => reject(cancellationError()))
    io.signal.addEventListener("abort", onAbort, { once: true })
    let result: Promise<unknown> | unknown
    try { result = io.request(path, io.signal) }
    catch (cause) { finish(() => reject(cause)); return }
    Promise.resolve(result).then(
      value => finish(() => {
        try { resolve(parseClosureReadiness(value)) } catch (cause) { reject(cause) }
      }),
      cause => finish(() => reject(cause)),
    )
  })
}

export function closureReadinessScopeKey(scope: ClosureReadinessScope) {
  return JSON.stringify([
    "closure-readiness-v1", scope.storeId, scope.storeSlug, scope.actorId,
    scope.sessionId, scope.scopeSignature,
  ])
}
export function emptyClosureReadinessState(scopeKey: string): ClosureReadinessState {
  return { scopeKey, snapshot: null, loading: true, stale: false, error: null }
}
export function beginClosureReadinessLoad(state: ClosureReadinessState, scopeKey: string): ClosureReadinessState {
  if (state.scopeKey !== scopeKey) return emptyClosureReadinessState(scopeKey)
  return { ...state, loading: true, error: null }
}
export function completeClosureReadinessLoad(state: ClosureReadinessState, scopeKey: string, data: ClosureReadiness): ClosureReadinessState {
  if (state.scopeKey !== scopeKey) return state
  return { scopeKey, snapshot: { data }, loading: false, stale: false, error: null }
}
export function failClosureReadinessLoad(state: ClosureReadinessState, scopeKey: string, cause: unknown): ClosureReadinessState {
  if (state.scopeKey !== scopeKey) return state
  const status = (cause as { status?: number } | null)?.status
  const aborted = (cause as { name?: string } | null)?.name === "AbortError"
  const message = status === 403
    ? "无权读取本店结清预检；旧快照已清除，当前不可最终停用"
    : aborted
      ? "预检读取已取消，当前不可最终停用"
      : cause instanceof SyntaxError
        ? "预检响应无法核验，当前不可最终停用"
        : "预检读取失败，当前不可最终停用；请稍后重试"
  return {
    scopeKey,
    snapshot: status === 403 ? null : state.snapshot,
    loading: false,
    stale: status === 403 ? false : state.snapshot !== null,
    error: new Error(message),
  }
}
export function closureReadinessMode(state: ClosureReadinessState): "loading" | "error" | "ready" | "empty" {
  if (!state.snapshot) return state.error ? "error" : "loading"
  if (state.snapshot.data.funds.accountCount === 0 && state.snapshot.data.obligations.metrics.length === 0) return "empty"
  return "ready"
}
export function closureReadinessReasonLabel(code: string) {
  const labels: Record<string, string> = {
    FUNDS_REVIEW_REQUIRED: "老板资金仍需复核或返还",
    BUSINESS_OBLIGATIONS: "业务义务仍需处理或复核",
    FEE_RULES_PENDING: "闭店费用规则仍待确认",
    FINAL_WRITE_BARRIER_UNAVAILABLE: "最终写入屏障尚未接通",
    READ_INCOMPLETE: "部分预检事实读取不完整",
  }
  return labels[code] || "其他阻断"
}
export function formatClosureReadinessValue(value: number | null) {
  return value === null || !Number.isFinite(value) ? "—" : numberFormatter.format(value)
}
