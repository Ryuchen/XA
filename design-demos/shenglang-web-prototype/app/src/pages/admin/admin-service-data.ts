export type AdminCommissionType = 'PERCENT' | 'FIXED'

export interface AdminGameCategory {
  id: number
  name: string
  active: boolean
  sortOrder: number
  escortCount: number
}

export interface AdminServiceCategory {
  id: number
  name: string
  isGift: boolean
  active: boolean
  sortOrder: number
}

export interface AdminServiceItem {
  id: number
  name: string
  description: string
  gameCategoryId: number | null
  serviceCategoryId: number | null
  gameplay: string
  platform: '' | 'MOBILE' | 'PC'
  securityLevel: '' | 'CONFIDENTIAL' | 'TOP_SECRET' | 'ANY'
  providerKind: '' | 'COMPANION' | 'GUARD' | 'GRIND' | 'RANK'
  providerGrade: string
  requiredLevel: string
  priceRaw: number
  priceUnit: string
  billingProviderCount: 1 | 2
  purchaseMode: 'DIRECT' | 'CONSULT'
  purchaseNote: string
  isAddon: boolean
  active: boolean
  sortOrder: number
  commissionOverride: boolean
  commissionType: AdminCommissionType
  commissionRate: number | null
  commissionFixedRaw: number
  commissionPolicy: 'LEGACY' | 'EXPERIENCE' | 'COMPANION' | 'REGULAR' | 'FUN'
  experienceKind: '' | 'NEW_FIRST' | 'NEW_VALUE' | 'DAILY' | 'DAILY_VALUE'
  serviceType: 'REGULAR' | 'ACTIVITY_LIMITED' | 'PROMOTIONAL' | 'COMPANION' | 'EXPERIENCE'
  allowsDiscountCoupons: boolean
  benefitKind: '' | 'COMPANION_HOUR' | 'SAFEBOX_33'
  giftNamingDays: number
  giftFestival: string
  giftStartsAt: string
  giftEndsAt: string
  catalogFamily: string
  catalogVariant: string
  rules: string
  hasOrders: boolean
}

export const initialAdminGameCategories: AdminGameCategory[] = [
  { id: 1, name: '王者荣耀', active: true, sortOrder: 10, escortCount: 18 },
  { id: 2, name: '英雄联盟', active: true, sortOrder: 20, escortCount: 12 },
  { id: 3, name: '和平精英', active: true, sortOrder: 30, escortCount: 9 },
  { id: 4, name: '金铲铲之战', active: true, sortOrder: 40, escortCount: 7 },
]

export const initialAdminServiceCategories: AdminServiceCategory[] = [
  { id: 11, name: '娱乐陪玩', isGift: false, active: true, sortOrder: 10 },
  { id: 12, name: '技术陪玩', isGift: false, active: true, sortOrder: 20 },
  { id: 21, name: '常规礼物', isGift: true, active: true, sortOrder: 10 },
  { id: 22, name: '专属冠名', isGift: true, active: true, sortOrder: 20 },
  { id: 23, name: '节日限定', isGift: true, active: true, sortOrder: 30 },
]

export const adminGameplays = ['娱乐双排', '技术提升', '周末盲盒']

