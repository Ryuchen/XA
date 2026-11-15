export type ReservationStatus = 'PENDING' | 'CONFIRMED' | 'CONVERTED' | 'REJECTED' | 'CANCELLED' | 'EXPIRED'

export interface Reservation {
  id: number
  reservationNo: string
  service: string
  provider: string
  providerInitial: string
  rounds: number
  startText: string
  status: ReservationStatus
  statusText: string
  estimatedAmount: number
  orderNo?: string
  endText?: string
  gameRegion?: string
  gameNickname?: string
  gameUid?: string
  remark?: string
  rejectReason?: string
  cancelReason?: string
}

export const bossReservations: Reservation[] = [
  { id: 301, reservationNo: 'YY202611030018', service: '王者双陪欢乐局', provider: '小澈', providerInitial: '澈', rounds: 2, startText: '11 月 4 日 20:00', status: 'CONFIRMED', statusText: '已确认', estimatedAmount: 136 },
  { id: 298, reservationNo: 'YY202611030015', service: '英雄联盟峡谷同行', provider: '北夜', providerInitial: '北', rounds: 1, startText: '11 月 5 日 19:30', status: 'PENDING', statusText: '待陪玩确认', estimatedAmount: 68 },
  { id: 287, reservationNo: 'YY202611020096', service: '王者峡谷上分计划', provider: '阿屿', providerInitial: '屿', rounds: 3, startText: '11 月 3 日 22:00', status: 'CONVERTED', statusText: '已成单', estimatedAmount: 204, orderNo: 'SL202611021024' },
  { id: 276, reservationNo: 'YY202611020081', service: '和平精英默契双排', provider: '桃乐丝', providerInitial: '桃', rounds: 1, startText: '11 月 4 日 16:00', status: 'REJECTED', statusText: '已拒绝', estimatedAmount: 58 },
  { id: 264, reservationNo: 'YY202611010063', service: '王者双陪欢乐局', provider: '小澈', providerInitial: '澈', rounds: 1, startText: '11 月 2 日 21:15', status: 'CANCELLED', statusText: '已取消', estimatedAmount: 68 },
  { id: 251, reservationNo: 'YY202610310042', service: '英雄联盟峡谷同行', provider: '北夜', providerInitial: '北', rounds: 2, startText: '11 月 1 日 18:45', status: 'EXPIRED', statusText: '已过期', estimatedAmount: 136 },
]
