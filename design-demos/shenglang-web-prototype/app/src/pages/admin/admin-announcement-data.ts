export interface AdminAnnouncementItem {
  id: number
  title: string
  content: string
  pinned: boolean
  active: boolean
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export const initialAdminAnnouncements: AdminAnnouncementItem[] = [
  {
    id: 1,
    title: '声浪新赛季服务安排与履约说明',
    content: '新赛季服务于 11 月 5 日起按现有价目正常开放。\n\n下单前请核对游戏区服、当前段位与期望服务时长。客服确认订单资料后，陪玩将按订单约定履约；如需调整时间，请尽早通过订单会话沟通。\n\n服务结果以实际对局为准，不承诺未在商品中明确约定的段位或收益。',
    pinned: true,
    active: true,
    sortOrder: 10,
    createdAt: '2026-11-03 18:30',
    updatedAt: '2026-11-03 18:30',
  },
  {
    id: 2,
    title: '关于规范报单凭证与结单审核的提醒',
    content: '陪玩完成服务后，请按订单要求提交清晰、完整的入队与结单凭证。\n\n凭证应能核对订单号、服务时间和实际对局结果。审核不通过时，请根据驳回原因补交，不要重复提交无关图片。',
    pinned: true,
    active: true,
    sortOrder: 20,
    createdAt: '2026-11-02 12:00',
    updatedAt: '2026-11-03 09:20',
  },
  {
    id: 3,
    title: '会员权益与浪花到账规则更新',
    content: '会员权益已在老板端「我的－会员权益」中集中展示。\n\n充值实收与赠送浪花按收款记录分别到账；订单支付、退款和售后金额以资金流水为准。任何线下收款都应在管理后台登记并留存凭证。',
    pinned: false,
    active: true,
    sortOrder: 30,
    createdAt: '2026-11-01 15:40',
    updatedAt: '2026-11-01 15:40',
  },
  {
    id: 4,
    title: '每日签到与积分奖励使用说明',
    content: '老板登录后可从首页进入每日签到。连续签到奖励以页面展示和实际到账记录为准。\n\n积分可在积分中心查看，并按当前开放的兑换规则使用。签到与积分不支持转让。',
    pinned: false,
    active: true,
    sortOrder: 40,
    createdAt: '2026-10-30 10:10',
    updatedAt: '2026-10-30 10:10',
  },
  {
    id: 5,
    title: '客服会话与订单售后处理指引',
    content: '订单疑问请优先从对应订单进入客服会话，以便团队核对订单、履约和售后记录。\n\n申请售后时请说明具体问题并提交必要凭证。退款结果、权益退回和收益追回分别以后续状态与资金流水为准。',
    pinned: false,
    active: true,
    sortOrder: 50,
    createdAt: '2026-10-28 16:20',
    updatedAt: '2026-10-29 11:00',
  },
  {
    id: 6,
    title: '旧版充值渠道停用公告',
    content: '旧版线下充值渠道已停止使用。请勿向历史公告中的账户转账。\n\n此公告当前处于停用状态，老板端首页、公告列表和详情均不展示。',
    pinned: false,
    active: false,
    sortOrder: 60,
    createdAt: '2026-09-20 09:00',
    updatedAt: '2026-10-20 09:00',
  },
]
