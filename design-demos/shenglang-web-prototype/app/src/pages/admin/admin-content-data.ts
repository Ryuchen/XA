export type AdminContentKind = 'HOT' | 'RIGHTS' | 'ORDER_RULES' | 'PROVIDER_RULES' | 'AFTERSALE_RULES' | 'POINT_RULES'

export interface AdminContentItem {
  id: number
  code: string
  kind: AdminContentKind
  title: string
  imageUrl: string
  content: string
  active: boolean
  startsAt: string
  endsAt: string
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export const adminContentKindLabels: Record<AdminContentKind, string> = {
  HOT: '俱乐部活动',
  RIGHTS: '老板权益',
  ORDER_RULES: '下单须知',
  PROVIDER_RULES: '打手保障',
  AFTERSALE_RULES: '售后标准',
  POINT_RULES: '积分规则',
}

export const initialAdminContents: AdminContentItem[] = [
  {
    id: 1,
    code: 'november-duo-plan',
    kind: 'HOT',
    title: '双排默契计划',
    imageUrl: './activity-duo.svg',
    content: '',
    active: true,
    startsAt: '2026-11-03 10:00',
    endsAt: '2026-11-16 23:59',
    sortOrder: 0,
    createdAt: '2026-11-02 16:20',
    updatedAt: '2026-11-03 09:40',
  },
  {
    id: 2,
    code: 'november-welcome-week',
    kind: 'HOT',
    title: '声浪迎新组队周',
    imageUrl: '',
    content: '',
    active: true,
    startsAt: '2026-11-10 10:00',
    endsAt: '2026-11-20 23:59',
    sortOrder: 0,
    createdAt: '2026-11-03 14:10',
    updatedAt: '2026-11-03 14:10',
  },
  {
    id: 3,
    code: 'october-weekend-match',
    kind: 'HOT',
    title: '声浪周末挑战赛',
    imageUrl: './activity-weekend.svg',
    content: '',
    active: true,
    startsAt: '2026-10-20 10:00',
    endsAt: '2026-10-25 23:59',
    sortOrder: 0,
    createdAt: '2026-10-18 12:00',
    updatedAt: '2026-10-20 10:00',
  },
  {
    id: 4,
    code: 'order-service-guide',
    kind: 'ORDER_RULES',
    title: '下单前确认服务、区服与时间安排',
    imageUrl: './content-order-guide.svg',
    content: '<h2>下单前请核对</h2><p>确认游戏区服、服务项目、期望时间与计费数量。订单资料由客服复核后进入履约流程。</p><ul><li>服务结果以实际对局为准。</li><li>需要调整时间时，请尽早通过订单会话沟通。</li></ul>',
    active: true,
    startsAt: '2026-11-01 09:00',
    endsAt: '',
    sortOrder: 90,
    createdAt: '2026-10-28 11:30',
    updatedAt: '2026-11-01 09:00',
  },
  {
    id: 5,
    code: 'boss-rights-guide',
    kind: 'RIGHTS',
    title: '老板权益与会员服务说明',
    imageUrl: '',
    content: '<p>会员折扣、优惠券与权益记录以老板端当前页面为准。VIP 折扣与折扣券取优，不叠加使用。</p><p>赠送权益的交付状态可在对应记录中查询。</p>',
    active: true,
    startsAt: '2026-10-28 10:00',
    endsAt: '',
    sortOrder: 80,
    createdAt: '2026-10-26 15:00',
    updatedAt: '2026-10-28 10:00',
  },
  {
    id: 6,
    code: 'points-redemption-rules',
    kind: 'POINT_RULES',
    title: '积分获取、兑换与奖品交付规则',
    imageUrl: '',
    content: '<p>充值本身不增加积分，消费积分按实际业务规则累计。兑换前请核对所需积分、库存与交付方式。</p><p>奖品交付结果以权益记录为准。</p>',
    active: true,
    startsAt: '2026-10-26 09:00',
    endsAt: '',
    sortOrder: 70,
    createdAt: '2026-10-25 14:00',
    updatedAt: '2026-10-26 09:00',
  },
  {
    id: 7,
    code: 'provider-fulfillment-guide',
    kind: 'PROVIDER_RULES',
    title: '陪玩履约与报单凭证规范',
    imageUrl: '',
    content: '<p>陪玩应按订单约定完成服务，并提交清晰、完整的入队与结单凭证。审核驳回时，请按具体原因补交。</p>',
    active: true,
    startsAt: '2026-10-24 09:00',
    endsAt: '',
    sortOrder: 60,
    createdAt: '2026-10-23 16:00',
    updatedAt: '2026-10-24 09:00',
  },
  {
    id: 8,
    code: 'aftersale-service-standard',
    kind: 'AFTERSALE_RULES',
    title: '订单售后与退款处理标准',
    imageUrl: '',
    content: '<p>售后申请须说明具体问题并提交必要凭证。退款、权益退回和收益追回分别以后续状态与资金流水为准。</p>',
    active: false,
    startsAt: '2026-10-20 09:00',
    endsAt: '',
    sortOrder: 50,
    createdAt: '2026-10-18 10:00',
    updatedAt: '2026-11-02 18:30',
  },
]
