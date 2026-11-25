export const PLATFORM_ORDER_PAGE_SIZE = 20
export const PLATFORM_ORDERS_CAPABILITY = "PLATFORM_ORDERS_V1"
export const PLATFORM_ORDER_MAX_PAGE =
  Math.floor(100000 / PLATFORM_ORDER_PAGE_SIZE) + 1

export const PLATFORM_ORDER_STATUS_LABELS = {
  PENDING: "待接单",
  GRABBED: "已接单",
  IN_SERVICE: "服务中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
} as const
export const PLATFORM_PAYMENT_STATUS_LABELS = {
  UNPAID: "未支付",
  PAID: "已支付",
  PARTIALLY_REFUNDED: "部分退款",
  REFUNDED: "已退款",
} as const
export const PLATFORM_ORDER_SOURCE_LABELS = {
  STAFF_MANUAL: "客服代下单",
  BOSS_H5: "老板 H5 下单",
  RESERVATION: "预约转单",
  GIFT: "礼物单",
} as const
export const PLATFORM_DISPATCH_ROUTE_LABELS = {
  PUBLIC: "公开接单",
  STAFF: "客服派单",
} as const
export const PLATFORM_AFTERSALE_STATUS_LABELS = {
  OPEN: "处理中",
  RESOLVED: "处理完成",
  REFUNDED: "已退款",
  PARTIALLY_REFUNDED: "部分退款",
} as const
export const PLATFORM_SETTLEMENT_STATUS_LABELS = {
  REVIEW: "待核对",
  WAITING: "待结算",
  SETTLED: "已结算",
} as const

export type PlatformOrderStatus = keyof typeof PLATFORM_ORDER_STATUS_LABELS
export type PlatformPaymentStatus = keyof typeof PLATFORM_PAYMENT_STATUS_LABELS
export type PlatformOrderSource = keyof typeof PLATFORM_ORDER_SOURCE_LABELS
export type PlatformDispatchRoute = keyof typeof PLATFORM_DISPATCH_ROUTE_LABELS
export type PlatformAftersaleStatus =
  keyof typeof PLATFORM_AFTERSALE_STATUS_LABELS
export type PlatformSettlementStatus =
  keyof typeof PLATFORM_SETTLEMENT_STATUS_LABELS

export type PlatformOrder = {
  id: string
  store_slug: string
  store_name: string
  order_no: string
  service_name: string
  customer_name: string
  provider_names: string[]
  currency_name: string
  version: string
  allowed_actions: []
  status: PlatformOrderStatus
  payment_status: PlatformPaymentStatus
  payment_display: { kind: "DIRECT_TEST" } | null
  dispatch_route: PlatformDispatchRoute
  order_source: PlatformOrderSource | null
  requires_boss_selection: boolean
  pending_deadline_expired: boolean
  aftersale_status: PlatformAftersaleStatus | null
  settlement_status: PlatformSettlementStatus
  created_at: string
  grabbed_at: string | null
  started_at: string | null
  in_service_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  updated_at: string
}

export type PlatformOrderEvent = {
  id: string
  at: string
  title: string
  actor: string | null
}

export type PlatformOrderDetail = PlatformOrder & {
  events: PlatformOrderEvent[]
}

export type PlatformOrderPage = {
  results: PlatformOrder[]
  total: number
  complete: boolean
  generated_at: string
  missing_stores: string[]
}

export type PlatformOrderFilters = {
  store: string
  source: PlatformOrderSource | ""
  status: PlatformOrderStatus | ""
  payment: PlatformPaymentStatus | ""
  keyword: string
}

export type PlatformOrderAccess = {
  capabilities?: string[]
  grants: { role: string; scope_slug: string }[]
}

export type PlatformStoreOption = { slug: string; name: string }

