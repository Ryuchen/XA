import { clearSession, isAdminEntry } from "@xa/shared"
import { withdrawalIntentKey } from "./withdrawal-intent.ts"

const key = "xa:fronted:platform:session:v1"
export const PLATFORM_AUTHORIZATION_DENIED_EVENT = "xa:platform-authorization-denied"

export type PlatformRole = "SUPER_ADMIN" | "FINANCE" | "ORDER_VIEWER" | "ORDER_OPERATOR"
export type PlatformCapability =
  | "STORE_MANAGEMENT_V1"
  | "PLATFORM_SETUP_V1"
  | "PLATFORM_CLOCKS_V1"
  | "PLATFORM_DEPOSIT_V1"
  | "PLATFORM_WITHDRAW_V1"
  | "PLATFORM_ORDERS_V1"
  | "PLATFORM_DISPATCH_V1"
  | "PLATFORM_PASS_REPAYMENT_V1"
  | "SAAS_MODULES_V1"
export type PlatformProfile = {
  id: string
  username: string
  nickname?: string
  identity_type: "platform"
  grants: { role: PlatformRole; scope_slug: string }[]
  capabilities: PlatformCapability[]
}
export type PlatformStoreStatus = "PROVISIONING" | "CONFIGURING" | "ACTIVE" | "DISABLED"
export type PlatformStore = {
  id: number
  slug: string
  name: string
  status: PlatformStoreStatus
  currency_name: string
}
export type PlatformStoreDirectory = { results: PlatformStore[] }
type PlatformSession = { token: string; refresh: string; profile: PlatformProfile; sessionId: string }

export class PlatformRequestError extends Error {
  readonly status: number
  readonly code: string | undefined
  constructor(message: string, status: number, code?: string) { super(message); this.status = status; this.code = code }
}
export class PlatformNetworkError extends Error {}
export class PlatformSessionError extends Error {}

const PROFILE_REQUIRED_KEYS = ["id", "username", "identity_type", "grants", "capabilities"] as const
const PROFILE_KEYS = new Set<string>([...PROFILE_REQUIRED_KEYS, "nickname"])
const GRANT_KEYS = ["role", "scope_slug"] as const
const STORE_KEYS = [
  "id", "slug", "name", "currency_name", "entity_prefix", "provider_name_prefix",
  "logo_url", "background_url", "introduction", "status", "is_active",
  "boss_url", "provider_url", "admin_url",
] as const
const ROLES = new Set<string>(["SUPER_ADMIN", "FINANCE", "ORDER_VIEWER", "ORDER_OPERATOR"])
const CAPABILITIES = new Set<string>([
  "STORE_MANAGEMENT_V1", "PLATFORM_SETUP_V1", "PLATFORM_CLOCKS_V1",
  "PLATFORM_DEPOSIT_V1", "PLATFORM_WITHDRAW_V1", "PLATFORM_ORDERS_V1",
  "PLATFORM_DISPATCH_V1", "PLATFORM_PASS_REPAYMENT_V1", "SAAS_MODULES_V1",
])
const STORE_STATUSES = new Set<string>(["PROVISIONING", "CONFIGURING", "ACTIVE", "DISABLED"])
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const ENTITY_PREFIX = /^[A-Z0-9]{2,4}$/
const PROVIDER_PREFIX = /^[A-Z]{2,8}$/
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(name => Object.hasOwn(value, name))
}

function boundedString(value: unknown, maximum: number, allowEmpty = true): value is string {
  return typeof value === "string" && value.length <= maximum &&
    (allowEmpty || value.length > 0) && !CONTROL_CHARACTER.test(value)
}

function credential(value: unknown): value is string {
  return boundedString(value, 16_384, false) && value.trim().length > 0
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
}

function publicUrl(value: unknown): value is string {
  if (!boundedString(value, 500, true)) return false
  if (!value) return true
  try {
    const url = new URL(value)
    return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password
  } catch { return false }
}

