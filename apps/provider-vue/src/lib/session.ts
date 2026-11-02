import { ref } from 'vue'

type User = { id: string; role: 'provider'; nickname: string; username: string; avatar: string; phone?: string }
type LoginPayload = {
  requiresOTP?: boolean; requiresOTPSetup?: boolean; otpChallenge?: string; otpSecret?: string; otpQr?: string
  recoveryCodes?: string[]; token?: string; refreshToken?: string
  userInfo?: { id?: string | number; username?: string; nickname?: string; role?: string; phone?: string }
}
export type LoginResult = LoginPayload
type Envelope<T> = { code: number; errorCode?: string; msg?: string; data?: T; fundResult?: Envelope<T> }
const get = <T>(key: string): T | null => {
  try { return (JSON.parse(localStorage.getItem(key) || 'null') as { data: T } | null)?.data ?? null } catch { return null }
}
const put = (key: string, value: unknown) => localStorage.setItem(key, JSON.stringify({ data: value }))
export const accessToken = () => get<string>('token')
export const currentUser = ref<User | null>(get<User>('xa_provider_user'))
let generation = 0
export const sessionRevision = () => generation
let refreshPromise: Promise<string> | null = null

export function clearSession() {
  generation++
  for (const key of ['token', 'xa_provider_refresh_token', 'xa_provider_user']) localStorage.removeItem(key)
  currentUser.value = null
}

export function persistLogin(data: LoginPayload) {
  if (!data.token || !data.userInfo) throw new Error('登录信息不完整，请重试')
  const info = data.userInfo
  const user: User = { id: String(info.id || ''), role: 'provider', nickname: info.nickname || info.username || '陪玩用户', username: info.username || '', avatar: '', phone: info.phone }
  generation++
  put('token', data.token)
  if (data.refreshToken) put('xa_provider_refresh_token', data.refreshToken)
  else localStorage.removeItem('xa_provider_refresh_token')
  put('xa_provider_user', user)
  currentUser.value = user
}

export async function login(username: string, password: string, otpChallenge?: string, otpCode?: string): Promise<LoginPayload> {
  const res = await fetch('/api/users/account-login/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: username.trim(), password, otpChallenge, otpCode }) })
  const body = await res.json() as Envelope<LoginPayload>
  if (!res.ok || body.code !== 0 || !body.data) throw new Error(body.msg || '登录失败')
  return body.data
}

async function refresh(revision: number): Promise<string> {
  const token = get<string>('xa_provider_refresh_token')
  if (!token) throw new Error('登录已过期，请重新登录')
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const res = await fetch('/api/users/refresh/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh: token }) })
      const body = await res.json() as Envelope<{ token: string }>
      if (!res.ok || body.code !== 0 || !body.data?.token) {
        if (res.status === 401 && revision === generation) clearSession()
        throw new Error(body.msg || '登录状态暂时无法刷新')
      }
      if (revision !== generation) throw new Error('登录状态已变化')
      put('token', body.data.token)
      return body.data.token
    })().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

export async function api<T>(path: string, options: { method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'; data?: unknown } = {}): Promise<T> {
  const revision = generation
  let token = get<string>('token')
  if (!token) throw new Error('请先登录')
  const send = (access: string) => {
    const multipart = options.data instanceof FormData
    const headers: Record<string, string> = { Authorization: `Bearer ${access}` }
    if (options.data !== undefined && !multipart) headers['Content-Type'] = 'application/json'
    return fetch('/api' + path, { method: options.method || 'GET', headers,
      body: options.data === undefined ? undefined : multipart ? options.data as FormData : JSON.stringify(options.data) })
  }
  let res = await send(token)
  if (res.status === 401) { token = await refresh(revision); res = await send(token) }
  if (revision !== generation) throw new Error('登录状态已变化')
  const body = await res.json() as Envelope<T>
  if (!res.ok || body.code !== 0) throw Object.assign(new Error(body.msg || '请求失败，请重试'), { status: res.status, code: body.code, errorCode: body.errorCode, fundResult: body.fundResult })
  return body.data as T
}