const ORDER_KEYS = [
  "id",
  "store_slug",
  "store_name",
  "order_no",
  "service_name",
  "customer_name",
  "provider_names",
  "currency_name",
  "version",
  "allowed_actions",
  "status",
  "payment_status",
  "payment_display",
  "dispatch_route",
  "order_source",
  "requires_boss_selection",
  "pending_deadline_expired",
  "aftersale_status",
  "settlement_status",
  "created_at",
  "grabbed_at",
  "started_at",
  "in_service_at",
  "completed_at",
  "cancelled_at",
  "updated_at",
] as const
const DETAIL_KEYS = [...ORDER_KEYS, "events"] as const
const EVENT_KEYS = ["id", "at", "title", "actor"] as const
const LIST_KEYS = [
  "results",
  "total",
  "complete",
  "generated_at",
  "missing_stores",
] as const
const SEARCH_KEYS = new Set([
  "page",
  "page_size",
  "store",
  "source",
  "status",
  "payment",
  "keyword",
])
const ORDER_STATUS_VALUES = Object.keys(
  PLATFORM_ORDER_STATUS_LABELS
) as PlatformOrderStatus[]
const PAYMENT_STATUS_VALUES = Object.keys(
  PLATFORM_PAYMENT_STATUS_LABELS
) as PlatformPaymentStatus[]
const ORDER_SOURCE_VALUES = Object.keys(
  PLATFORM_ORDER_SOURCE_LABELS
) as PlatformOrderSource[]
const DISPATCH_ROUTE_VALUES = Object.keys(
  PLATFORM_DISPATCH_ROUTE_LABELS
) as PlatformDispatchRoute[]
const AFTERSALE_STATUS_VALUES = Object.keys(
  PLATFORM_AFTERSALE_STATUS_LABELS
) as PlatformAftersaleStatus[]
const SETTLEMENT_STATUS_VALUES = Object.keys(
  PLATFORM_SETTLEMENT_STATUS_LABELS
) as PlatformSettlementStatus[]
const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const ISO_DATE_TIME =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/

export class PlatformOrderContractError extends Error {
  constructor(
    message = "订单响应格式异常，已停止展示，请重试或联系平台维护。"
  ) {
    super(message)
    this.name = "PlatformOrderContractError"
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function hasExactKeys(
  value: unknown,
  keys: readonly string[]
): value is Record<string, unknown> {
  if (!isRecord(value)) return false
  const actual = Object.keys(value)
  return (
    actual.length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  )
}

function isString(
  value: unknown,
  maximum: number,
  allowEmpty = true
): value is string {
  return (
    typeof value === "string" &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0)
  )
}

function isIsoDateTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    ISO_DATE_TIME.test(value) &&
    Number.isFinite(Date.parse(value))
  )
}

function isNullableIsoDateTime(value: unknown): value is string | null {
  return value === null || isIsoDateTime(value)
}

function isPositiveId(value: unknown): value is string {
  return typeof value === "string" && /^[1-9]\d*$/.test(value)
}

function isOneOf<T extends string>(
  value: unknown,
  values: readonly T[]
): value is T {
  return (
    typeof value === "string" && (values as readonly string[]).includes(value)
  )
}

function parseEvent(value: unknown): PlatformOrderEvent {
  if (
    !hasExactKeys(value, EVENT_KEYS) ||
    !isPositiveId(value.id) ||
    !isIsoDateTime(value.at) ||
    !isString(value.title, 100, false) ||
    !(value.actor === null || isString(value.actor, 150))
  )
    throw new PlatformOrderContractError()
  return { id: value.id, at: value.at, title: value.title, actor: value.actor }
}

