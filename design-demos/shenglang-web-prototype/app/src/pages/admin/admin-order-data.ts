export type AdminOrderStatus = 'PENDING' | 'GRABBED' | 'IN_SERVICE' | 'COMPLETED' | 'CANCELLED'
export type AdminPaymentStatus = 'UNPAID' | 'PAID' | 'PARTIALLY_REFUNDED' | 'REFUNDED'

export interface AdminOrder {
  id: number
  orderNo: string
  customerName: string
  customerUsername: string
  customerPhone: string
  providerNames: string[]
  serviceName: string
  amount: number
  originalAmount: number
  unitPrice: number
  rounds: number
  discount: number
  status: AdminOrderStatus
  paymentStatus: AdminPaymentStatus
  route: 'STAFF' | 'PUBLIC'
  support: string
  createdAt: string
  deadline?: string
  teamSize: 1 | 2
  gender: '不限' | '男陪' | '女陪'
  gameRegion: string
  gameNickname: string
  gameUid: string
  commissionRate: number
  providerIncome: number
  shopIncome: number
  inviter: string
  inviterCommission: number
  settlementStatus: 'WAITING' | 'SETTLED' | 'REVIEW'
  remark: string
  aftersale?: { id: number; reason: string; createdAt: string }
  refundedAmount?: number
  recoveredAmount?: number
}

