import { createRefreshCoordinator, createViewScopeGuard } from "@xa/shared"
import { useCallback, useEffect, useRef, useState } from "react"
import { useLocation } from "react-router"
import {
  createApiClient,
  useRealtime,
  fundRequest as sharedFund,
  formatMoney,
  formatDateTime,
} from "@xa/shared"
export { requestId } from "@xa/shared"

export const client = createApiClient("provider")
export const api = <T>(
  path: string,
  options: { method?: string; data?: unknown; public?: boolean } = {}
) => client.request<T>(path, options)
export const anonymous = <T>(path: string, data?: unknown) =>
  api<T>(path, {
    method: data === undefined ? "GET" : "POST",
    data,
    public: true,
  })
export const fund = <T>(path: string, data: Record<string, unknown>) =>
  sharedFund<T>("provider", path, data)
export const money = (value: number | null | undefined) =>
  value == null ? "—" : formatMoney(value)
export const date = (value: string | null | undefined) =>
  value ? formatDateTime(value) : "—"
export const message = (cause: unknown) =>
  cause instanceof Error ? cause.message : "请求失败，请重试"
export type Failure = {
  status?: number
  code?: number
  errorCode?: string
  message?: string
  data?: unknown
}
export const uncertain = (cause: unknown) => {
  const error = cause as Failure
  return (
    !error.status ||
    error.status >= 500 ||
    (typeof error.code === "number" && error.code >= 500) ||
    error.code === 409 ||
    error.code === 429
  )
}
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("")
  const active = useRef(true),
    sequence = useRef(0),
    currentLoader = useRef(loader),
    scope = useRef([...deps])
  const scopeChanged = scope.current.length !== deps.length || deps.some((value, index) => !Object.is(value, scope.current[index]))
  currentLoader.current = loader
  const reload = useCallback(async () => {
    const version = ++sequence.current
    setLoading(true)
    setError("")
    try {
      const value = await currentLoader.current()
      if (active.current && version === sequence.current) {
        setData(value)
        return value
      }
    } catch (cause) {
      if (active.current && version === sequence.current)
        setError(message(cause))
      throw cause
    } finally {
      if (active.current && version === sequence.current) setLoading(false)
    }
  }, [])
  useEffect(() => {
    if (scopeChanged) {
      scope.current = [...deps]
      setData(null)
    }
    active.current = true
    void reload().catch(() => {})
    return () => {
      active.current = false
      sequence.current++
    }
  }, [reload, ...deps])
  return { data: scopeChanged ? null : data, setData, loading: scopeChanged || loading, error: scopeChanged ? "" : error, reload }
}
export function useRefresh(reload: () => Promise<unknown>, types?: string[]) {
  const current = useRef(reload),
    filter = useRef(types)
  current.current = reload
  filter.current = types
  const coordinator = useRef<ReturnType<
    typeof createRefreshCoordinator
  > | null>(null)
  useRealtime("provider", (event) => coordinator.current?.event(event.type))
  useEffect(() => {
    const active = createRefreshCoordinator({
      reload: () => current.current(),
      isVisible: () => !document.hidden,
      types: () => filter.current,
    })
    coordinator.current = active
    const resume = () => {
      active.resume()
    }
    document.addEventListener("visibilitychange", resume)
    window.addEventListener("focus", resume)
    return () => {
      active.dispose()
      if (coordinator.current === active) coordinator.current = null
      document.removeEventListener("visibilitychange", resume)
      window.removeEventListener("focus", resume)
    }
  }, [])
}
export function readStored<T>(key: string): T | null {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") as T | null
  } catch {
    return null
  }
}
export function useViewGuard() {
  const location = useLocation(),
    guard = useRef(createViewScopeGuard("provider", location.key))
  guard.current.setScope(location.key)
  useEffect(() => {
    guard.current.activate()
    return () => {
      guard.current.dispose()
    }
  }, [])
  return useCallback(() => guard.current.capture(), [])
}
export const statuses: Record<string, string> = {
  PENDING: "待处理",
  GRABBED: "已接单",
  IN_SERVICE: "服务中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  CONFIRMED: "已确认",
  REJECTED: "已退回",
  APPROVED: "已通过",
  CONVERTED: "已转订单",
  EXPIRED: "已过期",
  DRAFT: "待提交",
  SELECTED: "曾被选中",
  WITHDRAWN: "已撤回",
  NOT_SELECTED: "未被选中",
  WAITING: "待结算",
  SETTLED: "已结算",
}
