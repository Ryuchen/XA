import { createApiClient, fundRequest, requestId as sharedRequestId } from "@xa/shared"
import { reviewDescriptor, trackReview } from "@/lib/reviews"

export interface ApiResult<T = any> {
  code: number
  msg?: string
  data: T
}
export interface PageResult<T = any> {
  list: T[]
  total: number
  page: number
  page_size: number
}
export interface RequestConfig {
  url: string
  method?: string
  params?: Record<string, any>
  data?: any
  baseURL?: string
  silent?: boolean
  signal?: AbortSignal
  /** Internal destination for checking this mutation's result in its own list. */
  reviewPath?: string
}
export const adminClient = createApiClient("admin")

/** Preserve the existing console API envelope while sharing session refresh. */
export function request<T = any>(config: RequestConfig): Promise<ApiResult<T>> {
  const absolute = config.baseURL && !config.baseURL.endsWith("/admin")
  const path =
    config.url.startsWith("/admin/") ||
    (config.url.startsWith("/orders/") && absolute)
      ? config.url
      : absolute
        ? config.url
        : `/admin${config.url.startsWith("/") ? "" : "/"}${config.url}`
  const run = () =>
    adminClient.requestEnvelope<T>(path, {
      method: config.method || "GET",
      data: config.data,
      query: config.params,
      signal: config.signal,
    })
  const method = (config.method || "GET").toUpperCase()
  const descriptor = !["GET", "HEAD", "OPTIONS"].includes(method)
    ? reviewDescriptor(path, method)
    : null
  return descriptor
    ? trackReview(
        path,
        config.reviewPath ? { ...descriptor, path: config.reviewPath } : descriptor,
        config.data instanceof FormData
          ? String(config.data.get("client_request_id") || "") || undefined
          : config.data?.client_request_id,
        run
      )
    : run()
}
export function reviewedFundRequest<T = any>(
  path: string,
  payload: Record<string, unknown>
) {
  const descriptor = reviewDescriptor(path)
  const run = () => fundRequest<T>("admin", path, payload)
  return descriptor
    ? trackReview(
        path,
        descriptor,
        payload.client_request_id
          ? String(payload.client_request_id)
          : undefined,
        run
      )
    : run()
}
export function errorMessage(error: unknown): string {
  const value = error as {
    msg?: string
    message?: string
    data?: { msg?: string }
    response?: { data?: { msg?: string } }
  }
  return (
    value?.response?.data?.msg ||
    value?.data?.msg ||
    value?.msg ||
    value?.message ||
    "请求未完成，请检查网络后重试。"
  )
}
export const requestId = (prefix = "admin") => sharedRequestId(prefix, "_")
export default adminClient