function parseOrder(
  value: unknown,
  detail: boolean
): PlatformOrder | PlatformOrderDetail {
  if (
    !hasExactKeys(value, detail ? DETAIL_KEYS : ORDER_KEYS) ||
    !isPositiveId(value.id) ||
    !isString(value.store_slug, 48, false) ||
    !STORE_SLUG.test(value.store_slug) ||
    !isString(value.store_name, 100, false) ||
    !isString(value.order_no, 32, false) ||
    !isString(value.service_name, 100) ||
    !isString(value.customer_name, 150) ||
    !Array.isArray(value.provider_names) ||
    !value.provider_names.every((name) => isString(name, 150)) ||
    !isString(value.currency_name, 16, false) ||
    !isIsoDateTime(value.version) ||
    !Array.isArray(value.allowed_actions) ||
    value.allowed_actions.length !== 0 ||
    !isOneOf(value.status, ORDER_STATUS_VALUES) ||
    !isOneOf(value.payment_status, PAYMENT_STATUS_VALUES) ||
    !(
      value.payment_display === null ||
      (hasExactKeys(value.payment_display, ["kind"]) &&
        value.payment_display.kind === "DIRECT_TEST")
    ) ||
    !isOneOf(value.dispatch_route, DISPATCH_ROUTE_VALUES) ||
    !(
      value.order_source === null ||
      isOneOf(value.order_source, ORDER_SOURCE_VALUES)
    ) ||
    typeof value.requires_boss_selection !== "boolean" ||
    typeof value.pending_deadline_expired !== "boolean" ||
    !(
      value.aftersale_status === null ||
      isOneOf(value.aftersale_status, AFTERSALE_STATUS_VALUES)
    ) ||
    !isOneOf(value.settlement_status, SETTLEMENT_STATUS_VALUES) ||
    !isIsoDateTime(value.created_at) ||
    !isNullableIsoDateTime(value.grabbed_at) ||
    !isNullableIsoDateTime(value.started_at) ||
    !isNullableIsoDateTime(value.in_service_at) ||
    !isNullableIsoDateTime(value.completed_at) ||
    !isNullableIsoDateTime(value.cancelled_at) ||
    !isIsoDateTime(value.updated_at)
  )
    throw new PlatformOrderContractError()

  const order: PlatformOrder = {
    id: value.id,
    store_slug: value.store_slug,
    store_name: value.store_name,
    order_no: value.order_no,
    service_name: value.service_name,
    customer_name: value.customer_name,
    provider_names: [...value.provider_names],
    currency_name: value.currency_name,
    version: value.version,
    allowed_actions: [],
    status: value.status,
    payment_status: value.payment_status,
    payment_display:
      value.payment_display === null ? null : { kind: "DIRECT_TEST" },
    dispatch_route: value.dispatch_route,
    order_source: value.order_source,
    requires_boss_selection: value.requires_boss_selection,
    pending_deadline_expired: value.pending_deadline_expired,
    aftersale_status: value.aftersale_status,
    settlement_status: value.settlement_status,
    created_at: value.created_at,
    grabbed_at: value.grabbed_at,
    started_at: value.started_at,
    in_service_at: value.in_service_at,
    completed_at: value.completed_at,
    cancelled_at: value.cancelled_at,
    updated_at: value.updated_at,
  }
  if (!detail) return order
  if (!Array.isArray(value.events)) throw new PlatformOrderContractError()
  const events = value.events.map(parseEvent)
  if (new Set(events.map((event) => event.id)).size !== events.length)
    throw new PlatformOrderContractError()
  return { ...order, events }
}

