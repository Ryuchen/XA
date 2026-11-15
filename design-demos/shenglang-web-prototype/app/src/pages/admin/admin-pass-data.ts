export type AdminPassTier = 'BLACK' | 'GOLD' | 'SILVER' | 'BRONZE'

export interface AdminPassProduct {
  tier: AdminPassTier
  name: string
  dailyPrice: number
  delaySeconds: number
}

export const initialAdminPassProducts: AdminPassProduct[] = [
  { tier: 'BLACK', name: '黑卡通行证', dailyPrice: 50, delaySeconds: 0 },
  { tier: 'GOLD', name: '金卡通行证', dailyPrice: 30, delaySeconds: 30 },
  { tier: 'SILVER', name: '银卡通行证', dailyPrice: 10, delaySeconds: 60 },
  { tier: 'BRONZE', name: '铜卡通行证', dailyPrice: 2, delaySeconds: 120 },
]

export const initialNoPassDelaySeconds = 300

export interface AdminPassPurchase {
  id: number
  accountNo: string
  nickname: string
  username: string
  tier: AdminPassTier
  days: number
  paidAmount: number
  startsAt: string
  expiresAt: string
  createdAt: string
  txNo: string
}

export const adminPassPurchases: AdminPassPurchase[] = [
  { id: 2061, accountNo: 'PW-0102', nickname: '奈奈', username: 'provider_nana', tier: 'BLACK', days: 1, paidAmount: 50, startsAt: '2026-11-04 09:00', expiresAt: '2026-11-05 09:00', createdAt: '2026-11-04 08:58', txNo: 'TX-PASS-20261104-2061' },
  { id: 2060, accountNo: 'PW-0101', nickname: '小鹿', username: 'provider_deer', tier: 'GOLD', days: 1, paidAmount: 30, startsAt: '2026-11-04 08:30', expiresAt: '2026-11-05 08:30', createdAt: '2026-11-04 08:30', txNo: 'TX-PASS-20261104-2060' },
  { id: 2059, accountNo: 'PW-0103', nickname: '阿澈', username: 'provider_ache', tier: 'GOLD', days: 1, paidAmount: 30, startsAt: '2026-11-04 18:00', expiresAt: '2026-11-05 18:00', createdAt: '2026-11-03 17:56', txNo: 'TX-PASS-20261103-2059' },
  { id: 2058, accountNo: 'PW-0104', nickname: '可可', username: 'provider_coco', tier: 'SILVER', days: 1, paidAmount: 10, startsAt: '2026-11-03 16:20', expiresAt: '2026-11-04 16:20', createdAt: '2026-11-03 16:20', txNo: 'TX-PASS-20261103-2058' },
  { id: 2057, accountNo: 'PW-0106', nickname: '北辰', username: 'provider_beichen', tier: 'BRONZE', days: 1, paidAmount: 2, startsAt: '2026-11-03 14:00', expiresAt: '2026-11-04 14:00', createdAt: '2026-11-03 14:00', txNo: 'TX-PASS-20261103-2057' },
  { id: 2056, accountNo: 'PW-0107', nickname: '洛洛', username: 'provider_luoluo', tier: 'SILVER', days: 1, paidAmount: 10, startsAt: '2026-11-03 10:00', expiresAt: '2026-11-04 10:00', createdAt: '2026-11-03 10:00', txNo: 'TX-PASS-20261103-2056' },
]

export const adminPassTierLabel: Record<AdminPassTier, string> = {
  BLACK: '黑卡通行证', GOLD: '金卡通行证', SILVER: '银卡通行证', BRONZE: '铜卡通行证',
}
