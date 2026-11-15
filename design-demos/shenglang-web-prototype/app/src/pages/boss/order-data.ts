export type OrderStatus = 'PENDING' | 'GRABBED' | 'IN_SERVICE' | 'COMPLETED' | 'CANCELLED'

export interface BossOrder {
  id: number
  orderNo: string
  service: string
  game: string
  status: OrderStatus
  statusText: string
  provider: string
  quantity: number
  unit: string
  amount: number
  amountLabel?: string
  created: string
  progress: string
  deadline?: string
  refund?: string
  gift?: boolean
  evaluate?: boolean
  aftersale?: boolean
  paymentUnconfirmed?: boolean
}

export const bossOrders: BossOrder[] = [
  {
    id: 1038,
    orderNo: 'SL202611031038',
    service: '王者娱乐双排',
    game: '王者荣耀',
    status: 'PENDING',
    statusText: '待接单',
    provider: '已收到 3 份报名',
    quantity: 2,
    unit: '份',
    amount: 41,
    created: '2026-11-03 18:32',
    progress: '已收到报名，查看详情',
    deadline: '接单截止 19:02 · 到期未安排将自动取消并退回余额',
  },
  {
    id: 1036,
    orderNo: 'SL202611031036',
    service: '峡谷默契双排',
    game: '王者荣耀',
    status: 'GRABBED',
    statusText: '已接单',
    provider: '小澈',
    quantity: 1,
    unit: '份',
    amount: 68,
    created: '2026-11-03 17:18',
    progress: '陪玩已接单，等待约定时间开局',
  },
  {
    id: 1031,
    orderNo: 'SL202611031031',
    service: '峡谷技术提升',
    game: '王者荣耀',
    status: 'IN_SERVICE',
    statusText: '服务中',
    provider: '阿屿',
    quantity: 2,
    unit: '小时',
    amount: 96,
    created: '2026-11-03 15:40',
    progress: '服务进行中 · 已完成 1 / 2 小时',
  },
  {
    id: 1024,
    orderNo: 'SL202611021024',
    service: '王者双陪欢乐局',
    game: '王者荣耀',
    status: 'COMPLETED',
    statusText: '已完成',
    provider: '小澈、北夜',
    quantity: 2,
    unit: '份',
    amount: 136,
    created: '2026-11-02 21:06',
    progress: '待评价 · 已评 1 / 2 人',
    evaluate: true,
  },
  {
    id: 1019,
    orderNo: 'SL202611021019',
    service: '王者娱乐双排',
    game: '王者荣耀',
    status: 'CANCELLED',
    statusText: '已取消',
    provider: '未安排陪玩',
    quantity: 1,
    unit: '份',
    amount: 49,
    amountLabel: '原实付',
    created: '2026-11-02 19:24',
    progress: '订单已取消，退款已入账',
    refund: '累计已退 49 浪花 · 剩余 0 浪花',
  },
  {
    id: 1012,
    orderNo: 'SL202611011012',
    service: '星海守护者 · 专属冠名',
    game: '声浪礼物',
    status: 'COMPLETED',
    statusText: '礼物已送达',
    provider: '赠送给小澈',
    quantity: 1,
    unit: '份',
    amount: 52,
    created: '2026-11-01 20:16',
    progress: '查看赠礼记录',
    gift: true,
  },
]

export const paymentPendingOrder: BossOrder = {
  ...bossOrders[0],
  id: 1040,
  orderNo: 'SL202611031040',
  statusText: '支付待确认',
  provider: '付款状态待确认',
  amountLabel: '订单金额',
  progress: '付款状态尚未确认，暂不展示派单进度',
  deadline: undefined,
  paymentUnconfirmed: true,
}

export const aftersaleOrder: BossOrder = {
  ...bossOrders[2],
  progress: '售后处理中 · 实销暂不确认',
  aftersale: true,
}
