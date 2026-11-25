import { formatRmbFen, validStoreSlug } from "@xa/shared"

export const STORE_GUARANTEE_PATH = "/stores/guarantee/"

export type StoreGuaranteeStatus =
  | "PENDING_REVIEW"
  | "NOT_COVERED"
  | "PARTIAL"
  | "COVERED"

export type StoreGuarantee = {
  status: StoreGuaranteeStatus
  label: string
  currency: "CNY"
  amountDueFen: number | null
  netReceivedFen: number | null
  amountRemainingFen: number | null
  recordBasis: "OFFLINE_EVIDENCE"
}

export type StoreGuaranteeErrorKind =
  | "EMPTY"
  | "INVALID"
  | "IDENTITY"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "UNAVAILABLE"

export type StoreGuaranteeError = {
  kind: StoreGuaranteeErrorKind
  message: string
}

export type StoreGuaranteeSnapshot = {
  scope: string
  data: StoreGuarantee
}

export type StoreGuaranteeState = {
  scope: string
  snapshot: StoreGuaranteeSnapshot | null
  loading: boolean
  stale: boolean
  error: StoreGuaranteeError | null
}

export class StoreGuaranteePayloadError extends Error {
  kind: "EMPTY" | "INVALID" | "IDENTITY"

  constructor(kind: "EMPTY" | "INVALID" | "IDENTITY", message: string) {
    super(message)
    this.name = "StoreGuaranteePayloadError"
    this.kind = kind
  }
}

const publicFields = [
  "status",
  "label",
  "currency",
  "amount_due_fen",
  "net_received_fen",
  "amount_remaining_fen",
  "record_basis",
] as const

const statusLabels: Record<StoreGuaranteeStatus, string[]> = {
  PENDING_REVIEW: ["保障信息待核"],
  NOT_COVERED: ["未形成保证金保障", "无保证金缴纳要求"],
  PARTIAL: ["保证金部分登记"],
  COVERED: ["保证金已按合同登记足额"],
}

