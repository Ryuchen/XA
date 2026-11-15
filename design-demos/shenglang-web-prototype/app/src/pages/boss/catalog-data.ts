import type { CatalogService } from '@/pages/boss/ServiceCatalogPage'

export const defaultCatalogService: CatalogService = {
  id: 1,
  name: '王者娱乐双排',
  description: '轻松沟通，快乐上分',
  game: '王者荣耀',
  tab: 'experience',
  platform: '手游',
  level: '不限难度',
  price: 29,
  unit: '份',
  variants: 3,
  providerEligible: true,
  coupon: true,
  tone: 'from-sky-400/35 via-blue-600/25 to-indigo-700/30',
}
