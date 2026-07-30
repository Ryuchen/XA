import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/admin',
  timeout: 15000,
})
type RetryableConfig = InternalAxiosRequestConfig & { _retry?: boolean }
let refreshPromise: Promise<string> | null = null

service.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

service.interceptors.response.use(
  (response) => {
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
    const status = error?.response?.status
    const data = error?.response?.data
    if (status === 401) {
      const auth = useAuthStore()
      const config = error.config as RetryableConfig
      const isRefreshRequest = config?.url?.includes('/auth/refresh')
      if (auth.refresh && config && !config._retry && !isRefreshRequest) {
        config._retry = true
        try {
          if (!refreshPromise) {
            refreshPromise = auth.refreshAccessToken().finally(() => {
              refreshPromise = null
            })
          }
          const token = await refreshPromise
          config.headers = config.headers || {}
          config.headers.Authorization = `Bearer ${token}`
          return service(config)
        } catch {
          // Fall through to the shared logout path.
        }
      }
      auth.clear()
      ElMessage.error('登录已过期，请重新登录')
      router.replace('/login')
      return Promise.reject(error)
    }
    const msg = data?.msg || error.message || '网络错误'
    ElMessage.error(msg)
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
  return service(config) as unknown as Promise<ApiResult<T>>
}

export default service
