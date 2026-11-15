export type AdminReportStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type AdminReportSettlement = 'WAITING' | 'SETTLED' | 'REVIEW'

export interface AdminReport {
  id: number
  status: AdminReportStatus
  providerName: string
  providerNo: string
  providerUsername: string
  gameName: string
  orderNo: string
  orderId: number
  amount: number
  expectedIncome: number
  commissionRate: number
  commissionSource: string
  fulfillment: string
  settlement: AdminReportSettlement
  orderBlockReason: string
  aftersaleOpen: boolean
  paymentStatus: 'UNPAID' | 'PAID' | 'PARTIALLY_REFUNDED' | 'REFUNDED'
  description: string
  remark: string
  auditRemark: string
  auditorName: string
  submittedAt: string
  auditedAt: string
  entryImage?: string
  completionImage?: string
  resultImages: string[]
}

const entry = './content-order-guide.svg'
const completion = './activity-duo.svg'
const result = './activity-weekend.svg'

export const adminReports: AdminReport[] = [
  {
    id: 8201, status: 'PENDING', providerName: '小鹿', providerNo: 'SL-P-1026', providerUsername: 'deer_lol', gameName: '王者荣耀', orderNo: 'SL202611040037', orderId: 3610,
    amount: 216, expectedIncome: 86.4, commissionRate: 20, commissionSource: '订单分账快照', fulfillment: '双陪 · 2 局', settlement: 'WAITING', orderBlockReason: '', aftersaleOpen: false, paymentStatus: 'PAID',
    description: '双陪服务已完成，提交本人入队、结单与战绩凭证。', remark: '第二局中更换过一次房间。', auditRemark: '', auditorName: '', submittedAt: '2026-11-04 16:52', auditedAt: '', entryImage: entry, completionImage: completion, resultImages: [result],
  },
  {
    id: 8207, status: 'PENDING', providerName: '阿澈', providerNo: 'SL-P-1018', providerUsername: 'ache_game', gameName: '王者荣耀', orderNo: 'SL202611040037', orderId: 3610,
    amount: 216, expectedIncome: 86.4, commissionRate: 20, commissionSource: '订单分账快照', fulfillment: '双陪 · 2 局', settlement: 'WAITING', orderBlockReason: '', aftersaleOpen: false, paymentStatus: 'PAID',
    description: '双陪另一位陪玩的履约报单。', remark: '战绩截图尚未补齐。', auditRemark: '', auditorName: '', submittedAt: '2026-11-04 16:47', auditedAt: '', entryImage: entry, completionImage: completion, resultImages: [],
  },
  {
    id: 8202, status: 'PENDING', providerName: '小鹿', providerNo: 'SL-P-1026', providerUsername: 'deer_lol', gameName: '金铲铲之战', orderNo: 'SL202611040032', orderId: 3605,
    amount: 96, expectedIncome: 76.8, commissionRate: 20, commissionSource: '订单分账快照', fulfillment: '单陪 · 1 局', settlement: 'WAITING', orderBlockReason: '订单未完成，不能通过报单', aftersaleOpen: false, paymentStatus: 'PAID',
    description: '陪玩提前提交了过程凭证，订单仍未完成。', remark: '等待老板确认结单。', auditRemark: '', auditorName: '', submittedAt: '2026-11-04 16:21', auditedAt: '', entryImage: entry, completionImage: completion, resultImages: [result],
  },
  {
    id: 8203, status: 'PENDING', providerName: '阿澈', providerNo: 'SL-P-1018', providerUsername: 'ache_game', gameName: '英雄联盟', orderNo: 'SL202611040031', orderId: 3606,
    amount: 180, expectedIncome: 144, commissionRate: 20, commissionSource: '订单分账快照', fulfillment: '单陪 · 3 局', settlement: 'REVIEW', orderBlockReason: '订单已部分退款，不能通过报单', aftersaleOpen: true, paymentStatus: 'PARTIALLY_REFUNDED',
    description: '售后订单中的历史待审核凭证。', remark: '其中一局未完整履约。', auditRemark: '', auditorName: '', submittedAt: '2026-11-04 15:48', auditedAt: '', entryImage: entry, completionImage: completion, resultImages: [result],
  },
  {
    id: 8204, status: 'PENDING', providerName: '奈奈', providerNo: 'SL-P-1031', providerUsername: 'nana_val', gameName: '无畏契约', orderNo: 'SL202611040035', orderId: 3602,
    amount: 68, expectedIncome: 55.8, commissionRate: 18, commissionSource: '订单分账快照', fulfillment: '单陪 · 1 局', settlement: 'REVIEW', orderBlockReason: '订单未支付，不能通过报单', aftersaleOpen: false, paymentStatus: 'UNPAID',
    description: '未支付订单的异常提交记录，保留供核对。', remark: '不进入结算。', auditRemark: '', auditorName: '', submittedAt: '2026-11-04 15:12', auditedAt: '', entryImage: entry, completionImage: completion, resultImages: [result],
  },
  {
    id: 8205, status: 'APPROVED', providerName: '阿澈', providerNo: 'SL-P-1018', providerUsername: 'ache_game', gameName: '英雄联盟', orderNo: 'SL202611040031', orderId: 3606,
    amount: 180, expectedIncome: 144, commissionRate: 20, commissionSource: '审核快照', fulfillment: '单陪 · 3 局', settlement: 'SETTLED', orderBlockReason: '', aftersaleOpen: true, paymentStatus: 'PARTIALLY_REFUNDED',
    description: '凭证已审核，后续发生部分退款。', remark: '收益追回与净额以钱包流水为准。', auditRemark: '', auditorName: '客服小岚', submittedAt: '2026-11-04 14:26', auditedAt: '2026-11-04 14:32', entryImage: entry, completionImage: completion, resultImages: [result],
  },
  {
    id: 8206, status: 'REJECTED', providerName: '小鹿', providerNo: 'SL-P-1026', providerUsername: 'deer_lol', gameName: '无畏契约', orderNo: 'SL202611040035', orderId: 3602,
    amount: 68, expectedIncome: 54.4, commissionRate: 20, commissionSource: '订单分账快照', fulfillment: '单陪 · 1 局', settlement: 'REVIEW', orderBlockReason: '', aftersaleOpen: false, paymentStatus: 'UNPAID',
    description: '凭证画面无法对应订单中的游戏资料。', remark: '可重新提交清晰战绩截图。', auditRemark: '战绩截图未显示完整游戏昵称与对局时间，请补充可核对的原图。', auditorName: '客服阿澄', submittedAt: '2026-11-04 13:58', auditedAt: '2026-11-04 14:06', entryImage: entry, completionImage: completion, resultImages: [result],
  },
]

export const adminReportStatusLabel: Record<AdminReportStatus, string> = {
  PENDING: '待审核',
  APPROVED: '已通过',
  REJECTED: '已驳回',
}
