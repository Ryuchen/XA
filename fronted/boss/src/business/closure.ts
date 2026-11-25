import { formatDateTime } from "@xa/shared"
import { storeGuaranteeScope, validStoreGuaranteeIdentity } from "./guarantee"

export const STORE_CLOSURE_PATH = "/stores/closure/"

export type StoreClosureStatus =
  | "NOT_STARTED"
  | "NOTICE_ACTIVE"
  | "SETTLEMENT_PENDING"
  | "DISABLED"
  | "UNKNOWN"

export type StoreClosureClockStatus =
  | "VERIFIED"
  | "EVIDENCE_REVIEW"
  | "UNAVAILABLE"
  | "REVIEW"
  | "NOT_REQUIRED"

export type StoreClosureAnnouncement = {
  status: StoreClosureStatus
  announcementVisible: boolean
  clockStatus: StoreClosureClockStatus | null
  announcement: string | null
  startedAt: string | null
  earliestSettlementAt: string | null
  noticeDays: number | null
}

export type StoreClosureErrorKind =
  | "EMPTY"
  | "INVALID"
  | "IDENTITY"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "UNAVAILABLE"

export type StoreClosureError = {
  kind: StoreClosureErrorKind
  message: string
}

export type StoreClosureState = {
  scope: string
  snapshot: { scope: string; data: StoreClosureAnnouncement } | null
  loading: boolean
  stale: boolean
  error: StoreClosureError | null
}

export class StoreClosurePayloadError extends Error {
  kind: "EMPTY" | "INVALID" | "IDENTITY"

  constructor(kind: "EMPTY" | "INVALID" | "IDENTITY", message: string) {
    super(message)
    this.name = "StoreClosurePayloadError"
    this.kind = kind
  }
}

const statuses: StoreClosureStatus[] = [
  "NOT_STARTED",
  "NOTICE_ACTIVE",
  "SETTLEMENT_PENDING",
  "DISABLED",
  "UNKNOWN",
]
const notStartedFields = ["status", "announcement_visible"]
const evidenceReviewFields = ["status", "announcement_visible", "clock_status"]
const disabledFields = ["status", "announcement_visible", "clock_status", "announcement"]
const noticeFields = [
  "status",
  "announcement_visible",
  "clock_status",
  "announcement",
  "started_at",
  "earliest_settlement_at",
  "notice_days",
]
const activeText = "本店正在进行闭店公示。既有订单、售后与权益查询仍按原入口办理。"
const pendingText = "本店闭店公示期已届满，待平台完成后续结清核对；本状态不代表余额已返还。"
const unknownText = "本店闭店公示状态暂时待核，请以平台后续公告为准。"
const disabledText = "本店已停用，不再接受新增业务。既有订单、售后与余额返还情况需另行核对；停用不代表已完成退款或结清。"
const isoWithZone = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function invalid(message = "闭店公告响应格式异常，状态待核"): never {
  throw new StoreClosurePayloadError("INVALID", message)
}

function exactKeys(payload: Record<string, unknown>, fields: string[]): boolean {
  const actual = Object.keys(payload).sort()
  const expected = [...fields].sort()
  return actual.length === expected.length && expected.every((key, index) => key === actual[index])
}

function validStatus(value: unknown): value is StoreClosureStatus {
  return typeof value === "string" && statuses.includes(value as StoreClosureStatus)
}

function validTime(value: unknown): value is string {
  return typeof value === "string" && isoWithZone.test(value) && Number.isFinite(Date.parse(value))
}

function validNoticeDays(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 30
}

function emptyAnnouncement(
  status: StoreClosureStatus,
  visible: boolean,
  clockStatus: StoreClosureClockStatus | null,
): StoreClosureAnnouncement {
  return {
    status,
    announcementVisible: visible,
    clockStatus,
    announcement: null,
    startedAt: null,
    earliestSettlementAt: null,
    noticeDays: null,
  }
}

export function parseStoreClosure(payload: unknown): StoreClosureAnnouncement {
  if (payload === null || payload === undefined ||
      (object(payload) && Object.keys(payload).length === 0)) {
    throw new StoreClosurePayloadError(
      "EMPTY",
      "闭店公告暂未提供可核对信息，状态待核。",
    )
  }
  if (!object(payload) || !validStatus(payload.status)) invalid()
  const status = payload.status
  const visible = payload.announcement_visible
  if (typeof visible !== "boolean") invalid()
  if (status === "NOT_STARTED") {
    if (!exactKeys(payload, notStartedFields) || visible !== false) {
      invalid("未公示响应包含缺失或非公开字段，闭店状态待核")
    }
    return emptyAnnouncement(status, false, null)
  }
  if (status === "DISABLED") {
    if (!exactKeys(payload, disabledFields) || visible !== true ||
        payload.clock_status !== "NOT_REQUIRED" || payload.announcement !== disabledText) {
      invalid("门店停用响应不符合公开契约")
    }
    return { ...emptyAnnouncement(status, true, "NOT_REQUIRED"), announcement: disabledText }
  }
  if (status === "UNKNOWN" && visible === false) {
    if (!exactKeys(payload, evidenceReviewFields) || payload.clock_status !== "EVIDENCE_REVIEW") {
      invalid("闭店证据待核响应不符合公开契约")
    }
    return emptyAnnouncement(status, false, "EVIDENCE_REVIEW")
  }
  if (!exactKeys(payload, noticeFields)) {
    invalid("闭店公告包含缺失、camelCase 或非公开字段，状态待核")
  }
  const clockStatus = payload.clock_status
  const announcement = payload.announcement
  const startedAt = payload.started_at
  const earliestSettlementAt = payload.earliest_settlement_at
  const noticeDays = payload.notice_days
  if (visible !== true || !validTime(startedAt) || !validTime(earliestSettlementAt) ||
      !validNoticeDays(noticeDays) || Date.parse(earliestSettlementAt) <= Date.parse(startedAt) ||
      typeof announcement !== "string") {
    invalid()
  }
  if (status === "UNKNOWN") {
    if (!["UNAVAILABLE", "REVIEW"].includes(String(clockStatus)) || announcement !== unknownText) {
      invalid("闭店未知状态不得解释为未公示或已结清")
    }
  } else if (clockStatus !== "VERIFIED" ||
      (status === "NOTICE_ACTIVE" && announcement !== activeText) ||
      (status === "SETTLEMENT_PENDING" && announcement !== pendingText)) {
    invalid()
  }
  return {
    status,
    announcementVisible: true,
    clockStatus: clockStatus as StoreClosureClockStatus,
    announcement,
    startedAt,
    earliestSettlementAt,
    noticeDays,
  }
}