function object(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function validStatus(value: unknown): value is StoreGuaranteeStatus {
  return typeof value === "string" && Object.hasOwn(statusLabels, value)
}

function amount(value: unknown): value is number | null {
  return value === null || (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0
  )
}

function invalid(message = "门店保障响应格式异常，状态与金额均待核"): never {
  throw new StoreGuaranteePayloadError("INVALID", message)
}

export function parseStoreGuarantee(payload: unknown): StoreGuarantee {
  if (payload === null || payload === undefined ||
      (object(payload) && Object.keys(payload).length === 0)) {
    throw new StoreGuaranteePayloadError(
      "EMPTY",
      "门店保障暂未配置或暂无可核对信息，金额未知。",
    )
  }
  if (!object(payload)) invalid()
  const keys = Object.keys(payload).sort()
  if (keys.length !== publicFields.length ||
      ![...publicFields].sort().every((key, index) => key === keys[index])) {
    invalid("门店保障响应包含缺失或非公开字段，状态与金额均待核")
  }
  const status = payload.status
  if (!validStatus(status) || payload.currency !== "CNY" ||
      payload.record_basis !== "OFFLINE_EVIDENCE" ||
      typeof payload.label !== "string" ||
      !statusLabels[status].includes(payload.label)) {
    invalid()
  }
  const due = payload.amount_due_fen
  const net = payload.net_received_fen
  const remaining = payload.amount_remaining_fen
  if (!amount(due) || !amount(net) || !amount(remaining)) invalid()
  if (status === "PENDING_REVIEW") {
    if (due !== null || net !== null || remaining !== null) invalid()
  } else {
    if ((due !== null && net !== null && net > due) ||
        (due !== null && remaining !== null && remaining > due)) {
      invalid("门店保障净额关系异常，状态与金额均待核")
    }
    if ((status === "NOT_COVERED" && net !== null && net !== 0) ||
        (status === "PARTIAL" && net !== null && net <= 0) ||
        (status === "PARTIAL" && due !== null && net !== null && net === due) ||
        (status === "COVERED" && (net === 0 || due === 0)) ||
        (status === "COVERED" && due !== null && net !== null && net !== due)) {
      invalid()
    }
    if (due !== null && net !== null && remaining !== null) {
      if (remaining !== due - net) invalid("门店保障净额关系异常，状态与金额均待核")
      if (status === "PARTIAL" && !(net > 0 && net < due)) invalid()
      if (status === "COVERED" && !(due > 0 && net === due && remaining === 0)) invalid()
    }
    if (payload.label === "无保证金缴纳要求" && !(due === 0 && net === 0 && remaining === 0)) {
      invalid("无缴纳要求的金额依据不完整，保障信息待核")
    }
  }
  return {
    status,
    label: payload.label,
    currency: "CNY",
    amountDueFen: due,
    netReceivedFen: net,
    amountRemainingFen: remaining,
    recordBasis: "OFFLINE_EVIDENCE",
  }
}

export function formatStoreGuaranteeAmount(value: number | null): string {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? `¥ ${formatRmbFen(value)}`
    : "待核"
}

export function storeGuaranteePresentation(data: StoreGuarantee): {
  label: string
  description: string
  amountsUnknown: boolean
} {
  const amountsUnknown = data.amountDueFen === null ||
    data.netReceivedFen === null || data.amountRemainingFen === null
  if (data.status === "PENDING_REVIEW" || amountsUnknown) {
    return {
      label: "保障信息待核",
      description: data.status === "PENDING_REVIEW"
        ? "门店尚未提供完整可核对的保障公示，或合同与凭证仍待核；金额按未知处理。"
        : "响应未提供完整金额，当前保障情况与未知金额均需核对，不按零计算。",
      amountsUnknown: true,
    }
  }
  if (data.status === "NOT_COVERED") {
    return {
      label: data.label,
      description: data.amountDueFen === 0
        ? "当前登记合同无保证金缴纳要求，不延伸表示其他消费或履约保障。"
        : "当前凭证登记净额为零，尚未形成保证金保障。",
      amountsUnknown: false,
    }
  }
  return {
    label: data.label,
    description: data.status === "PARTIAL"
      ? "线下凭证已登记部分保证金，仍需按合同与原始凭证核对。"
      : "线下凭证登记净额已达到当前合同金额，不代表支付渠道已经确认。",
    amountsUnknown: false,
  }
}

function numericCode(cause: unknown): number {
  const error = cause as { status?: unknown; code?: unknown }
  const values = [error?.status, error?.code]
  return values.find((value): value is number => typeof value === "number" && value >= 400) ?? 0
}

export function storeGuaranteeError(cause: unknown): StoreGuaranteeError {
  if (cause instanceof StoreGuaranteePayloadError) {
    return { kind: cause.kind, message: cause.message }
  }
  const code = numericCode(cause)
  if (code === 401) return {
    kind: "UNAUTHORIZED",
    message: "访问身份待核（401），保障信息暂不可用；请刷新或重新进入门店。",
  }
  if (code === 403) return {
    kind: "FORBIDDEN",
    message: "当前入口未能读取门店保障（403），请核对门店域名或联系门店。",
  }
  if (code === 404) return {
    kind: "NOT_FOUND",
    message: "当前门店不存在或保障公示不可用，请核对门店入口。",
  }
  return {
    kind: "UNAVAILABLE",
    message: code >= 500 || code === 0
      ? "门店保障暂时无法读取，请稍后刷新；状态与金额尚未确认。"
      : cause instanceof Error && cause.message
        ? cause.message
        : "门店保障读取失败，状态与金额尚未确认。",
  }
}

export function emptyStoreGuaranteeState(scope: string): StoreGuaranteeState {
  return { scope, snapshot: null, loading: true, stale: false, error: null }
}

export function beginStoreGuaranteeLoad(
  previous: StoreGuaranteeState,
  scope: string,
): StoreGuaranteeState {
  const sameScope = previous.scope === scope && previous.snapshot?.scope === scope
  return {
    scope,
    snapshot: sameScope ? previous.snapshot : null,
    loading: true,
    stale: sameScope ? previous.stale : false,
    error: null,
  }
}

export function failStoreGuaranteeLoad(
  previous: StoreGuaranteeState,
  scope: string,
  cause: unknown,
): StoreGuaranteeState {
  if (previous.scope !== scope) return previous
  const snapshot = previous.snapshot?.scope === scope ? previous.snapshot : null
  return {
    scope,
    snapshot,
    loading: false,
    stale: snapshot !== null,
    error: storeGuaranteeError(cause),
  }
}

export function completeStoreGuaranteeLoad(
  previous: StoreGuaranteeState,
  scope: string,
  payload: unknown,
): StoreGuaranteeState {
  if (previous.scope !== scope) return previous
  try {
    return {
      scope,
      snapshot: { scope, data: parseStoreGuarantee(payload) },
      loading: false,
      stale: false,
      error: null,
    }
  } catch (cause) {
    return failStoreGuaranteeLoad(previous, scope, cause)
  }
}

export function validStoreGuaranteeIdentity(
  store: { id: number; slug: string },
  resolvedSlug: string,
): boolean {
  return Number.isSafeInteger(store.id) && store.id > 0 &&
    validStoreSlug(store.slug) && store.slug === resolvedSlug
}

export function storeGuaranteeScope(
  store: { id: number; slug: string },
  sessionRevision: number,
): string {
  return `${store.slug}:${store.id}:${sessionRevision}`
}

export async function requestPublicStoreGuarantee(
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
    throw new StoreGuaranteePayloadError(
      "IDENTITY",
      "门店身份与当前域名不一致，未读取保障信息。",
    )
  }
  return get(STORE_GUARANTEE_PATH, undefined, { public: true, signal })
}
