export type AdminBannerLinkType = 'NONE' | 'PRODUCT' | 'ANNOUNCEMENT' | 'GAMEPLAY' | 'CONTENT' | 'PAGE' | 'URL'

export interface AdminBannerItem {
  id: number
  title: string
  image: string
  linkType: AdminBannerLinkType
  target: string
  sortOrder: number
  active: boolean
}

export interface AdminBannerTarget {
  id: string
  type: Exclude<AdminBannerLinkType, 'NONE' | 'URL'>
  label: string
  meta: string
  available: boolean
  reason?: string
}

export const bannerLinkTypeLabels: Record<AdminBannerLinkType, string> = {
  NONE: '不跳转',
  PRODUCT: '服务商品',
  ANNOUNCEMENT: '公告',
  GAMEPLAY: '玩法',
  CONTENT: '运营内容',
  PAGE: '业务入口',
  URL: '外部链接',
}

export const adminBannerTargets: AdminBannerTarget[] = [
  { id: 'P101', type: 'PRODUCT', label: '王者娱乐双排', meta: '商品 #101 · 展示中', available: true },
  { id: 'P105', type: 'PRODUCT', label: '周末欢乐盲盒', meta: '商品 #105 · 展示中', available: true },
  { id: 'P104', type: 'PRODUCT', label: '专属训练方案', meta: '商品 #104 · 已下架', available: false, reason: '商品已停止展示。请更换商品或停用 Banner；老板账号资格仍以点击时的实际状态为准。' },
  { id: 'P203', type: 'PRODUCT', label: '金秋邮轮', meta: '商品 #203 · 已下架', available: false, reason: '商品已停止展示，不能继续作为有效投放目标。' },
  { id: 'A1', type: 'ANNOUNCEMENT', label: '声浪新赛季服务说明', meta: '公告 #1 · 已启用', available: true },
  { id: 'A2', type: 'ANNOUNCEMENT', label: '旧版充值公告', meta: '公告 #2 · 已停用', available: false, reason: '公告已停用，老板端无法继续查看。' },
  { id: 'G1', type: 'GAMEPLAY', label: '娱乐双排', meta: '玩法 #1 · 已启用', available: true },
  { id: 'G3', type: 'GAMEPLAY', label: '周末盲盒', meta: '玩法 #3 · 已启用', available: true },
  { id: 'G5', type: 'GAMEPLAY', label: '撤离挑战', meta: '玩法 #5 · 已停用', available: false, reason: '玩法已停用。保存新投放前请启用玩法或更换目标。' },
  { id: 'C1', type: 'CONTENT', label: '会员权益说明', meta: '内容 #1 · 展示中', available: true },
  { id: 'C2', type: 'CONTENT', label: '国庆活动回顾', meta: '内容 #2 · 已结束', available: false, reason: '内容已超过自身展示时间窗口。' },
  { id: 'C3', type: 'CONTENT', label: '每日签到提醒', meta: '内容 #3 · 弹窗内容', available: false, reason: 'POPUP 类型内容不能作为 Banner 跳转目标。' },
  { id: 'catalog', type: 'PAGE', label: '服务价目', meta: '业务入口 · catalog', available: true },
  { id: 'companions', type: 'PAGE', label: '陪玩列表', meta: '业务入口 · companions', available: true },
  { id: 'gifts', type: 'PAGE', label: '礼物中心', meta: '业务入口 · gifts', available: true },
  { id: 'coupons', type: 'PAGE', label: '优惠券', meta: '业务入口 · coupons', available: true },
  { id: 'checkin', type: 'PAGE', label: '每日签到', meta: '业务入口 · checkin', available: true },
  { id: 'points', type: 'PAGE', label: '积分中心', meta: '业务入口 · points', available: true },
  { id: 'membership', type: 'PAGE', label: '会员权益', meta: '业务入口 · membership', available: true },
  { id: 'support', type: 'PAGE', label: '联系客服', meta: '业务入口 · support', available: true },
]

export const initialAdminBanners: AdminBannerItem[] = [
  { id: 1, title: '和默契队友，认真开好每一局', image: './prototype-banners/companion.svg', linkType: 'PRODUCT', target: 'P101', sortOrder: 10, active: true },
  { id: 2, title: '声浪新赛季服务说明', image: './prototype-banners/season.svg', linkType: 'ANNOUNCEMENT', target: 'A1', sortOrder: 20, active: true },
  { id: 3, title: '娱乐双排，快乐不止上分', image: './prototype-banners/duo.svg', linkType: 'GAMEPLAY', target: 'G1', sortOrder: 30, active: true },
  { id: 4, title: '会员权益全新上线', image: './prototype-banners/membership.svg', linkType: 'PAGE', target: 'membership', sortOrder: 40, active: true },
  { id: 5, title: '周末盲盒惊喜开局', image: './prototype-banners/blindbox.svg', linkType: 'PRODUCT', target: 'P105', sortOrder: 50, active: false },
  { id: 6, title: '限时训练方案', image: './prototype-banners/training.svg', linkType: 'PRODUCT', target: 'P104', sortOrder: 60, active: true },
]

export function getBannerTarget(item: Pick<AdminBannerItem, 'linkType' | 'target'>) {
  if (item.linkType === 'NONE' || item.linkType === 'URL') return undefined
  return adminBannerTargets.find(target => target.type === item.linkType && target.id === item.target)
}

export function getBannerTargetLabel(item: Pick<AdminBannerItem, 'linkType' | 'target'>) {
  if (item.linkType === 'NONE') return '仅展示，不响应跳转'
  if (item.linkType === 'URL') return item.target
  return getBannerTarget(item)?.label || item.target || '目标缺失'
}
