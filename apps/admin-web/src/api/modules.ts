import { request, type ApiResult, type PageResult } from './request'

/** 通用 CRUD 工厂：对应后端 DRF ViewSet 的 RESTful 路由。 */
export function createCrudApi<T = any>(resource: string) {
  const base = `/${resource}/`
  const list = (params?: Record<string, any>, silent = false) => request<PageResult<T>>({ url: base, method: 'get', params, silent })
  const options = (params?: Record<string, any>, silent = false) => request<PageResult<T>>({ url: `/options/${resource}/`, params, silent })
  return {
    list,
    options,
    all: (params?: Record<string, any>) => fetchAllPages(list, params),
    allOptions: (params?: Record<string, any>, silent = false) => fetchAllPages(p => options(p, silent), params),
    retrieve: (id: number | string, silent = false) =>
      request<T>({ url: `${base}${id}/`, method: 'get', silent }),
    create: (data: Partial<T> | FormData, silent = false) =>
      request<T>({ url: base, method: 'post', data, silent }),
    update: (id: number | string, data: Partial<T> | FormData, silent = false) =>
      request<T>({ url: `${base}${id}/`, method: 'patch', data, silent }),
    remove: (id: number | string, silent = false) =>
      request<ApiResult>({ url: `${base}${id}/`, method: 'delete', silent }),
    action: (id: number | string, name: string, data?: any, silent = false) =>
      request<T>({ url: `${base}${id}/${name}/`, method: 'post', data, silent }),
    collectionAction: (name: string, data?: any) =>
      request({ url: `${base}${name}/`, method: 'post', data }),
  }
}

/** 字典选项按服务端分页逐页读取，避免 page_size 被限制后只显示前 100 项。 */
async function fetchAllPages<T>(fetcher: (params?: Record<string, any>) => Promise<ApiResult<PageResult<T>>>, params: Record<string, any> = {}) {
  const list: T[] = []
  let page = 1
  while (true) {
    const result = await fetcher({ ...params, page, page_size: 100 })
    list.push(...result.data.list)
    if (!result.data.list.length || list.length >= result.data.total) {
      return { ...result, data: { ...result.data, list } }
    }
    page += 1
  }
}

export const userApi = createCrudApi('users')
export const qualificationOptionsApi = { get: () => request<any>({ url: '/qualification-options/' }) }
export const escortApi = createCrudApi('escorts')
export const orderApi = createCrudApi('orders')
export const aftersaleApi = createCrudApi('aftersales')
export const evaluationApi = {
  ...createCrudApi('evaluations'),
  deleteAtVersion: (id: number | string, replyVersion: number) =>
    request({ url: `/evaluations/${id}/`, method: 'delete', data: { reply_version: replyVersion }, silent: true }),
}
export const reportApi = createCrudApi('reports')
export const withdrawApi = createCrudApi('withdrawals')
export const walletApi = createCrudApi('wallets')
export const passPurchaseApi = createCrudApi('pass-purchases')
export const providerPassConfigApi = {
  get: () => request({ url: '/config/provider-pass', method: 'get' }),
  update: (data: Record<string, any>) => request({ url: '/config/provider-pass', method: 'put', data }),
}
export const transactionApi = createCrudApi('transactions')
export const couponApi = {
  ...createCrudApi('coupons'),
  serviceOptions: () => request<{ id: number; name: string }[]>({ url: '/coupons/service-options/' }),
}
export const userCouponApi = createCrudApi('user-coupons')
export const announcementApi = createCrudApi('announcements')
export const bannerApi = createCrudApi('banners')
export const achievementApi = createCrudApi('achievements')
export const checkinGiftApi = createCrudApi('checkin-gifts')
export const checkinProgressApi = createCrudApi('checkin-progress')
export const gameplayApi = createCrudApi('gameplays')
export const catalogImageApi = {
  upload: (image: File) => {
    const data = new FormData()
    data.append('image', image)
    return request<{ url: string; original_url: string }>({ url: '/catalog-image/', method: 'post', data })
  },
}
export const serviceItemApi = createCrudApi('service-items')
export const gameCategoryApi = createCrudApi('game-categories')
export const serviceCategoryApi = createCrudApi('service-categories')
export const bossTypeApi = createCrudApi('boss-types')
export const escortLevelApi = {
  ...createCrudApi('escort-levels'),
  deletionImpact: (id: number | string) => request<{ id: number; name: string; providers: number; services: number; registrations: number }>({ url: `/escort-levels/${id}/deletion-impact/`, method: 'get', silent: true }),
}
export const promotionApi = createCrudApi('promotions')
export const rechargeRecordApi = createCrudApi('recharge-records')
export const disposeRecordApi = createCrudApi('dispose-records')
export const supportCardApi = createCrudApi('support-cards')
export const auditionApi = createCrudApi('audition-links')
export const auditionSignupApi = createCrudApi('audition-signups')
export const providerRegistrationApi = createCrudApi('provider-registrations')
export const messageApi = createCrudApi('messages')
export const roleApi = createCrudApi('roles')
export const adminApi = createCrudApi('admins')
export const auditApi = createCrudApi('audit-logs')

