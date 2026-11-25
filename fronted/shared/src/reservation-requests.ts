import { randomUuid } from "./ids.ts"
import { getStoreSlug, storeStorageKey } from "./store.ts"

export type ReservationRequestRole = "boss" | "provider"
export type ReservationRequestAction =
  | "create"
  | "confirm"
  | "reject"
  | "cancel"
  | "convert"
  | "replace_provider"

export type ReservationRequestIntent = {
  version: 1
  role: ReservationRequestRole
  accountId: string
  store: string
  action: ReservationRequestAction
  reservationId: number | null
  seatNumber: 1 | 2 | null
  targetProviderAccountId: number | null
  path: string
  payload: Record<string, unknown>
  decisionKey: string
  review?: boolean
  blocked?: boolean
}

export type ReservationRequestStorage = Pick<
  Storage,
  "getItem" | "setItem" | "removeItem"
>

export type ReservationRequestSnapshot = {
  intent: ReservationRequestIntent | null
  error: string
}

export type ReservationRequestFailureKind =
  | "expired"
  | "review"
  | "unknown"
  | "conflict"
  | "rejected"

const actions: ReservationRequestAction[] = [
  "create",
  "confirm",
  "reject",
  "cancel",
  "convert",
  "replace_provider",
]
const invalidCredential = "原预约请求凭证无法读取，请先核对预约记录或联系客服。"

export class ReservationRequestError extends Error {
  status: number
  code: number | string
  errorCode: string

  constructor(message: string, status: number, errorCode: string) {
    super(message)
    this.name = "ReservationRequestError"
    this.status = status
    this.code = status
    this.errorCode = errorCode
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`
  if (record(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`
  }
  return JSON.stringify(value) ?? "null"
}

function positiveId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
}

export function reservationRequestPath(
  action: ReservationRequestAction,
  reservationId: number | null,
): string {
  if (action === "create") return "/orders/reservations/"
  if (!positiveId(reservationId)) throw new Error("缺少有效预约编号")
  const suffix = action === "replace_provider" ? "replace-provider" : action
  return `/orders/reservations/${reservationId}/${suffix}/`
}

export function reservationRequestStorageKey(
  role: ReservationRequestRole,
  accountId: string | number | undefined,
  reservationId: number | "create",
  store = getStoreSlug(),
): string {
  return storeStorageKey(
    `xa:${role}:web:reservation-request:${accountId ?? "signed-out"}:${reservationId}`,
    store,
  )
}