export function parsePlatformProfile(value: unknown): PlatformProfile {
  if (!record(value) ||
      !PROFILE_REQUIRED_KEYS.every(name => Object.hasOwn(value, name)) ||
      Object.keys(value).some(name => !PROFILE_KEYS.has(name)) ||
      typeof value.id !== "string" || !UUID.test(value.id) ||
      !boundedString(value.username, 150, false) || !value.username.trim() ||
      (Object.hasOwn(value, "nickname") && !boundedString(value.nickname, 80, true)) ||
      value.identity_type !== "platform" || !Array.isArray(value.grants) || value.grants.length > 1000 ||
      !Array.isArray(value.capabilities) || value.capabilities.length > 32) {
    throw new Error("平台身份响应格式异常，已停止授权")
  }
  const grants: PlatformProfile["grants"] = []
  const grantKeys = new Set<string>()
  for (const grant of value.grants) {
    if (!record(grant) || !exactKeys(grant, GRANT_KEYS) ||
        typeof grant.role !== "string" || !ROLES.has(grant.role) ||
        typeof grant.scope_slug !== "string" ||
        (grant.scope_slug !== "*" && !STORE_SLUG.test(grant.scope_slug))) {
      throw new Error("平台身份授权格式异常，已停止授权")
    }
    const identity = `${grant.role}:${grant.scope_slug}`
    if (grantKeys.has(identity)) throw new Error("平台身份包含重复授权，已停止授权")
    grantKeys.add(identity)
    grants.push({ role: grant.role as PlatformRole, scope_slug: grant.scope_slug })
  }
  const capabilities: PlatformCapability[] = []
  const capabilityKeys = new Set<string>()
  for (const capability of value.capabilities) {
    if (typeof capability !== "string" || !CAPABILITIES.has(capability) || capabilityKeys.has(capability)) {
      throw new Error("平台身份能力格式异常，已停止授权")
    }
    capabilityKeys.add(capability)
    capabilities.push(capability as PlatformCapability)
  }
  return {
    id: value.id,
    username: value.username,
    ...(Object.hasOwn(value, "nickname") ? { nickname: value.nickname as string } : {}),
    identity_type: "platform",
    grants,
    capabilities,
  }
}

export function parsePlatformStores(value: unknown): PlatformStoreDirectory {
  if (!record(value) || !exactKeys(value, ["results"]) || !Array.isArray(value.results) || value.results.length > 10_000) {
    throw new Error("平台门店目录响应格式异常，已停止展示")
  }
  const ids = new Set<number>()
  const slugs = new Set<string>()
  const results = value.results.map((store): PlatformStore => {
    if (!record(store) || !exactKeys(store, STORE_KEYS) ||
        !positiveInteger(store.id) || ids.has(store.id) ||
        typeof store.slug !== "string" || !STORE_SLUG.test(store.slug) || slugs.has(store.slug) ||
        !boundedString(store.name, 80, false) || !store.name.trim() ||
        !boundedString(store.currency_name, 16, false) || !store.currency_name.trim() ||
        !(store.entity_prefix === null || (typeof store.entity_prefix === "string" && ENTITY_PREFIX.test(store.entity_prefix))) ||
        !(typeof store.provider_name_prefix === "string" && (!store.provider_name_prefix || PROVIDER_PREFIX.test(store.provider_name_prefix))) ||
        !boundedString(store.logo_url, 1000, true) || !boundedString(store.background_url, 1000, true) ||
        typeof store.introduction !== "string" || store.introduction.length > 2000 ||
        typeof store.status !== "string" || !STORE_STATUSES.has(store.status) ||
        typeof store.is_active !== "boolean" || store.is_active !== (store.status === "ACTIVE") ||
        !publicUrl(store.boss_url) || !publicUrl(store.provider_url) || !publicUrl(store.admin_url)) {
      throw new Error("平台门店目录记录格式异常，已停止展示")
    }
    ids.add(store.id)
    slugs.add(store.slug)
    return {
      id: store.id,
      slug: store.slug,
      name: store.name,
      status: store.status as PlatformStoreStatus,
      currency_name: store.currency_name,
    }
  })
  return { results }
}

function parsePlatformSession(value: unknown): PlatformSession | null {
  if (!record(value) || !exactKeys(value, ["token", "refresh", "profile", "sessionId"]) ||
      !credential(value.token) || !credential(value.refresh) || !credential(value.sessionId)) return null
  try {
    return { token: value.token, refresh: value.refresh, profile: parsePlatformProfile(value.profile), sessionId: value.sessionId }
  } catch { return null }
}

export function platformMutationResultUnknown(cause: unknown) {
  if (cause instanceof PlatformSessionError) return false
  if (cause instanceof PlatformNetworkError) return true
  if (cause instanceof PlatformRequestError) return cause.status === 408 || cause.status >= 500
  return true
}

export function platformSession(): PlatformSession | null {
  if (!isAdminEntry()) return null
  try {
    return parsePlatformSession(JSON.parse(sessionStorage.getItem(key) || "null"))
  } catch { return null }
}

export function savePlatformSession(value: { token: string; refresh: string; profile: PlatformProfile }) {
  if (!isAdminEntry() || !credential(value.token) || !credential(value.refresh)) throw new Error("平台登录身份无效")
  let profile: PlatformProfile
  try { profile = parsePlatformProfile(value.profile) }
  catch { throw new Error("平台登录身份无效") }
  const previous = platformSession()
  const sessionId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  sessionStorage.setItem(key, JSON.stringify({ token: value.token, refresh: value.refresh, profile, sessionId }))
  removeWithdrawalIntents(previous)
  clearSession("admin")
}

export function clearPlatformSession() {
  const session = platformSession()
  try { sessionStorage.removeItem(key) } catch { return /* The next request still revalidates authorization. */ }
  removeWithdrawalIntents(session)
}