export const dashboardApi = {
  stats: (silent = false) => request({ url: '/dashboard/stats', method: 'get', silent }),
}

export const playerDashboardApi = {
  stats: (params?: Record<string, any>, silent = false) =>
    request({ url: '/dashboard/player', method: 'get', params, silent }),
}

export const permissionApi = {
  tree: () => request({ url: '/permissions', method: 'get' }),
}

export const commissionApi = {
  get: () => request<{ rate: number; commission_type?: 'PERCENT' | 'FIXED'; commission_fixed?: number }>({ url: '/config/commission', method: 'get' }),
  update: (value: number | { rate: number; commission_type: 'PERCENT' | 'FIXED'; commission_fixed: number }) => request<{ rate: number; commission_type?: 'PERCENT' | 'FIXED'; commission_fixed?: number }>({ url: '/config/commission', method: 'put', data: typeof value === 'number' ? { rate: value } : value }),
}

export const withdrawConfigApi = {
  get: (silent = false) => request<{ min_amount: number; tax_rate: number }>({ url: '/config/withdraw', method: 'get', silent }),
  update: (min_amount: number, tax_rate: number, silent = false) =>
    request<{ min_amount: number; tax_rate: number }>({ url: '/config/withdraw', method: 'put', data: { min_amount, tax_rate }, silent }),
}

export const checkinConfigApi = {
  get: () => request({ url: '/config/checkin', method: 'get' }),
  update: (data: Record<string, any>) => request({ url: '/config/checkin', method: 'put', data }),
}

export const chatApi = {
  list: (params?: Record<string, any>) =>
    request<PageResult>({ url: '/chat-sessions/', method: 'get', params }),
  messages: (id: number | string, params?: { cursor: 1; before_id?: number }) =>
    request<{ list: any[]; total: number; has_more?: boolean; next_before_id?: number | null }>({ url: `/chat-sessions/${id}/messages/`, method: 'get', params }),
  reply: (id: number | string, data: FormData) =>
    request({ url: `/chat-sessions/${id}/reply/`, method: 'post', data, silent: true }),
  messageStatus: (id: number | string, clientMessageId: string) =>
    request<{ status: 'FOUND' | 'UNKNOWN'; message: any }>({ url: `/chat-sessions/${id}/message-status/`, method: 'get', params: { client_message_id: clientMessageId }, silent: true }),
  read: (id: number | string, throughId: number) =>
    request<{ unread_support: number }>({ url: `/chat-sessions/${id}/read/`, method: 'post', data: { through_id: throughId } }),
  readIds: (id: number | string, messageIds: number[]) =>
    request<{ unread_support: number }>({ url: `/chat-sessions/${id}/read/`, method: 'post', data: { message_ids: messageIds } }),
}

export interface DepositRules { cash_target:number; earnings_target:number; earnings_multiplier:number; deposit_percent:number; settlement_percent:number; hold_hours:number; conditional_write_supported?: boolean }
export const depositRulesApi = {
  get: (silent = false) => request<DepositRules>({ url: '/config/deposit-rules', method: 'get', silent }),
  update: (deposit_percent:number, expected_deposit_percent?:number, silent = false) => request<DepositRules>({ url: '/config/deposit-rules', method: 'put', data: { deposit_percent, expected_deposit_percent }, silent }),
}