export function parsePlatformOrderPage(
  value: unknown,
  options: { store?: string; pageSize?: number } = {}
): PlatformOrderPage {
  const pageSize = options.pageSize ?? 100
  if (
    !hasExactKeys(value, LIST_KEYS) ||
    !Array.isArray(value.results) ||
    value.results.length > pageSize ||
    !Number.isSafeInteger(value.total) ||
    (value.total as number) < value.results.length ||
    typeof value.complete !== "boolean" ||
    !isIsoDateTime(value.generated_at) ||
    !Array.isArray(value.missing_stores) ||
    !value.missing_stores.every(
      (slug) => isString(slug, 48, false) && STORE_SLUG.test(slug)
    ) ||
    new Set(value.missing_stores).size !== value.missing_stores.length ||
    value.complete !== (value.missing_stores.length === 0) ||
    (options.store !== undefined &&
      !value.results.every(
        (row) => isRecord(row) && row.store_slug === options.store
      ))
  )
    throw new PlatformOrderContractError()

  const results = value.results.map(
    (row) => parseOrder(row, false) as PlatformOrder
  )
  if (new Set(results.map(orderIdentity)).size !== results.length)
    throw new PlatformOrderContractError()
  return {
    results,
    total: value.total as number,
    complete: value.complete,
    generated_at: value.generated_at,
    missing_stores: [...value.missing_stores] as string[],
  }
}

export function parsePlatformOrderDetail(value: unknown): PlatformOrderDetail {
  return parseOrder(value, true) as PlatformOrderDetail
}

export function platformOrderListAvailable(
  current: PlatformOrderPage | null | undefined
): boolean {
  return !!current && (current.complete || current.results.length > 0)
}

export function platformOrderCanGoNext(total: number, page: number): boolean {
  return (
    page < PLATFORM_ORDER_MAX_PAGE &&
    page * PLATFORM_ORDER_PAGE_SIZE < total
  )
}

export function orderIdentity(
  order: Pick<PlatformOrder, "store_slug" | "id">
): string {
  return `${order.store_slug}/${order.id}`
}

function oneSearchValue(params: URLSearchParams, name: string): string | null {
  const values = params.getAll(name)
  if (values.length > 1)
    throw new PlatformOrderContractError("筛选参数无效，请清除后重试。")
  return values[0] ?? null
}

export function parsePlatformOrderSearch(search: string): {
  filters: PlatformOrderFilters
  page: number
} {
  const params = new URLSearchParams(search)
  for (const name of params.keys()) {
    if (!SEARCH_KEYS.has(name))
      throw new PlatformOrderContractError(
        "存在不支持的筛选参数，请清除后重试。"
      )
  }
  const rawPage = oneSearchValue(params, "page")
  const rawPageSize = oneSearchValue(params, "page_size")
  if (
    rawPageSize !== null &&
    rawPageSize !== String(PLATFORM_ORDER_PAGE_SIZE)
  ) {
    throw new PlatformOrderContractError("每页条数无效，请清除后重试。")
  }
  const page = rawPage === null ? 1 : Number(rawPage)
  if (
    !Number.isInteger(page) ||
    page < 1 ||
    page > PLATFORM_ORDER_MAX_PAGE ||
    (rawPage !== null && !/^[1-9]\d*$/.test(rawPage))
  ) {
    throw new PlatformOrderContractError("页码无效，请清除后重试。")
  }
  const store = oneSearchValue(params, "store") || ""
  const source = oneSearchValue(params, "source") || ""
  const status = oneSearchValue(params, "status") || ""
  const payment = oneSearchValue(params, "payment") || ""
  const keyword = (oneSearchValue(params, "keyword") || "").trim()
  if (
    (store !== "" && !STORE_SLUG.test(store)) ||
    (source !== "" && !isOneOf(source, ORDER_SOURCE_VALUES)) ||
    (status !== "" && !isOneOf(status, ORDER_STATUS_VALUES)) ||
    (payment !== "" && !isOneOf(payment, PAYMENT_STATUS_VALUES)) ||
    keyword.length > 100 ||
    CONTROL_CHARACTER.test(keyword)
  )
    throw new PlatformOrderContractError("筛选条件无效，请清除后重试。")
  return {
    filters: {
      store,
      source,
      status,
      payment,
      keyword,
    } as PlatformOrderFilters,
    page,
  }
}