export function formatStoreClosureTime(value: string | null): string {
  return validTime(value) ? formatDateTime(value) : "待核"
}

export function storeClosurePresentation(data: StoreClosureAnnouncement): {
  label: string
  description: string
} {
  if (data.status === "NOT_STARTED") return {
    label: "未公示",
    description: "当前未发布闭店公示。",
  }
  if (data.status === "NOTICE_ACTIVE") return {
    label: "闭店公示中",
    description: data.announcement || activeText,
  }
  if (data.status === "SETTLEMENT_PENDING") return {
    label: "待结清",
    description: "待结清，尚未完成余额返还或最终停用。" + (data.announcement || pendingText),
  }
  if (data.status === "DISABLED") return {
    label: "门店已停用",
    description: data.announcement || disabledText,
  }
  return {
    label: "状态待核",
    description: (data.announcement ? data.announcement + " " : "") +
      "闭店公示证据或时钟状态待核，不能按未公示处理。",
  }
}

function numericCode(cause: unknown): number {
  const error = cause as { status?: unknown; code?: unknown }
  return [error?.status, error?.code]
    .find((value): value is number => typeof value === "number" && value >= 400) ?? 0
}

export function storeClosureError(cause: unknown): StoreClosureError {
  if (cause instanceof StoreClosurePayloadError) return { kind: cause.kind, message: cause.message }
  const code = numericCode(cause)
  if (code === 401) return {
    kind: "UNAUTHORIZED",
    message: "访问身份待核（401），闭店公告暂不可用；请刷新或重新进入门店。",
  }
  if (code === 403) return {
    kind: "FORBIDDEN",
    message: "当前入口未能读取闭店公告（403），请核对门店域名或联系门店。",
  }
  if (code === 404) return {
    kind: "NOT_FOUND",
    message: "当前门店不存在或闭店公告不可用，请核对门店入口。",
  }
  return {
    kind: "UNAVAILABLE",
    message: code >= 500 || code === 0
      ? "闭店公告暂时无法读取，请稍后刷新；当前状态待核。"
      : cause instanceof Error && cause.message
        ? cause.message
        : "闭店公告读取失败，当前状态待核。",
  }
}

export function emptyStoreClosureState(scope: string): StoreClosureState {
  return { scope, snapshot: null, loading: true, stale: false, error: null }
}

export function beginStoreClosureLoad(previous: StoreClosureState, scope: string): StoreClosureState {
  const sameScope = previous.scope === scope && previous.snapshot?.scope === scope
  return {
    scope,
    snapshot: sameScope ? previous.snapshot : null,
    loading: true,
    stale: sameScope ? previous.stale : false,
    error: null,
  }
}

export function failStoreClosureLoad(
  previous: StoreClosureState,
  scope: string,
  cause: unknown,
): StoreClosureState {
  if (previous.scope !== scope) return previous
  const snapshot = previous.snapshot?.scope === scope ? previous.snapshot : null
  return {
    scope,
    snapshot,
    loading: false,
    stale: snapshot !== null,
    error: storeClosureError(cause),
  }
}

export function completeStoreClosureLoad(
  previous: StoreClosureState,
  scope: string,
  payload: unknown,
): StoreClosureState {
  if (previous.scope !== scope) return previous
  try {
    return {
      scope,
      snapshot: { scope, data: parseStoreClosure(payload) },
      loading: false,
      stale: false,
      error: null,
    }
  } catch (cause) {
    return failStoreClosureLoad(previous, scope, cause)
  }
}

export function storeClosureScope(
  store: { id: number; slug: string },
  sessionRevision: number,
): string {
  return storeGuaranteeScope(store, sessionRevision)
}

export async function requestPublicStoreClosure(
  get: (
    path: string,
    query: undefined,
    options: { public: true; signal: AbortSignal },
  ) => Promise<unknown>,
  {
    store,
    resolvedSlug,
    signal,
  }: {
    store: { id: number; slug: string }
    resolvedSlug: string
    signal: AbortSignal
  },
): Promise<unknown> {
  if (!validStoreGuaranteeIdentity(store, resolvedSlug)) {
    throw new StoreClosurePayloadError(
      "IDENTITY",
      "门店身份与当前域名不一致，未读取闭店公告。",
    )
  }
  return get(STORE_CLOSURE_PATH, undefined, { public: true, signal })
}
