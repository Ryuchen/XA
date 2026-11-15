import type { StoreInfo } from '@/components/store/store-data'

export type AdminBossStatus = 'ACTIVE' | 'DISABLED'

export interface AdminBoss {
  id: string
  nickname: string
  username: string
  phone: string
  memberName: string
  status: AdminBossStatus
  wechatBound: boolean
  otpBound: boolean
  balance: string
  points: number
  totalSpend: number
  orderCount: number
  joinedAt: string
  lastActive: string
  recentDynamics: string[]
}

const storePrefixes: Record<string, string> = {
  shenglang: 'SL',
  xingchao: 'XC',
  wudao: 'WD',
}

const storeSpend: Record<string, number> = {
  shenglang: 2680,
  xingchao: 680,
  wudao: 0,
}

export function adminBossesForStore(store: StoreInfo): AdminBoss[] {
  const prefix = storePrefixes[store.slug] ?? store.slug.slice(0, 2).toUpperCase()
  const primary: AdminBoss = {
    id: store.boss.accountId,
    nickname: store.boss.nickname,
    username: `boss_${store.slug}`,
    phone: store.slug === 'wudao' ? '未记录' : '138****2601',
    memberName: store.boss.memberName,
    status: 'ACTIVE',
    wechatBound: true,
    otpBound: store.slug !== 'wudao',
    balance: store.boss.balance,
    points: store.boss.points,
    totalSpend: storeSpend[store.slug] ?? 0,
    orderCount: store.slug === 'shenglang' ? 28 : store.slug === 'xingchao' ? 6 : 0,
    joinedAt: '2026-09-18 10:26',
    lastActive: '2026-11-05 12:42',
    recentDynamics: [
      `完成本店订单并累计 ${store.boss.points} 积分`,
      `会员身份已更新为「${store.boss.memberName}」`,
      `关注本店活动「${store.activity}」`,
    ],
  }

  const examples: Array<Omit<AdminBoss, 'id' | 'recentDynamics'>> = [
    { nickname: '海边听风', username: 'boss_sea', phone: '138****2601', memberName: '星海 VIP 2', status: 'ACTIVE', wechatBound: true, otpBound: true, balance: '186.00', points: 860, totalSpend: 1680, orderCount: 18, joinedAt: '2026-08-12 14:08', lastActive: '2026-11-05 11:36' },
    { nickname: '一杯美式', username: 'coffee_daily', phone: '186****1077', memberName: '星海 VIP 1', status: 'ACTIVE', wechatBound: true, otpBound: false, balance: '96.00', points: 420, totalSpend: 864, orderCount: 9, joinedAt: '2026-09-26 19:42', lastActive: '2026-11-04 22:18' },
    { nickname: '北纬二十七', username: 'north_27', phone: '未记录', memberName: '普通会员', status: 'ACTIVE', wechatBound: true, otpBound: false, balance: '0.00', points: 60, totalSpend: 68, orderCount: 1, joinedAt: '2026-11-03 16:20', lastActive: '2026-11-04 16:35' },
    { nickname: '雾岛晴空', username: 'mist_sky', phone: '132****9071', memberName: '普通会员', status: 'DISABLED', wechatBound: false, otpBound: false, balance: '0.00', points: 0, totalSpend: 176, orderCount: 2, joinedAt: '2026-07-08 09:16', lastActive: '2026-10-28 20:06' },
  ]

  return [primary, ...examples.map((boss, index) => ({
    ...boss,
    id: `${prefix}${String(1002 + index)}`,
    memberName: boss.memberName.replace('星海', store.city),
    recentDynamics: [
      `${boss.lastActive.slice(0, 10)} 登录${store.shortName}老板端`,
      boss.orderCount > 0 ? `本店累计 ${boss.orderCount} 笔订单，消费记录仅用于店内核对` : '尚未在本店产生订单',
      boss.status === 'DISABLED' ? '账号已停用，历史订单与资金记录仍保留' : '账号状态正常',
    ],
  }))]
}

export const adminBossStatusLabel: Record<AdminBossStatus, string> = {
  ACTIVE: '正常',
  DISABLED: '已停用',
}
