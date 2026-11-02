import { api, currentUser } from './session'

const running = new Map<string, Promise<unknown>>()
export function fundRequest<T>(url: string, payload: Record<string, unknown>): Promise<T> {
  const owner = currentUser.value?.id
  if (!owner) return Promise.reject(new Error('请先登录'))
  const key = `xa-fund:${owner}:${url}:${JSON.stringify(payload)}`
  const existing = running.get(key); if (existing) return existing as Promise<T>
  let id: string
  try {
    const stored = localStorage.getItem(key)
    id = stored ? (JSON.parse(stored) as {data: string}).data : `fund-${Date.now()}-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(key, JSON.stringify({data:id}))
  } catch { return Promise.reject(new Error('无法保存交易请求，请检查设备存储后重试')) }
  const operation = api<T>(url, { method:'POST', data:{ ...payload, client_request_id:id } }).then(result => {
    if (currentUser.value?.id !== owner) throw new Error('账号已切换，请刷新页面')
    try { localStorage.removeItem(key) } catch { /* retain id if storage is unavailable */ }
    return result
  }).catch((cause: { code?: number; errorCode?: string; fundResult?: {code:number; data?:T} }) => {
    if (currentUser.value?.id !== owner) throw new Error('账号已切换，请刷新页面')
    if (cause.errorCode === 'FUND_ALREADY_PROCESSED' && cause.fundResult?.code === 0) {
      try { localStorage.removeItem(key) } catch { /* preserve id */ }
      return cause.fundResult.data as T
    }
    if (typeof cause.code === 'number' && cause.code >= 400 && cause.code < 500 && cause.code !== 409 && cause.code !== 429) {
      try { localStorage.removeItem(key) } catch { /* preserve id */ }
    }
    throw cause
  }).finally(() => { running.delete(key) })
  running.set(key, operation)
  return operation
}