export const adminOrders: AdminOrder[] = [
  {
    id: 3601, orderNo: 'SL202611040036', customerName: '海边听风', customerUsername: 'boss_sea', customerPhone: '138****2601',
    providerNames: [], serviceName: '英雄联盟 · 双排 2 局', amount: 136, originalAmount: 152, unitPrice: 76, rounds: 2, discount: 16,
    status: 'PENDING', paymentStatus: 'PAID', route: 'STAFF', support: '客服小岚', createdAt: '2026-11-04 16:42', deadline: '2026-11-04 17:12', teamSize: 1, gender: '女陪',
    gameRegion: '艾欧尼亚', gameNickname: '听风入海', gameUid: 'LOL-829361', commissionRate: 20, providerIncome: 108.8, shopIncome: 27.2, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '希望温柔耐心，尽量连麦。',
  },
  {
    id: 3602, orderNo: 'SL202611040035', customerName: '北纬二十七', customerUsername: 'north_27', customerPhone: '未记录',
    providerNames: [], serviceName: '无畏契约 · 体验 1 局', amount: 68, originalAmount: 68, unitPrice: 68, rounds: 1, discount: 0,
    status: 'PENDING', paymentStatus: 'UNPAID', route: 'PUBLIC', support: '客服小岚', createdAt: '2026-11-04 16:35', teamSize: 1, gender: '不限',
    gameRegion: '国服', gameNickname: '北纬', gameUid: 'VAL-27018', commissionRate: 18, providerIncome: 55.8, shopIncome: 12.2, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '—',
  },
  {
    id: 3603, orderNo: 'SL202611040034', customerName: '一杯美式', customerUsername: 'coffee_daily', customerPhone: '186****1077',
    providerNames: ['阿澈', '可可'], serviceName: '王者荣耀 · 双陪 2 局', amount: 216, originalAmount: 216, unitPrice: 108, rounds: 2, discount: 0,
    status: 'IN_SERVICE', paymentStatus: 'PAID', route: 'STAFF', support: '客服阿澄', createdAt: '2026-11-04 16:18', teamSize: 2, gender: '不限',
    gameRegion: '微信区', gameNickname: '美式不加糖', gameUid: 'WZ-663091', commissionRate: 22, providerIncome: 168.5, shopIncome: 47.5, inviter: '老徐', inviterCommission: 4, settlementStatus: 'WAITING', remark: '双陪分别核对入队与结单凭证。',
  },
  {
    id: 3604, orderNo: 'SL202611040033', customerName: '星河入梦', customerUsername: 'star_dream', customerPhone: '135****8082',
    providerNames: ['奈奈'], serviceName: '礼物单 · 星光岛屿', amount: 52, originalAmount: 52, unitPrice: 52, rounds: 1, discount: 0,
    status: 'COMPLETED', paymentStatus: 'PAID', route: 'PUBLIC', support: '客服小岚', createdAt: '2026-11-04 15:56', teamSize: 1, gender: '女陪',
    gameRegion: '未填写', gameNickname: '未填写', gameUid: '未填写', commissionRate: 15, providerIncome: 44.2, shopIncome: 7.8, inviter: '—', inviterCommission: 0, settlementStatus: 'SETTLED', remark: '附言：今天也要开心。',
  },
  {
    id: 3605, orderNo: 'SL202611040032', customerName: '橘子海', customerUsername: 'orange_sea', customerPhone: '159****4418',
    providerNames: ['小鹿'], serviceName: '金铲铲之战 · 双人 1 局', amount: 96, originalAmount: 108, unitPrice: 108, rounds: 1, discount: 12,
    status: 'GRABBED', paymentStatus: 'PAID', route: 'STAFF', support: '客服阿澄', createdAt: '2026-11-04 15:41', teamSize: 1, gender: '女陪',
    gameRegion: '微信区', gameNickname: '橘子海', gameUid: 'TFT-51082', commissionRate: 20, providerIncome: 76.8, shopIncome: 19.2, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '已确认 17:00 开局。',
  },
  {
    id: 3606, orderNo: 'SL202611040031', customerName: '山间来客', customerUsername: 'mountain_guest', customerPhone: '177****3206',
    providerNames: ['阿澈'], serviceName: '英雄联盟 · 进阶 3 局', amount: 180, originalAmount: 198, unitPrice: 66, rounds: 3, discount: 18,
    status: 'COMPLETED', paymentStatus: 'PARTIALLY_REFUNDED', route: 'PUBLIC', support: '客服小岚', createdAt: '2026-11-04 15:22', teamSize: 1, gender: '男陪',
    gameRegion: '黑色玫瑰', gameNickname: '来客', gameUid: 'LOL-39017', commissionRate: 20, providerIncome: 144, shopIncome: 36, inviter: '—', inviterCommission: 0, settlementStatus: 'SETTLED', remark: '其中一局未完整履约，已部分退款。', refundedAmount: 60, recoveredAmount: 48,
    aftersale: { id: 208, reason: '其中一局未能完整履约，申请退还对应费用。', createdAt: '2026-11-04 16:02' },
  },
  {
    id: 3607, orderNo: 'SL202611040030', customerName: '雾岛晴空', customerUsername: 'mist_sky', customerPhone: '132****9071',
    providerNames: [], serviceName: '和平精英 · 双排 2 局', amount: 88, originalAmount: 88, unitPrice: 44, rounds: 2, discount: 0,
    status: 'CANCELLED', paymentStatus: 'REFUNDED', route: 'STAFF', support: '客服阿澄', createdAt: '2026-11-04 14:58', teamSize: 1, gender: '不限',
    gameRegion: '微信区', gameNickname: '雾岛', gameUid: 'PE-77201', commissionRate: 18, providerIncome: 72.2, shopIncome: 15.8, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '老板临时有事，接单前取消。', refundedAmount: 88, recoveredAmount: 0,
  },
  {
    id: 3608, orderNo: 'SL202611040029', customerName: '晚风电台', customerUsername: 'night_radio', customerPhone: '150****1169',
    providerNames: [], serviceName: '永劫无间 · 三排 2 局', amount: 148, originalAmount: 148, unitPrice: 74, rounds: 2, discount: 0,
    status: 'PENDING', paymentStatus: 'PAID', route: 'PUBLIC', support: '客服小岚', createdAt: '2026-11-04 14:46', deadline: '2026-11-04 17:16', teamSize: 1, gender: '男陪',
    gameRegion: '国服', gameNickname: '晚风', gameUid: 'NB-62014', commissionRate: 20, providerIncome: 118.4, shopIncome: 29.6, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '公开接单，由老板选择报名陪玩。',
  },
  {
    id: 3609, orderNo: 'ORD6B21A90C4F7302D8E110', customerName: '海边听风', customerUsername: 'boss_sea', customerPhone: '138****2601',
    providerNames: ['小鹿'], serviceName: '英雄联盟 · 双排 2 局', amount: 136, originalAmount: 152, unitPrice: 76, rounds: 2, discount: 16,
    status: 'GRABBED', paymentStatus: 'PAID', route: 'STAFF', support: '客服小岚', createdAt: '2026-11-04 16:40', teamSize: 1, gender: '女陪',
    gameRegion: '艾欧尼亚', gameNickname: '听风入海', gameUid: 'LOL-829361', commissionRate: 20, providerIncome: 108.8, shopIncome: 27.2, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '已确认 20:00 开局，如需换区提前联系老板。',
  },
  {
    id: 3610, orderNo: 'SL202611040037', customerName: '一杯美式', customerUsername: 'coffee_daily', customerPhone: '186****1077',
    providerNames: ['小鹿', '阿澈'], serviceName: '王者荣耀 · 双陪 2 局', amount: 216, originalAmount: 216, unitPrice: 108, rounds: 2, discount: 0,
    status: 'COMPLETED', paymentStatus: 'PAID', route: 'STAFF', support: '客服小岚', createdAt: '2026-11-04 14:36', teamSize: 2, gender: '不限',
    gameRegion: '微信区', gameNickname: '美式不加糖', gameUid: 'WZ-663091', commissionRate: 20, providerIncome: 172.8, shopIncome: 43.2, inviter: '—', inviterCommission: 0, settlementStatus: 'WAITING', remark: '双方分别提交报单，全部审核通过且售后窗口结束后结算。',
  },
]

export const adminOrderStatusLabel: Record<AdminOrderStatus, string> = {
  PENDING: '待接单', GRABBED: '已接单', IN_SERVICE: '服务中', COMPLETED: '已完成', CANCELLED: '已取消',
}

export const adminPaymentStatusLabel: Record<AdminPaymentStatus, string> = {
  UNPAID: '未支付', PAID: '已支付', PARTIALLY_REFUNDED: '部分退款', REFUNDED: '已退款',
}
