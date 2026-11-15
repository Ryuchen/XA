export type AdminReservationStatus = 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'CANCELLED' | 'CONVERTED' | 'EXPIRED'

export interface AdminReservationQuote {
  originalAmount: number
  bossDiscount: number
  promoDiscount: number
  couponDiscount: number
  amount: number
  walletBalance: number
}

export interface AdminReservation {
  id: number
  reservationNo: string
  status: AdminReservationStatus
  customerAccountId: number
  customerName: string
  providerAccountId: number
  providerName: string
  serviceId: number
  serviceName: string
  platform: 'PC' | 'MOBILE'
  securityLevel: 'CONFIDENTIAL' | 'TOP_SECRET' | 'ANY'
  startTime: string
  endTime: string
  durationMinutes: number
  gameRounds: number
  estimatedAmount: number
  gameRegion: string
  gameNickname: string
  gameUid: string
  remark: string
  cancelReason: string
  rejectReason: string
  createdAt: string
  orderNo?: string
  quote: AdminReservationQuote
}

export const adminReservationStatusLabel: Record<AdminReservationStatus, string> = {
  PENDING: '待确认',
  CONFIRMED: '已确认',
  REJECTED: '已拒绝',
  CANCELLED: '已取消',
  CONVERTED: '已转单',
  EXPIRED: '已过期',
}

const standardQuote: AdminReservationQuote = {
  originalAmount: 1520,
  bossDiscount: 100,
  promoDiscount: 40,
  couponDiscount: 0,
  amount: 1360,
  walletBalance: 8620,
}

