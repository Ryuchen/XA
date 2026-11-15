export type AdminWithdrawStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type AdminWithdrawClaim = 'unclaimed' | 'mine' | 'other'

export interface AdminWithdraw {
  id: number
  providerName: string
  providerUsername: string
  amount: number
  taxRate: number
  taxAmount: number
  actualAmount: number
  payeeMethod: '支付宝' | '微信' | '银行卡'
  payeeName: string
  payeeAccount: string
  status: AdminWithdrawStatus
  claim: AdminWithdrawClaim
  claimedBy: string
  claimedAt: string | null
  remark: string
  auditRemark: string
  payoutReference: string
  paidAt: string | null
  auditorName: string
  createdAt: string
  auditedAt: string | null
}

export const initialAdminWithdraws: AdminWithdraw[] = [
  {
    id: 9206,
    providerName: '青禾',
    providerUsername: 'provider_qinghe',
    amount: 50000,
    taxRate: 10,
    taxAmount: 5000,
    actualAmount: 45000,
    payeeMethod: '支付宝',
    payeeName: '林青禾',
    payeeAccount: 'qh_demo@example.com',
    status: 'PENDING',
    claim: 'unclaimed',
    claimedBy: '',
    claimedAt: null,
    remark: '本周收益结算',
    auditRemark: '',
    payoutReference: '',
    paidAt: null,
    auditorName: '',
    createdAt: '2026-11-04 09:32',
    auditedAt: null,
  },
  {
    id: 9205,
    providerName: '小岚',
    providerUsername: 'provider_lan',
    amount: 32000,
    taxRate: 10,
    taxAmount: 3200,
    actualAmount: 28800,
    payeeMethod: '微信',
    payeeName: '许岚',
    payeeAccount: 'lan_demo',
    status: 'PENDING',
    claim: 'mine',
    claimedBy: '运营管理员',
    claimedAt: '2026-11-04 09:05',
    remark: '',
    auditRemark: '',
    payoutReference: '',
    paidAt: null,
    auditorName: '',
    createdAt: '2026-11-04 08:57',
    auditedAt: null,
  },
  {
    id: 9204,
    providerName: '北岛',
    providerUsername: 'provider_beidao',
    amount: 20000,
    taxRate: 10,
    taxAmount: 2000,
    actualAmount: 18000,
    payeeMethod: '支付宝',
    payeeName: '陈北',
    payeeAccount: 'bd_demo@example.com',
    status: 'PENDING',
    claim: 'other',
    claimedBy: '客服岚',
    claimedAt: '2026-11-04 08:44',
    remark: '收款信息已电话复核',
    auditRemark: '',
    payoutReference: '',
    paidAt: null,
    auditorName: '',
    createdAt: '2026-11-04 08:20',
    auditedAt: null,
  },
  {
    id: 9203,
    providerName: '阿泽',
    providerUsername: 'provider_aze',
    amount: 68000,
    taxRate: 10,
    taxAmount: 6800,
    actualAmount: 61200,
    payeeMethod: '微信',
    payeeName: '周泽',
    payeeAccount: 'aze_demo',
    status: 'APPROVED',
    claim: 'other',
    claimedBy: '客服岚',
    claimedAt: '2026-11-03 17:12',
    remark: '',
    auditRemark: '',
    payoutReference: 'DEMO-PAY-20261103-001',
    paidAt: '2026-11-03 17:26',
    auditorName: '客服岚',
    createdAt: '2026-11-03 16:48',
    auditedAt: '2026-11-03 17:26',
  },
  {
    id: 9202,
    providerName: '旧版收益账号',
    providerUsername: 'wallet_legacy',
    amount: 15000,
    taxRate: 0,
    taxAmount: 0,
    actualAmount: 15000,
    payeeMethod: '支付宝',
    payeeName: '历史收款人',
    payeeAccount: 'legacy_demo@example.com',
    status: 'REJECTED',
    claim: 'other',
    claimedBy: '客服岚',
    claimedAt: '2026-11-02 11:06',
    remark: '历史提现记录',
    auditRemark: '收款账号实名信息与陪玩资料不一致，请更新后重新申请。',
    payoutReference: '',
    paidAt: null,
    auditorName: '客服岚',
    createdAt: '2026-11-02 10:38',
    auditedAt: '2026-11-02 11:20',
  },
  {
    id: 9201,
    providerName: '阿澈',
    providerUsername: 'provider_che',
    amount: 26000,
    taxRate: 10,
    taxAmount: 2600,
    actualAmount: 23400,
    payeeMethod: '银行卡',
    payeeName: '沈澈',
    payeeAccount: '6222 **** **** 0101',
    status: 'APPROVED',
    claim: 'mine',
    claimedBy: '运营管理员',
    claimedAt: '2026-11-01 15:22',
    remark: '',
    auditRemark: '',
    payoutReference: 'DEMO-PAY-20261101-003',
    paidAt: '2026-11-01 15:40',
    auditorName: '运营管理员',
    createdAt: '2026-11-01 15:08',
    auditedAt: '2026-11-01 15:40',
  },
]
