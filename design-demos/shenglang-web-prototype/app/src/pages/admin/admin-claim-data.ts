export type AdminClaimStatus = 'PENDING' | 'FULFILLING' | 'COMPLETED' | 'CANCELLED'
export type AdminClaimSource = 'GIFT' | 'VIP' | 'REDEMPTION' | 'LOTTERY'
export type AdminClaimKind = 'PHYSICAL' | 'VIRTUAL' | 'SERVICE' | 'NAMING' | 'TAG' | 'BALANCE' | 'POINTS'
export type AdminClaimAccountType = 'BOSS' | 'PROVIDER'

export interface AdminClaimTimelineItem {
  id: number
  status: AdminClaimStatus
  note: string
  operator: string
  at: string
}

export interface AdminClaim {
  id: number
  account: number
  accountName: string
  username: string
  accountType: AdminClaimAccountType
  reward: number
  source: AdminClaimSource
  sourceOrder: number | null
  nameSnapshot: string
  kindSnapshot: AdminClaimKind
  durationDays: number
  durationMonths: number
  highlighted: boolean
  expiresAt: string
  pointsSpent: number
  pointsReturned: boolean
  balanceAmount: number
  status: AdminClaimStatus
  details: Record<string, string>
  deliveryNote: string
  createdAt: string
  completedAt: string
  tagReviewed: boolean | null
  tagSuspensionReason: string
  tagRevisionRequired: boolean
  tagEditable: boolean
  tagRevisionSubmitted: boolean
  tagExpired: boolean
  timeline: AdminClaimTimelineItem[]
}

export const adminClaimStatusLabels: Record<AdminClaimStatus, string> = {
  PENDING: '待处理', FULFILLING: '交付中', COMPLETED: '已完成', CANCELLED: '已取消',
}

export const adminClaimSourceLabels: Record<AdminClaimSource, string> = {
  GIFT: '赠礼权益', VIP: '会员权益', REDEMPTION: '积分兑换', LOTTERY: '转盘奖品',
}

export const adminClaimKindLabels: Record<AdminClaimKind, string> = {
  PHYSICAL: '实物好礼', VIRTUAL: '虚拟物品', SERVICE: '服务权益', NAMING: '冠名权益', TAG: '专属 TAG', BALANCE: '余额奖励', POINTS: '积分奖励',
}

export const adminClaimDetailLabels: Record<string, string> = {
  recipient: '收件人', phone: '联系电话', address: '收货地址', game_uid: '游戏 UID', note: '补充说明', tag: 'TAG 内容', provider_name: '冠名陪玩',
}

