export type AdminTransactionType = 'TOPUP' | 'PAY' | 'INCOME' | 'WITHDRAW' | 'REWARD' | 'GIFT' | 'PENALTY' | 'DEPOSIT' | 'DEPOSIT_INCOME' | 'SHOP_INCOME' | 'REFUND' | 'PASS_PURCHASE' | 'PASS_INCOME' | 'WITHDRAW_TAX' | 'ADJUST' | 'SUBSIDY_EXPENSE' | 'EARNINGS_FREEZE' | 'EARNINGS_RELEASE'
export type AdminTransactionStatus = 'PENDING' | 'SUCCESS' | 'FAILED'

export interface AdminTransaction {
  id: number
  txNo: string
  walletId: number
  username: string
  nickname: string
  orderNo: string | null
  amount: number
  type: AdminTransactionType
  balanceBefore: number
  balanceAfter: number
  status: AdminTransactionStatus
  remark: string
  operatorName: string
  createdAt: string
  reversalOf?: number
}

export const adminTransactionTypeLabel: Record<AdminTransactionType, string> = {
  TOPUP: '充值', PAY: '支付', INCOME: '收益', WITHDRAW: '提现', REWARD: '奖励', GIFT: '赠送', PENALTY: '罚款', DEPOSIT: '押金', DEPOSIT_INCOME: '押金收退', SHOP_INCOME: '平台收入', REFUND: '订单退款', PASS_PURCHASE: '通行证购买', PASS_INCOME: '通行证收入', WITHDRAW_TAX: '提现税费', ADJUST: '后台调账', SUBSIDY_EXPENSE: '平台补贴支出', EARNINGS_FREEZE: '订单收益冻结', EARNINGS_RELEASE: '订单收益解冻',
}

export const adminTransactionStatusLabel: Record<AdminTransactionStatus, string> = {
  PENDING: '处理中', SUCCESS: '成功', FAILED: '失败',
}

