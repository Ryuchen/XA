import type { AdminBroadcastKind } from '@/pages/admin/admin-broadcast-rule-data'

export const ADMIN_BROADCAST_PROTOTYPE_NOW = '2026-11-04T12:00:00'

export type AdminBroadcastPublicStatus = 'PUBLIC' | 'HIDDEN' | 'EXPIRED' | 'UNPUBLISHED' | 'SCHEDULED'

export interface AdminBroadcastRecordItem {
  id: number
  kind: AdminBroadcastKind
  content: string
  isVisible: boolean
  startsAt: string
  expiresAt: string
  createdAt: string
  publishedAt: string | null
}

export const broadcastPublicStatusLabels: Record<AdminBroadcastPublicStatus, string> = {
  PUBLIC: '公开中',
  HIDDEN: '已隐藏',
  EXPIRED: '已到期',
  UNPUBLISHED: '未发布',
  SCHEDULED: '待开始',
}

export function getAdminBroadcastPublicStatus(item: Pick<AdminBroadcastRecordItem, 'isVisible' | 'publishedAt' | 'startsAt' | 'expiresAt'>): AdminBroadcastPublicStatus {
  if (!item.isVisible) return 'HIDDEN'
  if (!item.publishedAt) return 'UNPUBLISHED'
  if (item.startsAt > ADMIN_BROADCAST_PROTOTYPE_NOW) return 'SCHEDULED'
  if (item.expiresAt <= ADMIN_BROADCAST_PROTOTYPE_NOW) return 'EXPIRED'
  return 'PUBLIC'
}

export function getAdminBroadcastBlockReason(item: Pick<AdminBroadcastRecordItem, 'isVisible' | 'publishedAt' | 'startsAt' | 'expiresAt'>) {
  const status = getAdminBroadcastPublicStatus(item)
  if (status === 'HIDDEN') return '前台显示已关闭'
  if (status === 'UNPUBLISHED') return '记录尚未发布'
  if (status === 'SCHEDULED') return '尚未到达开始时间'
  if (status === 'EXPIRED') return '展示截止时间已到'
  return ''
}

export function formatAdminBroadcastTime(value: string | null) {
  if (!value) return '尚未发布'
  return value.replace('T', ' ').slice(0, 16)
}

export const initialAdminBroadcastRecords: AdminBroadcastRecordItem[] = [
  { id: 2088, kind: 'GIFT', content: '山海岚 送给 星野「星河礼赞」', isVisible: true, startsAt: '2026-11-04T10:30:00', expiresAt: '2026-11-07T10:30:00', createdAt: '2026-11-04T10:30:00', publishedAt: '2026-11-04T10:30:00' },
  { id: 2087, kind: 'LOTTERY_SERVICE', content: '恭喜 岚** 抽中「1 小时体验单」', isVisible: true, startsAt: '2026-11-04T09:45:00', expiresAt: '2026-11-07T09:45:00', createdAt: '2026-11-04T09:45:00', publishedAt: '2026-11-04T09:45:00' },
  { id: 2086, kind: 'RECHARGE', content: '岚** 充值成功，感谢对俱乐部的支持', isVisible: true, startsAt: '2026-11-04T08:20:00', expiresAt: '2026-11-05T08:20:00', createdAt: '2026-11-04T08:20:00', publishedAt: '2026-11-04T08:20:00' },
  { id: 2085, kind: 'REVIEW', content: '岚** 给 星野 送上了好评', isVisible: false, startsAt: '2026-11-04T07:10:00', expiresAt: '2026-11-06T07:10:00', createdAt: '2026-11-04T07:10:00', publishedAt: '2026-11-04T07:10:00' },
  { id: 2084, kind: 'NAMING', content: '山海岚 获得了冠名权益「星野专属冠名」', isVisible: true, startsAt: '2026-11-03T18:00:00', expiresAt: '2026-11-06T18:00:00', createdAt: '2026-11-03T18:00:00', publishedAt: '2026-11-03T18:00:00' },
  { id: 2083, kind: 'BIRTHDAY', content: '今天是 山海岚 的生日，声浪电竞祝你生日快乐！', isVisible: true, startsAt: '2026-11-03T00:00:00', expiresAt: '2026-11-04T00:00:00', createdAt: '2026-11-03T00:00:00', publishedAt: '2026-11-03T00:00:00' },
  { id: 2082, kind: 'UPGRADE', content: '恭喜 山海岚 升级为 VIP 4', isVisible: true, startsAt: '2026-11-04T13:00:00', expiresAt: '2026-11-07T13:00:00', createdAt: '2026-11-04T11:50:00', publishedAt: '2026-11-04T11:50:00' },
  { id: 2081, kind: 'LOTTERY_TAG', content: '恭喜 岚** 抽中「SVIP 专属 TAG」', isVisible: true, startsAt: '2026-11-04T10:00:00', expiresAt: '2026-11-05T10:00:00', createdAt: '2026-11-04T10:00:00', publishedAt: null },
  { id: 2080, kind: 'LOTTERY_NAMING', content: '恭喜 山海岚 抽中「星野专属冠名」', isVisible: false, startsAt: '2026-11-03T16:40:00', expiresAt: '2026-11-06T16:40:00', createdAt: '2026-11-03T16:40:00', publishedAt: '2026-11-03T16:40:00' },
  { id: 2079, kind: 'LOTTERY', content: '恭喜 岚** 抽中「星河礼赞」', isVisible: true, startsAt: '2026-11-03T15:20:00', expiresAt: '2026-11-06T15:20:00', createdAt: '2026-11-03T15:20:00', publishedAt: '2026-11-03T15:20:00' },
]