export const initialAdminClaims: AdminClaim[] = [
  {
    id: 8108, account: 1001, accountName: '海边散步', username: 'boss_seaside', accountType: 'BOSS', reward: 101, source: 'REDEMPTION', sourceOrder: null,
    nameSnapshot: '声浪限定机械键帽', kindSnapshot: 'PHYSICAL', durationDays: 0, durationMonths: 0, highlighted: false, expiresAt: '', pointsSpent: 1680, pointsReturned: false, balanceAmount: 0,
    status: 'PENDING', details: { recipient: '陈屿', phone: '138 0013 8000', address: '上海市徐汇区滨江西岸 88 号 1206 室', note: '工作日白天配送，到达前电话联系。' }, deliveryNote: '', createdAt: '2026-11-04T10:26:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 1, status: 'PENDING', note: '积分兑换成功，等待俱乐部核对收件资料。', operator: '系统', at: '2026-11-04T10:26:00' }],
  },
  {
    id: 8107, account: 1002, accountName: '蓝鲸不睡觉', username: 'boss_bluewhale', accountType: 'BOSS', reward: 109, source: 'LOTTERY', sourceOrder: null,
    nameSnapshot: '68 体验单', kindSnapshot: 'SERVICE', durationDays: 0, durationMonths: 0, highlighted: false, expiresAt: '', pointsSpent: 0, pointsReturned: false, balanceAmount: 0,
    status: 'FULFILLING', details: { game_uid: 'OW2-SURF-6824', note: '主玩辅助，希望安排晚间两小时体验。' }, deliveryNote: '已安排陪玩「小满」，11 月 4 日 20:00 开始服务。', createdAt: '2026-11-04T09:48:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'FULFILLING', note: '已安排陪玩「小满」，11 月 4 日 20:00 开始服务。', operator: '客服 · 林岚', at: '2026-11-04T10:05:00' }, { id: 1, status: 'PENDING', note: '海浪幸运季中奖，专属服务权益待交付。', operator: '系统', at: '2026-11-04T09:48:00' }],
  },
  {
    id: 8106, account: 2001, accountName: '小满', username: 'provider_xiaoman', accountType: 'PROVIDER', reward: 112, source: 'LOTTERY', sourceOrder: null,
    nameSnapshot: '限定高亮 TAG', kindSnapshot: 'TAG', durationDays: 30, durationMonths: 0, highlighted: true, expiresAt: '', pointsSpent: 0, pointsReturned: false, balanceAmount: 0,
    status: 'PENDING', details: { tag: '温柔指挥 · 稳定上分' }, deliveryNote: '', createdAt: '2026-11-04T09:18:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 1, status: 'PENDING', note: '老板已提交 TAG 内容，等待客服审核。', operator: '系统', at: '2026-11-04T09:18:00' }],
  },
  {
    id: 8105, account: 1003, accountName: '橘子海', username: 'boss_orange', accountType: 'BOSS', reward: 106, source: 'VIP', sourceOrder: null,
    nameSnapshot: 'VIP4 专属 TAG', kindSnapshot: 'TAG', durationDays: 0, durationMonths: 0, highlighted: true, expiresAt: '', pointsSpent: 0, pointsReturned: false, balanceAmount: 0,
    status: 'PENDING', details: { tag: '全场最低价代练' }, deliveryNote: '请移除价格与代练承诺，改为真实的游戏定位或擅长角色。', createdAt: '2026-11-03T22:14:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: true, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'PENDING', note: '请移除价格与代练承诺，改为真实的游戏定位或擅长角色。', operator: '客服 · 林岚', at: '2026-11-04T08:52:00' }, { id: 1, status: 'PENDING', note: '会员升级获得专属 TAG 权益。', operator: '系统', at: '2026-11-03T22:14:00' }],
  },
  {
    id: 8104, account: 1004, accountName: '晚风电台', username: 'boss_nightradio', accountType: 'BOSS', reward: 105, source: 'REDEMPTION', sourceOrder: null,
    nameSnapshot: '30 天高亮 TAG', kindSnapshot: 'TAG', durationDays: 30, durationMonths: 0, highlighted: true, expiresAt: '2026-11-29T16:40:00', pointsSpent: 680, pointsReturned: false, balanceAmount: 0,
    status: 'COMPLETED', details: { tag: '认真沟通 · 快乐游戏' }, deliveryNote: '历史权益已完成交付，标签内容待补充审核记录。', createdAt: '2026-10-28T14:02:00', completedAt: '2026-10-30T16:40:00',
    tagReviewed: false, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'COMPLETED', note: '历史权益已完成交付，标签内容待补充审核记录。', operator: '客服 · 周宁', at: '2026-10-30T16:40:00' }, { id: 1, status: 'PENDING', note: '积分兑换成功。', operator: '系统', at: '2026-10-28T14:02:00' }],
  },
  {
    id: 8103, account: 2002, accountName: '阿澈', username: 'provider_ache', accountType: 'PROVIDER', reward: 106, source: 'VIP', sourceOrder: null,
    nameSnapshot: 'VIP5 高亮 TAG', kindSnapshot: 'TAG', durationDays: 0, durationMonths: 0, highlighted: true, expiresAt: '', pointsSpent: 0, pointsReturned: false, balanceAmount: 0,
    status: 'COMPLETED', details: { tag: '峡谷辅助 · 视野布控' }, deliveryNote: 'TAG 已审核并展示。', createdAt: '2026-11-01T11:20:00', completedAt: '2026-11-01T13:08:00',
    tagReviewed: true, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'COMPLETED', note: 'TAG 已审核并展示。', operator: '客服 · 林岚', at: '2026-11-01T13:08:00' }, { id: 1, status: 'PENDING', note: '会员升级获得高亮 TAG 权益。', operator: '系统', at: '2026-11-01T11:20:00' }],
  },
  {
    id: 8102, account: 1005, accountName: '雾里看海', username: 'boss_mist', accountType: 'BOSS', reward: 101, source: 'REDEMPTION', sourceOrder: null,
    nameSnapshot: '声浪限定机械键帽', kindSnapshot: 'PHYSICAL', durationDays: 0, durationMonths: 0, highlighted: false, expiresAt: '', pointsSpent: 1680, pointsReturned: true, balanceAmount: 0,
    status: 'CANCELLED', details: { recipient: '吴女士', phone: '139 0013 9000', address: '杭州市西湖区文三路 90 号' }, deliveryNote: '用户重复兑换，已取消本笔并退回 1680 积分，商品库存恢复 1 件。', createdAt: '2026-11-02T18:36:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'CANCELLED', note: '用户重复兑换，已取消本笔并退回 1680 积分，商品库存恢复 1 件。', operator: '客服 · 林岚', at: '2026-11-02T18:48:00' }, { id: 1, status: 'PENDING', note: '积分兑换成功。', operator: '系统', at: '2026-11-02T18:36:00' }],
  },
  {
    id: 8101, account: 1006, accountName: '逐浪者', username: 'boss_wave', accountType: 'BOSS', reward: 104, source: 'GIFT', sourceOrder: 31026,
    nameSnapshot: '陪玩冠名 7 天', kindSnapshot: 'NAMING', durationDays: 7, durationMonths: 0, highlighted: false, expiresAt: '2026-11-10T21:30:00', pointsSpent: 0, pointsReturned: false, balanceAmount: 0,
    status: 'COMPLETED', details: { provider_name: '小满', note: '冠名展示为「逐浪者的快乐海湾」。' }, deliveryNote: '冠名已生效，有效期至 2026-11-10 21:30。', createdAt: '2026-11-03T21:12:00', completedAt: '2026-11-03T21:30:00',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'COMPLETED', note: '冠名已生效，有效期至 2026-11-10 21:30。', operator: '客服 · 周宁', at: '2026-11-03T21:30:00' }, { id: 1, status: 'PENDING', note: '赠礼订单 #31026 附送冠名权益。', operator: '系统', at: '2026-11-03T21:12:00' }],
  },
  {
    id: 8100, account: 1007, accountName: '一颗海盐糖', username: 'boss_salt', accountType: 'BOSS', reward: 103, source: 'REDEMPTION', sourceOrder: null,
    nameSnapshot: '游戏改名卡', kindSnapshot: 'VIRTUAL', durationDays: 0, durationMonths: 0, highlighted: false, expiresAt: '', pointsSpent: 280, pointsReturned: false, balanceAmount: 0,
    status: 'FULFILLING', details: { game_uid: 'LOL-44829176', note: '艾欧尼亚大区。' }, deliveryNote: '已向对应游戏 UID 发放，等待系统回执。', createdAt: '2026-11-04T08:05:00', completedAt: '',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 2, status: 'FULFILLING', note: '已向对应游戏 UID 发放，等待系统回执。', operator: '客服 · 周宁', at: '2026-11-04T08:18:00' }, { id: 1, status: 'PENDING', note: '积分兑换成功。', operator: '系统', at: '2026-11-04T08:05:00' }],
  },
  {
    id: 8099, account: 1008, accountName: '岛屿来信', username: 'boss_island', accountType: 'BOSS', reward: 102, source: 'REDEMPTION', sourceOrder: null,
    nameSnapshot: '20 浪花余额', kindSnapshot: 'BALANCE', durationDays: 0, durationMonths: 0, highlighted: false, expiresAt: '', pointsSpent: 200, pointsReturned: false, balanceAmount: 200,
    status: 'COMPLETED', details: {}, deliveryNote: '20 浪花已自动计入老板钱包。', createdAt: '2026-11-01T16:26:00', completedAt: '2026-11-01T16:26:00',
    tagReviewed: null, tagSuspensionReason: '', tagRevisionRequired: false, tagEditable: false, tagRevisionSubmitted: false, tagExpired: false,
    timeline: [{ id: 1, status: 'COMPLETED', note: '20 浪花已自动计入老板钱包。', operator: '系统', at: '2026-11-01T16:26:00' }],
  },
]