export const initialAdminServices: AdminServiceItem[] = [
  {
    id: 101, name: '王者娱乐双排', description: '轻松沟通，快乐上分', gameCategoryId: 1, serviceCategoryId: 11,
    gameplay: '娱乐双排', platform: 'MOBILE', securityLevel: 'ANY', providerKind: 'COMPANION', providerGrade: '王者', requiredLevel: '',
    priceRaw: 290, priceUnit: '份', billingProviderCount: 2, purchaseMode: 'DIRECT', purchaseNote: '两位陪玩共同履约', isAddon: false, active: true, sortOrder: 10,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'COMPANION',
    experienceKind: '', serviceType: 'COMPANION', allowsDiscountCoupons: true, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '王者双排', catalogVariant: '娱乐档 · 2 人', rules: '两位陪玩分别提交入队与结单截图。', hasOrders: true,
  },
  {
    id: 102, name: '王者默契陪伴', description: '认真沟通，稳定发挥', gameCategoryId: 1, serviceCategoryId: 11,
    gameplay: '娱乐双排', platform: 'MOBILE', securityLevel: 'CONFIDENTIAL', providerKind: 'COMPANION', providerGrade: '星耀', requiredLevel: '',
    priceRaw: 680, priceUnit: '小时', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: true, sortOrder: 20,
    commissionOverride: true, commissionType: 'PERCENT', commissionRate: 15, commissionFixedRaw: 0, commissionPolicy: 'LEGACY',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: 'COMPANION_HOUR', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '王者陪伴', catalogVariant: '默契档 · 1 小时', rules: '按约定时长完成陪伴服务。', hasOrders: true,
  },
  {
    id: 103, name: '峡谷技术双排', description: '对局复盘，技术提升', gameCategoryId: 2, serviceCategoryId: 12,
    gameplay: '技术提升', platform: 'PC', securityLevel: 'TOP_SECRET', providerKind: 'RANK', providerGrade: '王者', requiredLevel: '',
    priceRaw: 560, priceUnit: '小时', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: true, sortOrder: 30,
    commissionOverride: true, commissionType: 'FIXED', commissionRate: 0, commissionFixedRaw: 80, commissionPolicy: 'REGULAR',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: true, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '峡谷技术单', catalogVariant: '技术档 · 1 小时', rules: '完成后提供一局复盘记录。', hasOrders: true,
  },
  {
    id: 104, name: '专属训练方案', description: '由客服确认目标与安排', gameCategoryId: 2, serviceCategoryId: 12,
    gameplay: '技术提升', platform: 'PC', securityLevel: 'TOP_SECRET', providerKind: 'RANK', providerGrade: '王者', requiredLevel: '',
    priceRaw: 990, priceUnit: '份', billingProviderCount: 1, purchaseMode: 'CONSULT', purchaseNote: '客服确认训练目标与可用档期', isAddon: false, active: false, sortOrder: 40,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'REGULAR',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '专属训练', catalogVariant: '定制方案', rules: '以客服确认的训练计划为准。', hasOrders: false,
  },
  {
    id: 105, name: '周末欢乐盲盒', description: '随机玩法，惊喜开局', gameCategoryId: 1, serviceCategoryId: 11,
    gameplay: '周末盲盒', platform: 'MOBILE', securityLevel: 'ANY', providerKind: 'COMPANION', providerGrade: '钻石', requiredLevel: '',
    priceRaw: 190, priceUnit: '份', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: true, sortOrder: 50,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'FUN',
    experienceKind: '', serviceType: 'PROMOTIONAL', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '周末盲盒', catalogVariant: '欢乐档', rules: '随机匹配玩法，不指定对局结果。', hasOrders: false,
  },
  {
    id: 106, name: '双人成行加位', description: '主套餐附加一位陪玩', gameCategoryId: 1, serviceCategoryId: 11,
    gameplay: '', platform: '', securityLevel: '', providerKind: '', providerGrade: '', requiredLevel: '资深陪玩',
    priceRaw: 200, priceUnit: '份', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '须与主套餐一同购买', isAddon: true, active: true, sortOrder: 60,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'LEGACY',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '加购项目', catalogVariant: '增加 1 人', rules: '不能独立下单。', hasOrders: false,
  },
  {
    id: 201, name: '心动荧光', description: '常规互动礼物', gameCategoryId: null, serviceCategoryId: 21,
    gameplay: '', platform: '', securityLevel: '', providerKind: '', providerGrade: '', requiredLevel: '',
    priceRaw: 60, priceUnit: '件', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: true, sortOrder: 10,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'LEGACY',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 0, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '常规礼物', catalogVariant: '荧光', rules: '', hasOrders: true,
  },
  {
    id: 202, name: '星海守护者', description: '专属冠名礼物', gameCategoryId: null, serviceCategoryId: 22,
    gameplay: '', platform: '', securityLevel: '', providerKind: '', providerGrade: '', requiredLevel: '',
    priceRaw: 520, priceUnit: '件', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: true, sortOrder: 20,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'LEGACY',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 3, giftFestival: '', giftStartsAt: '', giftEndsAt: '',
    catalogFamily: '冠名礼物', catalogVariant: '3 天冠名', rules: '', hasOrders: true,
  },
  {
    id: 203, name: '金秋邮轮', description: '中秋节日限定礼物', gameCategoryId: null, serviceCategoryId: 23,
    gameplay: '', platform: '', securityLevel: '', providerKind: '', providerGrade: '', requiredLevel: '',
    priceRaw: 660, priceUnit: '件', billingProviderCount: 1, purchaseMode: 'DIRECT', purchaseNote: '', isAddon: false, active: false, sortOrder: 30,
    commissionOverride: false, commissionType: 'PERCENT', commissionRate: null, commissionFixedRaw: 0, commissionPolicy: 'LEGACY',
    experienceKind: '', serviceType: 'REGULAR', allowsDiscountCoupons: false, benefitKind: '', giftNamingDays: 0, giftFestival: '中秋限定', giftStartsAt: '2026-09-25T00:00', giftEndsAt: '2026-10-07T23:59',
    catalogFamily: '节日礼物', catalogVariant: '中秋限定', rules: '', hasOrders: false,
  },
]

import { formatWaveAmount } from '@/lib/format'

export function formatServiceRaw(value: number) {
  return formatWaveAmount(value / 10)
}

export function parseServiceCoins(value: string, allowZero = false) {
  if (!/^(?:0|[1-9]\d{0,8})$/.test(value)) return null
  const raw = Number(value) * 10
  return Number.isSafeInteger(raw) && raw <= 2147483647 && (allowZero ? raw >= 0 : raw > 0) ? raw : null
}

export function serviceCommissionLabel(item: AdminServiceItem, isGift: boolean) {
  if (isGift) return '礼物独立规则'
  if (!item.commissionOverride) return '沿用自动规则'
  return item.commissionType === 'FIXED' ? `整单固定 ${formatServiceRaw(item.commissionFixedRaw)} 浪花` : `${item.commissionRate ?? 0}%`
}
