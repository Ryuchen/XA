export type StoreProduct = 'boss' | 'provider' | 'admin'
export type StoreStatus = 'ACTIVE' | 'PROVISIONING' | 'DISABLED'

export interface StoreInfo {
  slug: string
  name: string
  shortName: string
  city: string
  status: StoreStatus
  databaseAlias: string
  boss: {
    accountId: string
    nickname: string
    memberName: string
    balance: string
    frozen: string
    points: number
  }
  provider: {
    accountId: string
    nickname: string
    balance: string
    frozen: string
    monthIncome: string
    todayOrders: string
    completion: string
    rating: string
    points: number
  }
  metrics: {
    todayOrders: string
    waiting: string
    inService: string
    net: string
    accounts: string
    bosses: string
    providers: string
  }
  announcement: string
  activity: string
}

export const prototypeStores: StoreInfo[] = [
  {
    slug: 'shenglang',
    name: '声浪电竞 · 星海店',
    shortName: '声浪星海',
    city: '星海',
    status: 'ACTIVE',
    databaseAlias: 'xa_store_shenglang',
    boss: { accountId: '10241', nickname: '海浪收藏家', memberName: '星海 VIP 2', balance: '326', frozen: '20', points: 1280 },
    provider: { accountId: '2086', nickname: '岚汐', balance: '826', frozen: '80', monthIncome: '1,286', todayOrders: '6', completion: '96', rating: '4.98', points: 1680 },
    metrics: { todayOrders: '36', waiting: '8', inService: '12', net: '128,460', accounts: '12,680', bosses: '11,942', providers: '738' },
    announcement: '11 月星海店俱乐部活动与下单须知已更新',
    activity: '星海店 · 双排默契计划',
  },
  {
    slug: 'xingchao',
    name: '星潮电竞 · 湖滨店',
    shortName: '星潮湖滨',
    city: '湖滨',
    status: 'ACTIVE',
    databaseAlias: 'xa_store_xingchao',
    boss: { accountId: '73018', nickname: '湖畔听风', memberName: '星潮 VIP 1', balance: '80', frozen: '0', points: 320 },
    provider: { accountId: '6102', nickname: '乔雾', balance: '218', frozen: '36', monthIncome: '642', todayOrders: '3', completion: '92', rating: '4.86', points: 460 },
    metrics: { todayOrders: '18', waiting: '4', inService: '6', net: '46,280', accounts: '3,206', bosses: '3,024', providers: '182' },
    announcement: '湖滨店周末开黑节报名与到店权益说明',
    activity: '湖滨店 · 周末开黑节',
  },
  {
    slug: 'wudao',
    name: '雾岛电竞 · 云城店',
    shortName: '雾岛云城',
    city: '云城',
    status: 'ACTIVE',
    databaseAlias: 'xa_store_wudao',
    boss: { accountId: '9056', nickname: '云上玩家', memberName: '雾岛会员', balance: '0', frozen: '0', points: 60 },
    provider: { accountId: '3091', nickname: '新羽', balance: '96', frozen: '0', monthIncome: '188', todayOrders: '1', completion: '100', rating: '5.00', points: 120 },
    metrics: { todayOrders: '7', waiting: '2', inService: '3', net: '8,960', accounts: '486', bosses: '421', providers: '65' },
    announcement: '云城店新客服务指南与试音招募已发布',
    activity: '云城店 · 新客同行周',
  },
]

export const defaultStoreSlug = 'shenglang'

export function storeEntryUrl(store: StoreInfo, product: StoreProduct) {
  const url = new URL(window.location.href)
  url.search = ''
  url.searchParams.set('store', store.slug)
  url.hash = product === 'boss' ? '/login' : product === 'provider' ? '/provider-login' : '/admin-login'
  return url.href
}
