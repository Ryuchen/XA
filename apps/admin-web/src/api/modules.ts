import { request, type ApiResult, type PageResult } from './request'

/** 通用 CRUD 工厂：对应后端 DRF ViewSet 的 RESTful 路由。 */
export function createCrudApi<T = any>(resource: string) {
  const base = `/${resource}/`
  return {
    list: (params?: Record<string, any>) =>
      request<PageResult<T>>({ url: base, method: 'get', params }),
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

export const userApi = createCrudApi('users')
export const escortApi = createCrudApi('escorts')
export const orderApi = createCrudApi('orders')
export const evaluationApi = createCrudApi('evaluations')
export const reportApi = createCrudApi('reports')
export const withdrawApi = createCrudApi('withdrawals')
export const walletApi = createCrudApi('wallets')
export const transactionApi = createCrudApi('transactions')
export const couponApi = createCrudApi('coupons')
export const userCouponApi = createCrudApi('user-coupons')
export const announcementApi = createCrudApi('announcements')
export const bannerApi = createCrudApi('banners')
export const achievementApi = createCrudApi('achievements')
export const checkinGiftApi = createCrudApi('checkin-gifts')
export const checkinProgressApi = createCrudApi('checkin-progress')
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
export const messageApi = createCrudApi('messages')
export const roleApi = createCrudApi('roles')
export const adminApi = createCrudApi('admins')

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
  get: () => request<{ min_amount: number }>({ url: '/config/withdraw', method: 'get' }),
  update: (min_amount: number) =>
    request<{ min_amount: number }>({ url: '/config/withdraw', method: 'put', data: { min_amount } }),
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