export const initialAdminReservations: AdminReservation[] = [
  {
    id: 301,
    reservationNo: 'RSV202611030018',
    status: 'CONFIRMED',
    customerAccountId: 101,
    customerName: '海边听风',
    providerAccountId: 201,
    providerName: '小鹿',
    serviceId: 11,
    serviceName: '英雄联盟 · 双排 2 局',
    platform: 'PC',
    securityLevel: 'ANY',
    startTime: '2026-11-04 20:00',
    endTime: '2026-11-04 22:00',
    durationMinutes: 120,
    gameRounds: 2,
    estimatedAmount: 1520,
    gameRegion: '艾欧尼亚',
    gameNickname: '听风入海',
    gameUid: 'LOL-829361',
    remark: '希望温柔耐心，尽量连麦。',
    cancelReason: '',
    rejectReason: '',
    createdAt: '2026-11-03 18:26',
    quote: standardQuote,
  },
  {
    id: 298,
    reservationNo: 'RSV202611030015',
    status: 'PENDING',
    customerAccountId: 102,
    customerName: '北纬二十七',
    providerAccountId: 202,
    providerName: '北夜',
    serviceId: 12,
    serviceName: '英雄联盟 · 峡谷同行',
    platform: 'PC',
    securityLevel: 'CONFIDENTIAL',
    startTime: '2026-11-05 19:30',
    endTime: '2026-11-05 21:00',
    durationMinutes: 90,
    gameRounds: 1,
    estimatedAmount: 680,
    gameRegion: '黑色玫瑰',
    gameNickname: '北纬',
    gameUid: 'LOL-27018',
    remark: '请提前 10 分钟联系。',
    cancelReason: '',
    rejectReason: '',
    createdAt: '2026-11-03 17:42',
    quote: { ...standardQuote, originalAmount: 680, bossDiscount: 0, promoDiscount: 0, amount: 680 },
  },
  {
    id: 295,
    reservationNo: 'RSV202611030011',
    status: 'CONFIRMED',
    customerAccountId: 103,
    customerName: '一杯美式',
    providerAccountId: 203,
    providerName: '阿澈',
    serviceId: 13,
    serviceName: '无畏契约 · 体验 1 局',
    platform: 'PC',
    securityLevel: 'TOP_SECRET',
    startTime: '2026-11-05 18:00',
    endTime: '2026-11-05 19:00',
    durationMinutes: 60,
    gameRounds: 1,
    estimatedAmount: 680,
    gameRegion: '国服',
    gameNickname: '美式不加糖',
    gameUid: 'VAL-663091',
    remark: '',
    cancelReason: '',
    rejectReason: '',
    createdAt: '2026-11-03 15:08',
    quote: { ...standardQuote, originalAmount: 680, bossDiscount: 0, promoDiscount: 0, amount: 680 },
  },
  {
    id: 287,
    reservationNo: 'RSV202611020096',
    status: 'CONVERTED',
    customerAccountId: 104,
    customerName: '山间来客',
    providerAccountId: 203,
    providerName: '阿澈',
    serviceId: 14,
    serviceName: '英雄联盟 · 进阶 3 局',
    platform: 'PC',
    securityLevel: 'ANY',
    startTime: '2026-11-03 22:00',
    endTime: '2026-11-04 00:00',
    durationMinutes: 120,
    gameRounds: 3,
    estimatedAmount: 1980,
    gameRegion: '黑色玫瑰',
    gameNickname: '来客',
    gameUid: 'LOL-39017',
    remark: '转单后按正式订单履约。',
    cancelReason: '',
    rejectReason: '',
    createdAt: '2026-11-02 12:16',
    orderNo: 'SL202611040031',
    quote: { ...standardQuote, originalAmount: 1980, bossDiscount: 180, promoDiscount: 0, amount: 1800 },
  },
  {
    id: 276,
    reservationNo: 'RSV202611020081',
    status: 'REJECTED',
    customerAccountId: 105,
    customerName: '橘子海',
    providerAccountId: 204,
    providerName: '桃乐丝',
    serviceId: 15,
    serviceName: '和平精英 · 默契双排',
    platform: 'MOBILE',
    securityLevel: 'ANY',
    startTime: '2026-11-04 16:00',
    endTime: '2026-11-04 17:00',
    durationMinutes: 60,
    gameRounds: 1,
    estimatedAmount: 580,
    gameRegion: '微信区',
    gameNickname: '橘子海',
    gameUid: 'PE-51082',
    remark: '',
    cancelReason: '',
    rejectReason: '临时档期冲突，已告知老板重新选择时间。',
    createdAt: '2026-11-02 10:32',
    quote: { ...standardQuote, originalAmount: 580, bossDiscount: 0, promoDiscount: 0, amount: 580 },
  },
  {
    id: 264,
    reservationNo: 'RSV202611010063',
    status: 'CANCELLED',
    customerAccountId: 101,
    customerName: '海边听风',
    providerAccountId: 201,
    providerName: '小鹿',
    serviceId: 11,
    serviceName: '英雄联盟 · 双排 2 局',
    platform: 'PC',
    securityLevel: 'ANY',
    startTime: '2026-11-02 21:15',
    endTime: '2026-11-02 22:45',
    durationMinutes: 90,
    gameRounds: 2,
    estimatedAmount: 1520,
    gameRegion: '艾欧尼亚',
    gameNickname: '听风入海',
    gameUid: 'LOL-829361',
    remark: '',
    cancelReason: '老板行程调整，双方已确认取消。',
    rejectReason: '',
    createdAt: '2026-11-01 13:20',
    quote: standardQuote,
  },
  {
    id: 251,
    reservationNo: 'RSV202610310042',
    status: 'EXPIRED',
    customerAccountId: 102,
    customerName: '北纬二十七',
    providerAccountId: 202,
    providerName: '北夜',
    serviceId: 12,
    serviceName: '英雄联盟 · 峡谷同行',
    platform: 'PC',
    securityLevel: 'CONFIDENTIAL',
    startTime: '2026-11-01 18:45',
    endTime: '2026-11-01 20:15',
    durationMinutes: 90,
    gameRounds: 2,
    estimatedAmount: 1360,
    gameRegion: '黑色玫瑰',
    gameNickname: '北纬',
    gameUid: 'LOL-27018',
    remark: '',
    cancelReason: '',
    rejectReason: '',
    createdAt: '2026-10-31 09:18',
    quote: { ...standardQuote, originalAmount: 1360, bossDiscount: 0, promoDiscount: 0, amount: 1360 },
  },
]