export function createReservationRequest({
  role,
  accountId,
  action,
  reservationId = null,
  seatNumber = null,
  targetProviderAccountId = null,
  payload,
  decisionKey,
  store = getStoreSlug(),
}: {
  role: ReservationRequestRole
  accountId: string | number
  action: ReservationRequestAction
  reservationId?: number | null
  seatNumber?: 1 | 2 | null
  targetProviderAccountId?: number | null
  payload: Record<string, unknown>
  decisionKey?: string
  store?: string
}): ReservationRequestIntent {
  const owner = String(accountId)
  if (!owner || !store || !actions.includes(action)) {
    throw new ReservationRequestError(
      "预约请求身份或动作无效，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  if (!record(payload) || "decision_key" in payload || "client_request_id" in payload) {
    throw new ReservationRequestError(
      "预约请求内容无效，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  if (action === "create" ? reservationId !== null : !positiveId(reservationId)) {
    throw new ReservationRequestError(
      "预约编号与动作不匹配，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  if (seatNumber !== null && ![1, 2].includes(seatNumber)) {
    throw new ReservationRequestError(
      "预约席位无效，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  if (targetProviderAccountId !== null && !positiveId(targetProviderAccountId)) {
    throw new ReservationRequestError(
      "目标陪玩无效，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  if (
    seatNumber !== null &&
    payload.seat_number !== undefined &&
    payload.seat_number !== seatNumber
  ) {
    throw new ReservationRequestError(
      "预约席位与原内容不一致，尚未提交。",
      409,
      "RESERVATION_REQUEST_CONFLICT",
    )
  }
  if (
    targetProviderAccountId !== null &&
    payload.provider_account_id !== undefined &&
    payload.provider_account_id !== targetProviderAccountId
  ) {
    throw new ReservationRequestError(
      "目标陪玩与原内容不一致，尚未提交。",
      409,
      "RESERVATION_REQUEST_CONFLICT",
    )
  }
  const key = decisionKey ?? `${action}-${randomUuid().replaceAll("-", "")}`
  if (!key || key.trim() !== key || key.length > 64) {
    throw new ReservationRequestError(
      "预约请求编号无效，尚未提交。",
      400,
      "RESERVATION_REQUEST_INVALID",
    )
  }
  return {
    version: 1,
    role,
    accountId: owner,
    store,
    action,
    reservationId,
    seatNumber,
    targetProviderAccountId,
    path: reservationRequestPath(action, reservationId),
    payload: { ...payload },
    decisionKey: key,
  }
}

function parseIntent(raw: string, key: string): ReservationRequestIntent {
  const value: unknown = JSON.parse(raw)
  if (!record(value) || value.version !== 1) throw new Error("invalid version")
  const action = value.action as ReservationRequestAction
  if (!actions.includes(action)) throw new Error("invalid action")
  if (value.role !== "boss" && value.role !== "provider") throw new Error("invalid role")
  if (
    typeof value.accountId !== "string" ||
    !value.accountId ||
    typeof value.store !== "string" ||
    !value.store ||
    typeof value.decisionKey !== "string" ||
    !value.decisionKey ||
    value.decisionKey.trim() !== value.decisionKey ||
    value.decisionKey.length > 64 ||
    !record(value.payload) ||
    "decision_key" in value.payload ||
    "client_request_id" in value.payload
  ) {
    throw new Error("invalid credential")
  }
  const reservationId = value.reservationId
  if (action === "create" ? reservationId !== null : !positiveId(reservationId)) {
    throw new Error("invalid reservation")
  }
  const seatNumber = value.seatNumber
  if (seatNumber !== null && seatNumber !== 1 && seatNumber !== 2) {
    throw new Error("invalid seat")
  }
  const target = value.targetProviderAccountId
  if (target !== null && !positiveId(target)) throw new Error("invalid target")
  if (
    value.path !== reservationRequestPath(action, reservationId as number | null) ||
    (value.review !== undefined && typeof value.review !== "boolean") ||
    (value.blocked !== undefined && typeof value.blocked !== "boolean") ||
    key !== reservationRequestStorageKey(
      value.role,
      value.accountId,
      (reservationId as number | null) ?? "create",
      value.store,
    )
  ) {
    throw new Error("invalid scope")
  }
  if (
    (seatNumber !== null && value.payload.seat_number !== undefined &&
      value.payload.seat_number !== seatNumber) ||
    (target !== null && value.payload.provider_account_id !== undefined &&
      value.payload.provider_account_id !== target)
  ) {
    throw new Error("invalid content")
  }
  return {
    version: 1,
    role: value.role,
    accountId: value.accountId,
    store: value.store,
    action,
    reservationId: reservationId as number | null,
    seatNumber: seatNumber as 1 | 2 | null,
    targetProviderAccountId: target as number | null,
    path: value.path as string,
    payload: { ...value.payload },
    decisionKey: value.decisionKey,
    ...(value.review === true ? { review: true } : {}),
    ...(value.blocked === true ? { blocked: true } : {}),
  }
}

export function readReservationRequest(
  storage: ReservationRequestStorage,
  key: string,
): ReservationRequestSnapshot {
  try {
    const raw = storage.getItem(key)
    return raw ? { intent: parseIntent(raw, key), error: "" } : { intent: null, error: "" }
  } catch {
    return { intent: null, error: invalidCredential }
  }
}

function sameContent(left: ReservationRequestIntent, right: ReservationRequestIntent): boolean {
  const identity = (value: ReservationRequestIntent) => canonical({
    version: value.version,
    role: value.role,
    accountId: value.accountId,
    store: value.store,
    action: value.action,
    reservationId: value.reservationId,
    seatNumber: value.seatNumber,
    targetProviderAccountId: value.targetProviderAccountId,
    path: value.path,
    payload: value.payload,
  })
  return identity(left) === identity(right)
}

function save(storage: ReservationRequestStorage, key: string, intent: ReservationRequestIntent) {
  storage.setItem(key, canonical(intent))
}

function clearIfCurrent(
  storage: ReservationRequestStorage,
  key: string,
  intent: ReservationRequestIntent,
) {
  try {
    const current = readReservationRequest(storage, key).intent
    if (current?.decisionKey === intent.decisionKey && sameContent(current, intent)) {
      storage.removeItem(key)
    }
  } catch {
    // A verified server result must not be retried merely because cleanup failed.
  }
}

function updateIfCurrent(
  storage: ReservationRequestStorage,
  key: string,
  intent: ReservationRequestIntent,
) {
  try {
    const current = readReservationRequest(storage, key).intent
    if (current?.decisionKey === intent.decisionKey && sameContent(current, intent)) {
      save(storage, key, intent)
    }
  } catch {
    // The write-before-send credential remains intact when a status annotation fails.
  }
}

export function discardReservationRequest(
  storage: ReservationRequestStorage,
  key: string,
) {
  storage.removeItem(key)
}

function numericCodes(error: unknown): number[] {
  const value = error as {
    status?: unknown
    code?: unknown
    body?: { code?: unknown }
  }
  return [value?.status, value?.code, value?.body?.code]
    .filter((item): item is number => typeof item === "number" && Number.isFinite(item))
}

export function reservationRequestFailure(error: unknown): {
  kind: ReservationRequestFailureKind
  message: string
  errorCode: string
} {
  const value = error as {
    message?: unknown
    errorCode?: unknown
    body?: { errorCode?: unknown; msg?: unknown }
  }
  const codes = numericCodes(error)
  const status = codes.find((code) => code >= 400) ?? codes[0] ?? 0
  const errorCode = String(value?.errorCode || value?.body?.errorCode || "")
  const message = String(
    (error instanceof Error && error.message) || value?.body?.msg || "操作未完成，请重试",
  )
  const evidence = `${errorCode} ${message}`
  if (
    errorCode === "RESERVATION_STORAGE_UNAVAILABLE" ||
    (errorCode === "RESERVATION_REQUEST_INVALID" && status === 400)
  ) {
    return { kind: "rejected", message, errorCode }
  }
  if (
    /RESERVATION_CLOCK_AFTER|CLOCK_AFTER/i.test(evidence) ||
    (status === 409 && /已到达截止|已超时|已过期|付款.*超时|确认.*超时/.test(message))
  ) {
    return { kind: "expired", message, errorCode }
  }
  if (
    ["RESERVATION_REQUEST_CONFLICT", "RESERVATION_REQUEST_INVALID", "RESERVATION_CLOCK_REVIEW", "REQUEST_ID_CONFLICT"].includes(errorCode) ||
    ([400, 409].includes(status) && /decision_key|请求编号.*(?:其他|冲突)|原.*(?:内容不同|已回执)/i.test(message)) ||
    (status === 409 && /REVIEW|时钟.*核对|回执.*核对|证据.*核对/i.test(message))
  ) {
    return { kind: "review", message, errorCode }
  }
  if (status === 409) return { kind: "conflict", message, errorCode }
  if (status >= 400 && status < 500 && ![408, 429].includes(status)) {
    return { kind: "rejected", message, errorCode }
  }
  return { kind: "unknown", message, errorCode }
}

export function reservationRequestNotice(error: unknown): {
  kind: ReservationRequestFailureKind
  title: string
  detail: string
  retryable: boolean
} {
  const failure = reservationRequestFailure(error)
  if (failure.kind === "expired") {
    return {
      ...failure,
      title: "预约已到期",
      detail: `${failure.message}。请查看最新预约，不要沿用原请求重试。`,
      retryable: false,
    }
  }
  if (failure.kind === "review") {
    return {
      ...failure,
      title: "预约结果待核对",
      detail: `${failure.message}。原请求及编号已保留，不能换号或修改决定；请刷新预约或联系客服核对。`,
      retryable: false,
    }
  }
  if (failure.kind === "unknown") {
    return {
      ...failure,
      title: "预约结果未知",
      detail: `${failure.message}。请使用原请求同号重试，或先刷新核对；不要改成其他决定。`,
      retryable: true,
    }
  }
  if (failure.kind === "conflict") {
    return {
      ...failure,
      title: "预约状态有冲突",
      detail: `${failure.message}。请刷新预约并核对最新状态后再操作。`,
      retryable: false,
    }
  }
  return {
    ...failure,
    title: "预约操作未完成",
    detail: failure.message,
    retryable: false,
  }
}

export const reservationRequestActionLabel = (
  action: ReservationRequestAction,
): string => ({
  create: "提交预约",
  confirm: "确认预约",
  reject: "拒绝预约",
  cancel: "取消预约",
  convert: "预约成单",
  replace_provider: "邀请补位陪玩",
})[action]

export async function executeReservationRequest<T>({
  storage,
  key,
  request,
  post,
  acceptsResult,
}: {
  storage: ReservationRequestStorage
  key: string
  request: ReservationRequestIntent
  post: (path: string, payload: Record<string, unknown>) => Promise<T>
  acceptsResult?: (result: T) => boolean
}): Promise<T> {
  let requested: ReservationRequestIntent
  try {
    requested = parseIntent(canonical(request), key)
  } catch {
    throw new ReservationRequestError(
      "原预约请求与当前门店、账号或预约作用域不一致，尚未提交。",
      409,
      "RESERVATION_REQUEST_CONFLICT",
    )
  }
  const snapshot = readReservationRequest(storage, key)
  if (snapshot.error) {
    throw new ReservationRequestError(snapshot.error, 409, "RESERVATION_REQUEST_INVALID")
  }
  if (snapshot.intent && !sameContent(snapshot.intent, requested)) {
    throw new ReservationRequestError(
      "原预约请求的动作、预约、席位、目标陪玩或决定内容不同，不能复用请求编号。",
      409,
      "RESERVATION_REQUEST_CONFLICT",
    )
  }
  const original = snapshot.intent ?? requested
  if (original.review || original.blocked) {
    throw new ReservationRequestError(
      original.review
        ? "原预约请求仍待人工核对，不能换号或重复提交。"
        : "原预约请求已被明确拒绝或凭证已变化，请先核对预约记录。",
      409,
      original.review ? "RESERVATION_CLOCK_REVIEW" : "RESERVATION_REQUEST_CONFLICT",
    )
  }
  try {
    save(storage, key, original)
  } catch {
    throw new ReservationRequestError(
      "无法保存原预约请求，尚未提交；请检查浏览器存储后重试。",
      409,
      "RESERVATION_STORAGE_UNAVAILABLE",
    )
  }
  const hadPending = snapshot.intent !== null
  try {
    const result = await post(original.path, {
      ...original.payload,
      decision_key: original.decisionKey,
      client_request_id: original.decisionKey,
    })
    if (acceptsResult && !acceptsResult(result)) {
      throw new ReservationRequestError(
        "服务返回内容无法与原预约核对，结果尚未确认。",
        0,
        "RESERVATION_RESULT_UNKNOWN",
      )
    }
    clearIfCurrent(storage, key, original)
    return result
  } catch (error) {
    const failure = reservationRequestFailure(error)
    if (failure.kind === "expired" || (["rejected", "conflict"].includes(failure.kind) && !hadPending)) {
      clearIfCurrent(storage, key, original)
    } else if (failure.kind === "rejected" || failure.kind === "conflict") {
      updateIfCurrent(storage, key, { ...original, blocked: true })
    } else if (failure.kind === "review") {
      updateIfCurrent(storage, key, { ...original, review: true })
    }
    throw error
  }
}
