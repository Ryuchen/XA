export type AdminWalletKind = 'BOSS' | 'PROVIDER'

export interface AdminWalletTransaction {
  id: string
  type: 'RECHARGE' | 'GIFT' | 'ADJUST' | 'ORDER' | 'REFUND' | 'WITHDRAW'
  amount: number
  balanceAfter: number
  remark: string
  reference: string
  operator: string
  createdAt: string
}

export interface AdminRechargeRecord {
  id: string
  paidAmount: number
  giftAmount: number
  creditedAmount: number
  reference: string
  voucherName: string
  operator: string
  createdAt: string
}

export interface AdminWallet {
  id: number
  walletNo: string
  kind: AdminWalletKind
  username: string
  nickname: string
  phone: string
  bossLevel: string | null
  balance: number
  frozen: number
  totalRecharge: number
  totalGift: number
  enabled: boolean
  updatedAt: string
  transactions: AdminWalletTransaction[]
  recharges: AdminRechargeRecord[]
}

export interface AdminSubsidyRecord {
  id: string
  amount: number
  reference: string
  remark: string
  operator: string
  createdAt: string
}

export interface AdminPendingOrder {
  id: number
  orderNo: string
  serviceName: string
  status: 'COMPLETED' | 'IN_SERVICE' | 'CANCELLED'
  amount: number
}

export const transactionTypeLabel: Record<AdminWalletTransaction['type'], string> = {
  RECHARGE: '客服实充',
  GIFT: '充值赠送',
  ADJUST: '人工调账',
  ORDER: '订单支出',
  REFUND: '订单退款',
  WITHDRAW: '收益提现',
}

function records(walletNo: string, balance: number, variant = 0): Pick<AdminWallet, 'transactions' | 'recharges'> {
  const base = balance + 12000 + variant * 1000
  return {
    transactions: [
      { id: `${walletNo}-T03`, type: 'ORDER', amount: -6800, balanceAfter: balance, remark: '王者荣耀技术服务订单', reference: `DEMO-ORDER-${walletNo}-03`, operator: '系统', createdAt: '2026-11-04 10:26' },
      { id: `${walletNo}-T02`, type: 'GIFT', amount: 2000, balanceAfter: base - 12000, remark: '客服充值赠送', reference: `DEMO-PAY-${walletNo}-02`, operator: '客服岚', createdAt: '2026-11-03 16:42' },
      { id: `${walletNo}-T01`, type: 'RECHARGE', amount: 20000, balanceAfter: base - 32000, remark: '线下收款后客服实充', reference: `DEMO-PAY-${walletNo}-02`, operator: '客服岚', createdAt: '2026-11-03 16:42' },
    ],
    recharges: [
      { id: `${walletNo}-R02`, paidAmount: 20000, giftAmount: 2000, creditedAmount: 22000, reference: `DEMO-PAY-${walletNo}-02`, voucherName: 'demo-receipt-1103.png', operator: '客服岚', createdAt: '2026-11-03 16:42' },
      { id: `${walletNo}-R01`, paidAmount: 10000, giftAmount: 0, creditedAmount: 10000, reference: `DEMO-PAY-${walletNo}-01`, voucherName: 'demo-receipt-1031.png', operator: '运营管理员', createdAt: '2026-10-31 14:08' },
    ],
  }
}