function removeWithdrawalIntents(session: PlatformSession | null) {
  if (!session) return
  try { sessionStorage.removeItem(withdrawalIntentKey(session.profile.id, session.sessionId)) }
  catch { /* A different session cannot read this session's markers. */ }
}

function parsePlatformResponse(path: string, value: unknown, session: PlatformSession): unknown {
  if (path === "profile") {
    const profile = parsePlatformProfile(value)
    if (profile.id !== session.profile.id) throw new PlatformSessionError("平台身份响应与当前登录不一致，请重新登录")
    session.profile = profile
    sessionStorage.setItem(key, JSON.stringify(session))
    return profile
  }
  if (path === "stores") return parsePlatformStores(value)
  return value
}

function notifyAuthorizationDenied() {
  if (typeof globalThis.dispatchEvent === "function") globalThis.dispatchEvent(new Event(PLATFORM_AUTHORIZATION_DENIED_EVENT))
}

export type PlatformRequestPath =
  | "profile"
  | "stores"
  | "dashboard"
  | "auth/otp-setup"
  | "accounts"
  | `accounts/${string}`
  | "store-management"
  | `store-management/${string}`
  | `store-management/${string}/content-compliance-receipts?${string}`
  | "compliance-rules"
  | `compliance-rules?${string}`
  | `compliance-rules/${string}`
  | `compliance-rules/${string}/publish`
  | "pass-refund-shortfalls"
  | `pass-refund-shortfalls/${string}`
  | `pass-refund-repayments/${string}`
  | "withdrawals"
  | `withdrawals/${string}`
  | `saas/modules/${string}`
  | `orders?${string}`
  | `orders/${string}`

export async function platformRequest<T>(path: PlatformRequestPath, options: { method?: "GET" | "POST" | "PUT" | "PATCH"; data?: unknown } = {}): Promise<T> {
  const session = platformSession()
  if (!session) throw new PlatformSessionError("平台登录已失效，请重新登录")
  const method = options.method || "GET"
  const write = method !== "GET"
  const verify = () => {
    if (platformSession()?.sessionId !== session.sessionId) throw new Error("登录状态已变化，请重新操作")
  }
  const send = async (token: string) => {
    try {
      return await fetch(`/api/admin/platform/${path}`, {
        method, cache: "no-store",
        headers: { Authorization: `Bearer ${token}`, ...(options.data === undefined ? {} : { "Content-Type": "application/json" }) },
        body: options.data === undefined ? undefined : JSON.stringify(options.data),
      })
    } catch {
      throw new PlatformNetworkError(write ? "网络中断，操作结果尚未确认，请刷新后核对。" : "网络连接失败，请检查网络后重试。")
    }
  }
  let response = await send(session.token)
  if (response.status !== 401) verify()
  if (response.status === 401) {
    let refreshed: Response
    let body: unknown
    try {
      refreshed = await fetch("/api/admin/platform/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh: session.refresh }) })
      body = await refreshed.json()
    } catch {
      throw new PlatformSessionError("登录状态无法刷新，请重新登录")
    }
    if (platformSession()?.sessionId !== session.sessionId) throw new PlatformSessionError("登录状态已变化，请重新登录")
    if (!refreshed.ok || !record(body) || body.code !== 0 || !record(body.data) || !credential(body.data.token)) {
      if (refreshed.status === 401) clearPlatformSession()
      throw new PlatformSessionError(typeof body === "object" && body !== null && "msg" in body && typeof body.msg === "string" && body.msg ? body.msg : "登录状态无法刷新，请重新登录")
    }
    session.token = body.data.token
    sessionStorage.setItem(key, JSON.stringify(session))
    response = await send(session.token)
    verify()
  }
  let body: unknown
  try { body = await response.json() }
  catch {
    verify()
    if (!response.ok) {
      if (write && response.status === 403) notifyAuthorizationDenied()
      throw new PlatformRequestError(response.status === 403 ? "平台操作未获授权" : "平台服务响应异常，请稍后重试", response.status)
    }
    throw new PlatformNetworkError(write ? "平台响应格式异常，操作结果尚未确认，请刷新后核对。" : "平台响应格式异常，请稍后重试。")
  }
  verify()
  const envelope = record(body) ? body : null
  const status = !response.ok
    ? response.status
    : typeof envelope?.code === "number" && envelope.code >= 400 && envelope.code <= 599
      ? envelope.code
      : response.status
  if (!response.ok || envelope?.code !== 0) {
    if (write && status === 403) notifyAuthorizationDenied()
    throw new PlatformRequestError(
      typeof envelope?.msg === "string" && envelope.msg ? envelope.msg : status === 403 ? "平台操作未获授权" : "平台数据读取失败",
      status,
      typeof envelope?.errorCode === "string" ? envelope.errorCode : undefined,
    )
  }
  return parsePlatformResponse(path, envelope.data, session) as T
}
