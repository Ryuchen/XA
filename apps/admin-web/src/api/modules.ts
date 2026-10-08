import { request, type ApiResult, type PageResult } from './request'

/** 通用 CRUD 工厂：对应后端 DRF ViewSet 的 RESTful 路由。 */
export function createCrudApi<T = any>(resource: string) {
  const base = `/${resource}/`
  const list = (params?: Record<string, any>) => request<PageResult<T>>({ url: base, method: 'get', params })
  const options = (params?: Record<string, any>) => request<PageResult<T>>({ url: `/options/${resource}/`, params })
  return {
    list,
    options,
    all: (params?: Record<string, any>) => fetchAllPages(list, params),
    allOptions: (params?: Record<string, any>) => fetchAllPages(options, params),
    retrieve: (id: number | string) =>
      request<T>({ url: `${base}${id}/`, method: 'get' }),
    create: (data: Partial<T> | FormData) =>
      request<T>({ url: base, method: 'post', data }),
    update: (id: number | string, data: Partial<T> | FormData) =>
      request<T>({ url: `${base}${id}/`, method: 'patch', data }),
    remove: (id: number | string) =>
      request<ApiResult>({ url: `${base}${id}/`, method: 'delete' }),
    action: (id: number | string, name: string, data?: any) =>
      request<T>({ url: `${base}${id}/${name}/`, method: 'post', data }),
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
export const evaluationApi = createCrudApi('evaluations')
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
export const escortLevelApi = createCrudApi('escort-levels')
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
  stats: () => request({ url: '/dashboard/stats', method: 'get' }),
}

export const playerDashboardApi = {
  stats: (params?: Record<string, any>) =>
    request({ url: '/dashboard/player', method: 'get', params }),
}

export const permissionApi = {
  tree: () => request({ url: '/permissions', method: 'get' }),
}

export const commissionApi = {
  get: () => request<{ rate: number }>({ url: '/config/commission', method: 'get' }),
  update: (rate: number) => request<{ rate: number }>({ url: '/config/commission', method: 'put', data: { rate } }),
}

export const withdrawConfigApi = {
  get: () => request<{ min_amount: number; tax_rate: number }>({ url: '/config/withdraw', method: 'get' }),
  update: (min_amount: number, tax_rate: number) =>
    request<{ min_amount: number; tax_rate: number }>({ url: '/config/withdraw', method: 'put', data: { min_amount, tax_rate } }),
}

export const checkinConfigApi = {
  get: () => request({ url: '/config/checkin', method: 'get' }),
  update: (data: Record<string, any>) => request({ url: '/config/checkin', method: 'put', data }),
}

export const chatApi = {
  list: (params?: Record<string, any>) =>
    request<PageResult>({ url: '/chat-sessions/', method: 'get', params }),
  messages: (id: number | string) =>
    request<{ list: any[]; total: number }>({ url: `/chat-sessions/${id}/messages/`, method: 'get' }),
  reply: (id: number | string, data: FormData) =>
    request({ url: `/chat-sessions/${id}/reply/`, method: 'post', data }),
  read: (id: number | string) =>
    request({ url: `/chat-sessions/${id}/read/`, method: 'post' }),
}

export interface DepositRules { cash_target:number; earnings_target:number; earnings_multiplier:number; deposit_percent:number; settlement_percent:number; hold_hours:number }
export const depositRulesApi = {
  get: () => request<DepositRules>({ url: '/config/deposit-rules', method: 'get' }),
  update: (deposit_percent:number) => request<DepositRules>({ url: '/config/deposit-rules', method: 'put', data: { deposit_percent } }),
}