export const initialAdminWallets: AdminWallet[] = [
  { id: 1001, walletNo: 'W-Boss-1001', kind: 'BOSS', username: 'boss_chen', nickname: '陈默', phone: '13800001001', bossLevel: '星海老板', balance: 86200, frozen: 6800, totalRecharge: 128000, totalGift: 12000, enabled: true, updatedAt: '2026-11-04 10:26', ...records('1001', 86200) },
  { id: 1002, walletNo: 'W-Boss-1002', kind: 'BOSS', username: 'boss_luo', nickname: '洛川', phone: '13800001002', bossLevel: '逐浪老板', balance: 43800, frozen: 0, totalRecharge: 60000, totalGift: 4000, enabled: true, updatedAt: '2026-11-04 09:48', ...records('1002', 43800, 1) },
  { id: 1003, walletNo: 'W-Boss-1003', kind: 'BOSS', username: 'boss_xu', nickname: '许星河', phone: '13800001003', bossLevel: '听澜老板', balance: 18600, frozen: 3200, totalRecharge: 30000, totalGift: 2000, enabled: true, updatedAt: '2026-11-03 22:16', ...records('1003', 18600, 2) },
  { id: 1004, walletNo: 'W-Boss-1004', kind: 'BOSS', username: 'boss_zhou', nickname: '周眠', phone: '13800001004', bossLevel: '初识老板', balance: 5600, frozen: 0, totalRecharge: 10000, totalGift: 0, enabled: true, updatedAt: '2026-11-03 19:30', ...records('1004', 5600, 3) },
  { id: 1005, walletNo: 'W-Boss-1005', kind: 'BOSS', username: 'boss_legacy', nickname: '历史停用账户', phone: '13800001005', bossLevel: '初识老板', balance: 1200, frozen: 0, totalRecharge: 5000, totalGift: 0, enabled: false, updatedAt: '2026-10-28 11:20', ...records('1005', 1200, 4) },
  { id: 2001, walletNo: 'W-Provider-2001', kind: 'PROVIDER', username: 'provider_lan', nickname: '小岚', phone: '13900002001', bossLevel: null, balance: 52400, frozen: 28800, totalRecharge: 20000, totalGift: 2000, enabled: true, updatedAt: '2026-11-04 10:08', ...records('2001', 52400) },
  { id: 2002, walletNo: 'W-Provider-2002', kind: 'PROVIDER', username: 'provider_qinghe', nickname: '青禾', phone: '13900002002', bossLevel: null, balance: 36800, frozen: 45000, totalRecharge: 10000, totalGift: 0, enabled: true, updatedAt: '2026-11-04 09:32', ...records('2002', 36800, 1) },
  { id: 2003, walletNo: 'W-Provider-2003', kind: 'PROVIDER', username: 'provider_beidao', nickname: '北岛', phone: '13900002003', bossLevel: null, balance: 21600, frozen: 18000, totalRecharge: 0, totalGift: 0, enabled: true, updatedAt: '2026-11-04 08:20', ...records('2003', 21600, 2) },
  { id: 2004, walletNo: 'W-Provider-2004', kind: 'PROVIDER', username: 'provider_legacy', nickname: '旧版陪玩账户', phone: '13900002004', bossLevel: null, balance: 900, frozen: 0, totalRecharge: 0, totalGift: 0, enabled: false, updatedAt: '2026-10-26 15:42', ...records('2004', 900, 3) },
]

export const initialPlatformFunds = {
  balance: 860000,
  providerDeposits: 128000,
  subsidyAvailable: 732000,
  unsettled: 184000,
  unsettledOrders: 12,
}

export const initialPendingOrders: AdminPendingOrder[] = [
  { id: 9101, orderNo: 'SL202611040101', serviceName: '王者荣耀 · 技术陪练', status: 'COMPLETED', amount: 16800 },
  { id: 9102, orderNo: 'SL202611040102', serviceName: '和平精英 · 上分陪练', status: 'IN_SERVICE', amount: 22400 },
  { id: 9103, orderNo: 'SL202611040103', serviceName: '英雄联盟 · 峡谷陪练', status: 'CANCELLED', amount: 15600 },
  { id: 9104, orderNo: 'SL202611040104', serviceName: '金铲铲之战 · 双人成行', status: 'COMPLETED', amount: 12000 },
  { id: 9105, orderNo: 'SL202611040105', serviceName: '无畏契约 · 排位陪练', status: 'COMPLETED', amount: 18000 },
  { id: 9106, orderNo: 'SL202611040106', serviceName: '王者荣耀 · 娱乐陪练', status: 'IN_SERVICE', amount: 9600 },
  { id: 9107, orderNo: 'SL202611040107', serviceName: '三角洲行动 · 护航', status: 'COMPLETED', amount: 14400 },
  { id: 9108, orderNo: 'SL202611040108', serviceName: '永劫无间 · 三排陪练', status: 'COMPLETED', amount: 13200 },
  { id: 9109, orderNo: 'SL202611040109', serviceName: '英雄联盟 · 大乱斗', status: 'CANCELLED', amount: 8000 },
  { id: 9110, orderNo: 'SL202611040110', serviceName: '和平精英 · 娱乐陪练', status: 'COMPLETED', amount: 11200 },
  { id: 9111, orderNo: 'SL202611040111', serviceName: '王者荣耀 · 赛季冲刺', status: 'COMPLETED', amount: 20800 },
  { id: 9112, orderNo: 'SL202611040112', serviceName: '无畏契约 · 定级陪练', status: 'IN_SERVICE', amount: 22000 },
]

export const initialSubsidyRecords: AdminSubsidyRecord[] = [
  { id: 'SUB-1103-02', amount: 20000, reference: 'DEMO-OFFLINE-20261103-02', remark: '线下补入平台补贴资金', operator: '运营管理员', createdAt: '2026-11-03 17:18' },
  { id: 'SUB-1101-01', amount: 50000, reference: 'DEMO-OFFLINE-20261101-01', remark: '月度平台补贴预算补入', operator: '财务管理员', createdAt: '2026-11-01 10:06' },
]
