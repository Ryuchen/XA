import { clearSession, isAdminEntry } from "@xa/shared"
import { withdrawalIntentKey } from "./withdrawal-intent"

const key = "xa:fronted:platform:session:v1"
export type PlatformProfile = { id: string; username: string; nickname?: string; identity_type: "platform"; grants: { role: string; scope_slug: string }[]; capabilities?: string[] }
type PlatformSession = { token: string; refresh: string; profile: PlatformProfile; sessionId: string }
export class PlatformRequestError extends Error {
  readonly status: number
  readonly code: string | undefined
  constructor(message: string, status: number, code?: string) { super(message); this.status = status; this.code = code }
}

export function platformSession(): PlatformSession | null {
  if (!isAdminEntry()) return null
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null")
    return value?.token && value?.refresh && value?.sessionId && value?.profile?.identity_type === "platform" ? value : null
  } catch { return null }
}
export function savePlatformSession(value: { token: string; refresh: string; profile: PlatformProfile }) {
  if (!isAdminEntry() || !value.token || !value.refresh || value.profile?.identity_type !== "platform") throw new Error("平台登录身份无效")
  const previous = platformSession()
  const sessionId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  sessionStorage.setItem(key, JSON.stringify({ ...value, sessionId }))
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
export async function platformRequest<T>(path: "profile" | "stores" | "dashboard" | "auth/otp-setup" | "accounts" | `accounts/${string}` | "store-management" | `store-management/${string}` | "pass-refund-shortfalls" | `pass-refund-shortfalls/${string}` | `pass-refund-repayments/${string}` | "withdrawals" | `withdrawals/${string}` | `saas/modules/${string}` | `orders?${string}` | `orders/${string}`, options: { method?: "GET" | "POST" | "PUT" | "PATCH"; data?: unknown } = {}): Promise<T> {
  const session = platformSession()
  if (!session) throw new Error("平台登录已失效，请重新登录")
  const verify = () => {
    if (platformSession()?.sessionId !== session.sessionId) throw new Error("登录状态已变化，请重新操作")
  }
  const send = async (token: string) => {
    try {
      return await fetch(`/api/admin/platform/${path}`, {
        method: options.method || "GET", cache: "no-store",
        headers: { Authorization: `Bearer ${token}`, ...(options.data === undefined ? {} : { "Content-Type": "application/json" }) },
        body: options.data === undefined ? undefined : JSON.stringify(options.data),
      })
    } catch {
      throw new Error(options.method && options.method !== "GET" ? "网络中断，操作结果尚未确认，请刷新后核对。" : "网络连接失败，请检查网络后重试。")
    }
  }
  let response = await send(session.token)
  verify()
  if (response.status === 401) {
    const refreshed = await fetch("/api/admin/platform/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh: session.refresh }) })
    const body = await refreshed.json()
    verify()
    if (!refreshed.ok || body.code !== 0 || !body.data?.token) {
      if (refreshed.status === 401) clearPlatformSession()
      throw new Error(body.msg || "登录状态无法刷新，请重新登录")
    }
    session.token = body.data.token
    sessionStorage.setItem(key, JSON.stringify(session))
    response = await send(session.token)
    verify()
  }
  const body = await response.json()
  verify()
  if (!response.ok || body.code !== 0) throw new PlatformRequestError(body.msg || "平台数据读取失败", response.status, body.errorCode)
  return body.data as T
}
