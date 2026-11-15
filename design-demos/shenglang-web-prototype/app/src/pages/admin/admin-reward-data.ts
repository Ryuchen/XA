import { formatWaveAmount } from '@/lib/format'

export type AdminRewardKind = 'PHYSICAL' | 'VIRTUAL' | 'SERVICE' | 'NAMING' | 'TAG' | 'BALANCE' | 'POINTS'
export type AdminRewardAudience = 'BOSS' | 'PROVIDER' | 'ALL'
export type AdminVipQuotaKind = '' | 'FRIEND' | 'BIRTHDAY_GIFT'

export interface AdminRewardItem {
  id: number
  code: string
  vipQuotaKind: AdminVipQuotaKind
  audience: AdminRewardAudience
  name: string
  kind: AdminRewardKind
  imageUrl: string
  description: string
  pointCost: number
  pointValue: number
  balanceCoins: number
  durationDays: number
  durationMonths: number
  highlighted: boolean
  serviceId: number | null
  stock: number | null
  redeemedCount: number
  isActive: boolean
  inShop: boolean
  sortOrder: number
  activeLottery?: string
}

export const adminRewardKindLabels: Record<AdminRewardKind, string> = {
  PHYSICAL: '实物寄送',
  VIRTUAL: '游戏内交付',
  SERVICE: '服务权益',
  NAMING: '冠名权益',
  TAG: '自定义 TAG',
  BALANCE: '余额兑换',
  POINTS: '积分奖励',
}

export const adminRewardAudienceLabels: Record<AdminRewardAudience, string> = {
  BOSS: '老板',
  PROVIDER: '陪玩',
  ALL: '全部会员',
}

export const adminRewardServiceOptions = [
  { id: 201, name: '王者荣耀 · 星耀陪练 1 小时' },
  { id: 202, name: '和平精英 · 双人上分 1 小时' },
  { id: 203, name: '三角洲行动 · 安全箱代肝体验' },
]

export const initialAdminRewards: AdminRewardItem[] = [
  {
    id: 101, code: 'headset-royal', vipQuotaKind: '', audience: 'BOSS', name: '王者限定电竞耳机', kind: 'PHYSICAL', imageUrl: '',
    description: '声浪定制无线耳机，兑换后填写收件信息，由客服安排寄送。', pointCost: 6800, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: 12, redeemedCount: 8, isActive: true, inShop: true, sortOrder: 10,
  },
  {
    id: 102, code: 'skin-legend', vipQuotaKind: '', audience: 'BOSS', name: '传说皮肤自选礼', kind: 'VIRTUAL', imageUrl: '',
    description: '支持指定游戏内传说皮肤，兑换后补充游戏账号与区服。', pointCost: 3200, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: null, redeemedCount: 26, isActive: true, inShop: true, sortOrder: 20,
  },
  {
    id: 103, code: 'companion-hour', vipQuotaKind: '', audience: 'ALL', name: '星耀陪练 1 小时', kind: 'SERVICE', imageUrl: '',
    description: '兑换后由客服确认可预约时段，服务权益不折现。', pointCost: 1800, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: 201, stock: 30, redeemedCount: 14, isActive: true, inShop: true, sortOrder: 30,
  },
  {
    id: 104, code: 'naming-seven-days', vipQuotaKind: '', audience: 'BOSS', name: '专属冠名 · 7 天', kind: 'NAMING', imageUrl: '',
    description: '为指定陪玩设置专属冠名，发放后生效，到期自动结束。', pointCost: 5200, pointValue: 0, balanceCoins: 0,
    durationDays: 7, durationMonths: 0, highlighted: false, serviceId: null, stock: 5, redeemedCount: 3, isActive: true, inShop: true, sortOrder: 40,
    activeLottery: 'S8 幸运转盘',
  },
  {
    id: 105, code: 'provider-highlight-tag', vipQuotaKind: '', audience: 'PROVIDER', name: '自定义高亮 TAG', kind: 'TAG', imageUrl: '',
    description: '提交个性化标签文案，审核通过后展示，有效期一个自然月。', pointCost: 2600, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 1, highlighted: true, serviceId: null, stock: null, redeemedCount: 6, isActive: false, inShop: false, sortOrder: 50,
  },
  {
    id: 106, code: 'balance-100', vipQuotaKind: '', audience: 'BOSS', name: '100 浪花余额', kind: 'BALANCE', imageUrl: '',
    description: '兑换成功后 100 浪花自动到账，不增加累计充值。', pointCost: 1000, pointValue: 0, balanceCoins: 100,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: 0, redeemedCount: 40, isActive: true, inShop: true, sortOrder: 60,
  },
  {
    id: 107, code: 'points-500', vipQuotaKind: '', audience: 'ALL', name: '500 积分补给', kind: 'POINTS', imageUrl: '',
    description: '兑换成功后发放 500 积分，可在积分明细中核对。', pointCost: 450, pointValue: 500, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: null, redeemedCount: 18, isActive: true, inShop: true, sortOrder: 70,
  },
  {
    id: 108, code: 'offline-finals-pack', vipQuotaKind: 'BIRTHDAY_GIFT', audience: 'ALL', name: '线下观赛应援包', kind: 'PHYSICAL', imageUrl: '',
    description: '线下活动限定应援周边，当前尚未开放兑换。', pointCost: 8800, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: 4, redeemedCount: 0, isActive: false, inShop: false, sortOrder: 80,
  },
]

export function getAdminRewardTerms(item: AdminRewardItem) {
  const parts: string[] = []
  if (item.kind === 'BALANCE') parts.push(`到账 ${formatWaveAmount(item.balanceCoins)} 浪花`)
  if (item.kind === 'POINTS') parts.push(`获得 ${item.pointValue.toLocaleString('zh-CN')} 积分`)
  const duration = [item.durationMonths ? `${item.durationMonths} 个月` : '', item.durationDays ? `${item.durationDays} 天` : ''].filter(Boolean).join(' + ')
  if (duration) parts.push(`发放后有效 ${duration}`)
  if (item.kind === 'TAG' && item.highlighted) parts.push('高亮 TAG')
  if (item.description) parts.push(item.description)
  return parts.join('；')
}

export function getAdminRewardShopBlockReason(item: AdminRewardItem) {
  if (!item.isActive) return '好礼未启用'
  if (!item.inShop) return '未在积分商城展示'
  if (item.audience === 'PROVIDER') return '仅适用于陪玩身份'
  return ''
}