export const initialAdminTransactions: AdminTransaction[] = [
  { id: 9018, txNo: 'TXDEMO202611040018', walletId: 2003, username: 'provider_beidao', nickname: '北岛', orderNo: null, amount: -5000, type: 'DEPOSIT', balanceBefore: 26600, balanceAfter: 21600, status: 'SUCCESS', remark: '现金押金钱包扣缴示例', operatorName: '运营管理员', createdAt: '2026-11-04 11:28' },
  { id: 9017, txNo: 'TXDEMO202611040017', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: null, amount: 5000, type: 'DEPOSIT_INCOME', balanceBefore: 123000, balanceAfter: 128000, status: 'SUCCESS', remark: '陪玩押金平台侧配对入账', operatorName: '运营管理员', createdAt: '2026-11-04 11:28' },
  { id: 9016, txNo: 'TXDEMO202611040016', walletId: 1001, username: 'boss_chen', nickname: '陈默', orderNo: null, amount: -1000, type: 'ADJUST', balanceBefore: 87200, balanceAfter: 86200, status: 'SUCCESS', remark: '冲正误操作调增，原因已复核', operatorName: '运营管理员', createdAt: '2026-11-04 11:06', reversalOf: 9012 },
  { id: 9015, txNo: 'TXDEMO202611040015', walletId: 2001, username: 'provider_lan', nickname: '小岚', orderNo: 'SL202611040031', amount: 1440, type: 'EARNINGS_RELEASE', balanceBefore: 51000, balanceAfter: 52440, status: 'SUCCESS', remark: '订单收益解冻，由冻结资金转回余额', operatorName: '系统', createdAt: '2026-11-04 10:58' },
  { id: 9014, txNo: 'TXDEMO202611040014', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: 'SL202611040031', amount: 360, type: 'SHOP_INCOME', balanceBefore: 731640, balanceAfter: 732000, status: 'SUCCESS', remark: '订单平台收入分配', operatorName: '系统', createdAt: '2026-11-04 10:52' },
  { id: 9019, txNo: 'TXDEMO202611040019', walletId: 2001, username: 'provider_lan', nickname: '小岚', orderNo: 'SL202611040031', amount: 1440, type: 'INCOME', balanceBefore: 51000, balanceAfter: 52440, status: 'SUCCESS', remark: '订单履约收益入账', operatorName: '系统', createdAt: '2026-11-04 10:50' },
  { id: 9013, txNo: 'TXDEMO202611040013', walletId: 2001, username: 'provider_lan', nickname: '小岚', orderNo: 'SL202611040031', amount: -1440, type: 'EARNINGS_FREEZE', balanceBefore: 52440, balanceAfter: 51000, status: 'SUCCESS', remark: '订单收益冻结，余额转入冻结资金', operatorName: '系统', createdAt: '2026-11-04 10:52' },
  { id: 9012, txNo: 'TXDEMO202611040012', walletId: 1001, username: 'boss_chen', nickname: '陈默', orderNo: null, amount: 1000, type: 'ADJUST', balanceBefore: 86200, balanceAfter: 87200, status: 'SUCCESS', remark: '原调账：误将示例补入记到此账户', operatorName: '运营管理员', createdAt: '2026-11-04 10:46' },
  { id: 9011, txNo: 'TXDEMO202611040011', walletId: 1001, username: 'boss_chen', nickname: '陈默', orderNo: 'SL202611040031', amount: 600, type: 'REFUND', balanceBefore: 85600, balanceAfter: 86200, status: 'SUCCESS', remark: '部分履约退款，退回老板钱包', operatorName: '客服小岚', createdAt: '2026-11-04 10:32' },
  { id: 9010, txNo: 'TXDEMO202611040010', walletId: 2002, username: 'provider_qinghe', nickname: '青禾', orderNo: null, amount: -50000, type: 'WITHDRAW', balanceBefore: 86800, balanceAfter: 36800, status: 'PENDING', remark: '收益提现申请，等待人工打款核对', operatorName: '系统', createdAt: '2026-11-04 09:32' },
  { id: 9009, txNo: 'TXDEMO202611040009', walletId: 1001, username: 'boss_chen', nickname: '陈默', orderNo: null, amount: 600, type: 'GIFT', balanceBefore: 85000, balanceAfter: 85600, status: 'SUCCESS', remark: '客服充值赠送，与实充分开记账', operatorName: '客服小岚', createdAt: '2026-11-04 09:18' },
  { id: 9008, txNo: 'TXDEMO202611040008', walletId: 1001, username: 'boss_chen', nickname: '陈默', orderNo: null, amount: 50000, type: 'TOPUP', balanceBefore: 35000, balanceAfter: 85000, status: 'SUCCESS', remark: '线下收款后客服实充', operatorName: '客服小岚', createdAt: '2026-11-04 09:18' },
  { id: 9007, txNo: 'TXDEMO202611040007', walletId: 1003, username: 'boss_xu', nickname: '许星河', orderNo: null, amount: -680, type: 'PASS_PURCHASE', balanceBefore: 19340, balanceAfter: 18660, status: 'SUCCESS', remark: '购买陪玩通行证', operatorName: '系统', createdAt: '2026-11-04 08:56' },
  { id: 9006, txNo: 'TXDEMO202611040006', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: null, amount: 680, type: 'PASS_INCOME', balanceBefore: 730960, balanceAfter: 731640, status: 'SUCCESS', remark: '通行证平台收入', operatorName: '系统', createdAt: '2026-11-04 08:56' },
  { id: 9005, txNo: 'TXDEMO202611040005', walletId: 2003, username: 'provider_beidao', nickname: '北岛', orderNo: null, amount: 800, type: 'REWARD', balanceBefore: 25800, balanceAfter: 26600, status: 'SUCCESS', remark: '服务质量奖励', operatorName: '运营管理员', createdAt: '2026-11-03 22:40' },
  { id: 9004, txNo: 'TXDEMO202611040004', walletId: 2004, username: 'provider_legacy', nickname: '旧版陪玩账户', orderNo: null, amount: -200, type: 'PENALTY', balanceBefore: 1100, balanceAfter: 1100, status: 'FAILED', remark: '历史罚款失败示例，未改变真实余额', operatorName: '运营管理员', createdAt: '2026-11-03 21:16' },
  { id: 9003, txNo: 'TXDEMO202611040003', walletId: 1006, username: 'mountain_guest', nickname: '山间来客', orderNo: 'SL202611040031', amount: -1800, type: 'PAY', balanceBefore: 20800, balanceAfter: 19000, status: 'SUCCESS', remark: '英雄联盟进阶服务订单支付', operatorName: '系统', createdAt: '2026-11-03 20:08' },
  { id: 9002, txNo: 'TXDEMO202611040002', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: 'SL202611040031', amount: -600, type: 'SUBSIDY_EXPENSE', balanceBefore: 732600, balanceAfter: 732000, status: 'SUCCESS', remark: '订单售后平台补贴支出', operatorName: '客服小岚', createdAt: '2026-11-03 19:20' },
  { id: 9001, txNo: 'TXDEMO202611040001', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: 'SL202611040031', amount: 600, type: 'SUBSIDY_EXPENSE', balanceBefore: 732000, balanceAfter: 732600, status: 'SUCCESS', remark: '平台补贴追回，需结合原支出核对', operatorName: '财务管理员', createdAt: '2026-11-03 19:42', reversalOf: 9002 },
  { id: 9020, txNo: 'TXDEMO202611030020', walletId: 9001, username: 'platform_fund', nickname: '平台资金', orderNo: null, amount: 6800, type: 'WITHDRAW_TAX', balanceBefore: 725200, balanceAfter: 732000, status: 'SUCCESS', remark: '提现代扣税费：申请 #9203，税前 6,800 浪花 × 10%', operatorName: '客服岚', createdAt: '2026-11-03 17:26' },
]