export function buildPlatformOrderQuery(
  filters: PlatformOrderFilters,
  page: number
): string {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(PLATFORM_ORDER_PAGE_SIZE),
  })
  for (const name of [
    "store",
    "source",
    "status",
    "payment",
    "keyword",
  ] as const) {
    const value = filters[name]
    if (value) params.set(name, value)
  }
  parsePlatformOrderSearch(params.toString())
  return params.toString()
}

export function platformOrderSearchWithFilters(
  _current: string,
  draft: PlatformOrderFilters
): string {
  const params = new URLSearchParams()
  for (const name of [
    "store",
    "source",
    "status",
    "payment",
    "keyword",
  ] as const) {
    const value = draft[name].trim()
    if (value) params.set(name, value)
  }
  // A submitted filter change always returns to page one; page_size stays UI-owned.
  return buildPlatformOrderQuery(
    parsePlatformOrderSearch(params.toString()).filters,
    1
  )
}

export function parsePlatformStoreOptions(
  value: unknown
): PlatformStoreOption[] {
  if (!Array.isArray(value))
    throw new PlatformOrderContractError(
      "门店目录格式异常，已停止展示，请重试。"
    )
  const seen = new Set<string>()
  return value.map((row) => {
    if (
      !isRecord(row) ||
      !isString(row.slug, 48, false) ||
      !STORE_SLUG.test(row.slug) ||
      !isString(row.name, 100, false) ||
      seen.has(row.slug)
    )
      throw new PlatformOrderContractError(
        "门店目录格式异常，已停止展示，请重试。"
      )
    seen.add(row.slug)
    return { slug: row.slug, name: row.name }
  })
}

export function hasPlatformOrderRole(profile: PlatformOrderAccess): boolean {
  return (
    Array.isArray(profile.grants) &&
    profile.grants.some((grant) =>
      ["SUPER_ADMIN", "ORDER_VIEWER", "ORDER_OPERATOR"].includes(grant.role)
    )
  )
}

export function hasPlatformOrderReadAccess(
  profile: PlatformOrderAccess
): boolean {
  return (
    hasPlatformOrderRole(profile) &&
    Array.isArray(profile.capabilities) &&
    profile.capabilities.includes(PLATFORM_ORDERS_CAPABILITY)
  )
}

export function platformOrderScopes(profile: PlatformOrderAccess): string[] {
  return Array.isArray(profile.grants)
    ? profile.grants
        .filter((grant) =>
          ["SUPER_ADMIN", "ORDER_VIEWER", "ORDER_OPERATOR"].includes(grant.role)
        )
        .map((grant) => grant.scope_slug)
    : []
}

export function canReadPlatformOrderStore(
  profile: PlatformOrderAccess,
  slug: string
): boolean {
  const scopes = platformOrderScopes(profile)
  return scopes.includes("*") || scopes.includes(slug)
}

export type KeyedReadPhase = "idle" | "loading" | "ready" | "error"
export type KeyedReadState<T> = {
  key: string
  phase: KeyedReadPhase
  data?: T
  error?: string
}
export type KeyedReadEvent<T> =
  | { type: "start"; key: string }
  | { type: "reset"; key: string }
  | { type: "success"; key: string; data: T }
  | { type: "failure"; key: string; error: string }

export function initialKeyedRead<T>(key: string): KeyedReadState<T> {
  return { key, phase: "idle" }
}

export function keyedReadReducer<T>(
  state: KeyedReadState<T>,
  event: KeyedReadEvent<T>
): KeyedReadState<T> {
  if (event.type === "start") {
    return state.key === event.key && state.data !== undefined
      ? { key: event.key, phase: "loading", data: state.data }
      : { key: event.key, phase: "loading" }
  }
  if (event.type === "reset") return initialKeyedRead<T>(event.key)
  if (state.key !== event.key) return state
  if (event.type === "success")
    return { key: event.key, phase: "ready", data: event.data }
  return {
    key: event.key,
    phase: "error",
    error: event.error,
    ...(state.data === undefined ? {} : { data: state.data }),
  }
}