export function formatAdminClaimTime(value: string) {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

export function formatAdminClaimTerms(claim: Pick<AdminClaim, 'durationDays' | 'durationMonths'>) {
  const parts = [claim.durationMonths ? `${claim.durationMonths} 个月` : '', claim.durationDays ? `${claim.durationDays} 天` : ''].filter(Boolean)
  return parts.length ? parts.join(' + ') : '无固定期限'
}

export function isAdminClaimTagReady(claim: AdminClaim) {
  return claim.kindSnapshot === 'TAG' && ['PENDING', 'FULFILLING'].includes(claim.status) && !claim.tagRevisionRequired && !!claim.details.tag?.trim()
}

export function isAdminClaimTagReviewNeeded(claim: AdminClaim) {
  return claim.kindSnapshot === 'TAG' && claim.status === 'COMPLETED' && claim.tagReviewed === false && !claim.tagExpired
}

export function maskAdminClaimDetail(key: string, value: string) {
  if (key === 'phone') return value.length > 7 ? `${value.slice(0, 3)} **** ${value.slice(-4)}` : '已填写'
  if (key === 'address') return value.length > 10 ? `${value.slice(0, 8)}…` : value
  if (key === 'game_uid') return value.length > 8 ? `${value.slice(0, 4)}••••${value.slice(-3)}` : value
  return value
}
