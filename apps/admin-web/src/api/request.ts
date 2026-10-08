import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { sessionChangedError, useAuthStore } from '@/stores/auth'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/admin',
  timeout: 15000,
})
type SessionConfig = { _retry?: boolean; _sessionRevision?: number; _accessToken?: string }
type RetryableConfig = InternalAxiosRequestConfig & SessionConfig
let refreshing: { revision: number; operation: Promise<string> } | null = null
const isAuthEndpoint = (config?: RetryableConfig) => /^\/auth\/(login|refresh)\/?$/.test(config?.url || '')
const assertSession = (config?: SessionConfig) => {
  if (config?._sessionRevision !== undefined && config._sessionRevision !== useAuthStore().sessionRevision) {
    throw sessionChangedError()
  }
}
const reportError = (error: any) => ElMessage.error(error?.response?.data?.msg || error.message || '网络错误')

service.interceptors.request.use((config: RetryableConfig) => {
  const auth = useAuthStore()
  config._sessionRevision ??= auth.sessionRevision
  assertSession(config)
  config._accessToken = auth.token
  // Login and refresh use their own credentials, independently of access expiry.
  if (auth.token && !isAuthEndpoint(config)) config.headers.Authorization = `Bearer ${auth.token}`
  else delete config.headers.Authorization
  return config
})

service.interceptors.response.use(
  (response) => {
    assertSession(response.config as RetryableConfig)
    const body = response.data
    // 文件下载等非标准响应直接透传
    if (body == null || typeof body !== 'object' || !('code' in body)) {
      return body
    }
    if (body.code === 0) {
      return body
    }
    // 业务错误
    ElMessage.error(body.msg || '请求失败')
    return Promise.reject(body)
  },
  async (error) => {
    if (error?.code === 'SESSION_CHANGED') throw error
    const config = error.config as RetryableConfig | undefined
    assertSession(config)
    const auth = useAuthStore()
    const revision = auth.sessionRevision
    const status = error?.response?.status
    // Protected requests own refresh failure/logout handling. A failed login
    // must not refresh another session or clear its credentials.
    if (isAuthEndpoint(config)) {
      if (!config?.url?.includes('/auth/refresh')) reportError(error)
      throw error
    }
    if (status === 401 && config) {
      if (config._accessToken && !config._retry && (auth.refresh || auth.token !== config._accessToken)) {
        config._retry = true
        try {
          if (auth.token === config._accessToken) {
            if (!refreshing || refreshing.revision !== revision) {
              const operation = auth.refreshAccessToken()
              refreshing = { revision, operation }
              operation.finally(() => { if (refreshing?.operation === operation) refreshing = null }).catch(() => {})
            }
            await refreshing.operation
          }
          assertSession(config)
        } catch (refreshError: any) {
          assertSession(config)
          if (refreshError?.response?.status !== 401) {
            reportError(refreshError)
            throw refreshError
          }
          auth.clear()
          ElMessage.error('登录已过期，请重新登录')
          void router.replace('/login')
          throw refreshError
        }
        // The retry retains its original session revision; the interceptor
        // checks it again before attaching the current access token.
        return service(config)
      }
      auth.clear()
      ElMessage.error('登录已过期，请重新登录')
      void router.replace('/login')
      return Promise.reject(error)
    }
    reportError(error)
    return Promise.reject(error)
  },
)

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

export function request<T = any>(config: AxiosRequestConfig): Promise<ApiResult<T>> {
  // Capture before Axios schedules interceptors, not after an account switch.
  const scoped: AxiosRequestConfig & SessionConfig = { ...config, _sessionRevision: useAuthStore().sessionRevision }
  return service(scoped) as unknown as Promise<ApiResult<T>>
}

export default service
