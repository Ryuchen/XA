type Dict = Record<string, { label: string; type?: 'success' | 'warning' | 'info' | 'danger' | 'primary' }>

export const USER_ROLE: Dict = {
  CUSTOMER: { label: '老板', type: 'primary' },
  PROVIDER: { label: '陪玩', type: 'success' },
  OPERATOR: { label: '客服', type: 'warning' },
  ADMIN: { label: '管理员', type: 'danger' },
}

export const ESCORT_STATUS: Dict = {
  AVAILABLE: { label: '空闲', type: 'success' },
  BUSY: { label: '忙碌', type: 'warning' },
  OFFLINE: { label: '离线', type: 'info' },
}

export const GENDER: Dict = {
  MALE: { label: '男陪', type: 'primary' },
  FEMALE: { label: '女陪', type: 'danger' },
  UNKNOWN: { label: '未知', type: 'info' },
}

export const ORDER_STATUS: Dict = {
  PENDING: { label: '待接单', type: 'info' },
  GRABBED: { label: '已接单', type: 'primary' },
  IN_SERVICE: { label: '服务中', type: 'warning' },
  COMPLETED: { label: '已完成', type: 'success' },
  CANCELLED: { label: '已取消', type: 'danger' },
}

export const PAYMENT_STATUS: Dict = {
  UNPAID: { label: '未支付', type: 'info' },
  PAID: { label: '已支付', type: 'success' },
  REFUNDED: { label: '已退款', type: 'danger' },
}

export const REPORT_STATUS: Dict = {
  PENDING: { label: '待审核', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
}

export const WITHDRAW_STATUS: Dict = {
  PENDING: { label: '待审核', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
}

export const AUDITION_SIGNUP_STATUS: Dict = {
  PENDING: { label: '待审核', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
}

export const PAYEE_METHOD: Dict = {
  WECHAT: { label: '微信', type: 'success' },
  ALIPAY: { label: '支付宝', type: 'primary' },
  BANK: { label: '银行卡', type: 'warning' },
}

export const TX_TYPE: Dict = {
  TOPUP: { label: '充值', type: 'success' },
  GIFT: { label: '赠送', type: 'success' },
  PAY: { label: '支付', type: 'warning' },
  INCOME: { label: '收益', type: 'success' },
  WITHDRAW: { label: '提现', type: 'info' },
  REWARD: { label: '奖励', type: 'primary' },
  PENALTY: { label: '罚款', type: 'danger' },
}

export const DISPOSE_TYPE: Dict = {
  REWARD: { label: '奖励', type: 'success' },
  PENALTY: { label: '罚款', type: 'danger' },
}

export const TX_STATUS: Dict = {
  PENDING: { label: '处理中', type: 'info' },
  SUCCESS: { label: '成功', type: 'success' },
  FAILED: { label: '失败', type: 'danger' },
}

export const COUPON_TYPE: Dict = {
  THRESHOLD: { label: '满减', type: 'warning' },
  DIRECT: { label: '无门槛', type: 'success' },
}

export const USER_COUPON_STATUS: Dict = {
  UNUSED: { label: '未使用', type: 'success' },
  USED: { label: '已使用', type: 'info' },
  EXPIRED: { label: '已过期', type: 'danger' },
}

export const MESSAGE_TYPE: Dict = {
  SYSTEM: { label: '系统通知', type: 'info' },
  ORDER: { label: '订单消息', type: 'primary' },
  SUPPORT: { label: '客服消息', type: 'warning' },
  PROMOTION: { label: '活动通知', type: 'success' },
}

export const PROMOTION_SCOPE: Dict = {
  ALL: { label: '全场', type: 'primary' },
  CATEGORY: { label: '指定分类', type: 'warning' },
  ITEMS: { label: '指定商品', type: 'success' },
}

export const ACHIEVEMENT_METRIC: Dict = {
  orders: { label: '完成订单数', type: 'primary' },
  amount: { label: '累计消费（兴安币）', type: 'warning' },
}

export const LINK_TYPE: Dict = {
  NONE: { label: '无跳转', type: 'info' },
  PRODUCT: { label: '商品详情', type: 'primary' },
  ANNOUNCEMENT: { label: '公告详情', type: 'success' },
  URL: { label: '外部链接', type: 'warning' },
}

export function dictLabel(dict: Dict, key?: string): string {
  if (!key) return '-'
  return dict[key]?.label || key
}
