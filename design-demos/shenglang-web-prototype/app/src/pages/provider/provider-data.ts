export type ProviderScenario = 'default' | 'loading' | 'empty' | 'error'
export type ProviderOrderStatus = 'PENDING' | 'GRABBED' | 'IN_SERVICE' | 'COMPLETED' | 'CANCELLED'
export type ProviderOrderFilter = 'pending' | 'all' | 'grabbed' | 'in_progress' | 'completed' | 'cancelled'

export interface ProviderOrder {
  id: number
  orderNo: string
  service: string
  game: string
  boss: string
  quantity: string
  income: number
  status: ProviderOrderStatus
  time: string
  target: string
  region: string
  identity: string
  team?: boolean
  teamWaiting?: boolean
  settlement?: string
  aftersale?: string
}

export const providerOrders: ProviderOrder[] = [
  { id: 101, orderNo: 'XA202611040101', service: '王者峡谷 · 技术陪练', game: '王者荣耀', boss: '星野', quantity: '5 局', income: 88, status: 'PENDING', time: '今天 20:30 开始', target: '全程沟通，完成 5 局对局', region: '微信区', identity: '荣耀王者 62 星' },
  { id: 102, orderNo: 'XA202611040102', service: '三角洲行动 · 双人护航', game: '三角洲行动', boss: '北岛', quantity: '2 小时', income: 156, status: 'PENDING', time: '今天 22:00 开始', target: '双人组队，老板确认后成单', region: 'PC · 国服', identity: '绝密难度', team: true },
  { id: 103, orderNo: 'XA202611040103', service: '无畏契约 · 排位上分', game: '无畏契约', boss: '青柠', quantity: '3 小时', income: 126, status: 'GRABBED', time: '今天 19:30–22:30', target: '钻石段位，保持积极沟通', region: '国服', identity: '神话 I' },
  { id: 104, orderNo: 'XA202611040104', service: '英雄联盟 · 灵活组排', game: '英雄联盟', boss: '晚风', quantity: '4 局', income: 96, status: 'IN_SERVICE', time: '今天 18:00–20:00', target: '进行中 · 已完成 2 / 4 局', region: '艾欧尼亚', identity: '超凡大师' },
  { id: 105, orderNo: 'XA202611040105', service: '金铲铲之战 · 双人陪玩', game: '金铲铲之战', boss: '梨子', quantity: '2 小时', income: 72, status: 'GRABBED', time: '明天 14:00–16:00', target: '等待队友确认，暂不可开始', region: 'QQ 区', identity: '双人模式', team: true, teamWaiting: true },
  { id: 106, orderNo: 'XA202611030096', service: '王者荣耀 · 娱乐陪玩', game: '王者荣耀', boss: '小满', quantity: '5 局', income: 80, status: 'COMPLETED', time: '昨天 21:00 完成', target: '5 局对局已完成', region: '微信区', identity: '最强王者', settlement: '已结算 · 钱包流水可核对' },
  { id: 107, orderNo: 'XA202611020081', service: '无畏契约 · 娱乐匹配', game: '无畏契约', boss: '临川', quantity: '2 小时', income: 56, status: 'CANCELLED', time: '11 月 2 日取消', target: '订单已取消，无需履约', region: '国服', identity: '不限段位', settlement: '未产生服务收益', aftersale: '老板取消 · 无需处理' },
]

export const providerScenarioOptions = [
  { value: 'default', label: '正常数据', description: '查看完整业务内容与可用操作' },
  { value: 'loading', label: '加载中', description: '检查信息骨架与读取反馈' },
  { value: 'empty', label: '空状态', description: '检查无内容时的下一步引导' },
  { value: 'error', label: '加载失败', description: '检查错误说明与重试入口' },
] as const

export const statusLabels: Record<ProviderOrderStatus, string> = {
  PENDING: '可抢单', GRABBED: '已接单', IN_SERVICE: '服务中', COMPLETED: '已完成', CANCELLED: '已取消',
}

export const orderFilters: { value: ProviderOrderFilter; label: string }[] = [
  { value: 'pending', label: '抢单池' }, { value: 'all', label: '我的订单' }, { value: 'grabbed', label: '已接单' },
  { value: 'in_progress', label: '服务中' }, { value: 'completed', label: '已完成' }, { value: 'cancelled', label: '已取消' },
]

export const providerReports = [
  { id: 1, orderNo: 'XA202611030096', service: '王者荣耀 · 娱乐陪玩', status: '待提交', income: 80, remark: '还需补充战绩截图后提交审核。', evidence: { entry: true, completion: true, result: false }, editable: true },
  { id: 2, orderNo: 'XA202611020073', service: '英雄联盟 · 灵活组排', status: '审核中', income: 96, remark: '已于 11 月 3 日 00:18 提交，等待平台审核。', evidence: { entry: true, completion: true, result: true }, editable: false },
  { id: 3, orderNo: 'XA202611010052', service: '无畏契约 · 排位上分', status: '已通过', income: 126, remark: '凭证审核通过，收益已结算至钱包。', evidence: { entry: true, completion: true, result: true }, editable: false },
]

export const providerMessages = [
  { id: 1, type: '订单消息', title: '服务即将开始', text: '订单 XA202611040103 将于 19:30 开始，请提前准备入队截图。', time: '18:42', unread: true, target: 'orders' },
  { id: 2, type: '组队消息', title: '收到组队邀请', text: '金铲铲双人陪玩订单邀请你组队，请核对队友与服务时间后确认。', time: '17:26', unread: true, target: 'applications' },
  { id: 3, type: '资金消息', title: '报单收益已结算', text: '报单 XA202611010052 的 126 浪花已计入钱包，请查看资金流水。', time: '昨天', unread: false, target: 'wallet' },
  { id: 4, type: '系统通知', title: '资料审核已通过', text: '你的最新游戏段位资料已通过审核，现已同步至个人档案。', time: '11 月 2 日', unread: false, target: 'profile' },
]
