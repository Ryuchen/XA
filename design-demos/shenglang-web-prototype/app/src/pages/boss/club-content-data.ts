export type ClubContentKind = 'ORDER_RULES' | 'PROVIDER_RULES' | 'AFTERSALE_RULES'

export interface ClubContentSection {
  heading?: string
  paragraphs?: string[]
  bullets?: string[]
  quote?: string
}

export interface ClubContentItem {
  id: number
  kind: ClubContentKind
  title: string
  summary: string
  date: string
  image?: string
  sections: ClubContentSection[]
}

export const clubRuleItems: ClubContentItem[] = [
  {
    id: 101,
    kind: 'ORDER_RULES',
    title: '下单须知：服务、陪玩与游戏资料确认',
    summary: '下单前核对服务规格、人员安排与游戏资料',
    date: '2026-11-01',
    image: './content-order-guide.svg',
    sections: [
      {
        paragraphs: ['为了让每一次服务都能顺利开始，请在提交订单前完成三项确认。订单生成后，服务规格、实付金额与陪玩安排均以订单详情为准。'],
      },
      {
        heading: '01  确认服务规格',
        paragraphs: ['选择游戏、服务项目与数量，并核对报价。多人服务需要满足对应人数要求；选择平台安排时，以最终接单记录为准。'],
        bullets: ['自主下单关闭时，请通过客服确认方案。', '支付结果未知时，先核对钱包与订单，不要重复付款。'],
      },
      {
        heading: '02  确认陪玩与服务时间',
        paragraphs: ['指定陪玩的订单不会进入公开接单池。预约与即时订单是不同流程，预约是否成单及服务时间请以预约详情和后续订单为准。'],
      },
      {
        heading: '03  确认游戏资料',
        paragraphs: ['核对默认游戏、区服和游戏昵称。资料变更后，新订单会按已保存信息自动回填；已生成订单如需调整，请及时联系客服。'],
        quote: '遇到报价、支付或订单状态异常，请先查看对应业务页面的最新状态，再决定下一步操作。',
      },
    ],
  },
  {
    id: 102,
    kind: 'PROVIDER_RULES',
    title: '陪玩保障：接单与服务过程说明',
    summary: '了解接单、履约、评价与平台保障边界',
    date: '2026-10-26',
    sections: [
      { paragraphs: ['声浪电竞依据订单记录协调服务过程。陪玩接单后，老板可在订单详情查看人员安排与当前进度。'] },
      { heading: '接单与开始服务', bullets: ['待接单阶段可查看报名与接单进度。', '服务开始前请保持游戏资料准确，并通过订单确认陪玩信息。', '服务状态发生变化时，以订单详情的最新记录为准。'] },
      { heading: '完成与评价', paragraphs: ['服务完成后可对本次服务进行评价。多人服务需要分别评价每位陪玩，评价内容应基于真实体验。'] },
      { heading: '沟通与异常', paragraphs: ['请勿引导线下交易或绕过平台付款。出现人员、时间或服务内容争议时，保留订单编号并通过平台客服核对。'] },
    ],
  },
  {
    id: 103,
    kind: 'AFTERSALE_RULES',
    title: '售后标准：申请、退款与处理进度',
    summary: '明确售后入口、金额口径与处理反馈',
    date: '2026-10-22',
    sections: [
      { paragraphs: ['如需售后，请从对应订单详情进入，并说明实际情况。平台会依据订单、服务与沟通记录处理。'] },
      { heading: '如何提交申请', bullets: ['准备订单编号与具体问题说明。', '按页面要求补充必要信息，不要重复提交相同申请。', '提交后可在订单详情查看售后进度。'] },
      { heading: '退款与实销口径', paragraphs: ['取消或退款金额以实际处理结果为准。订单已退款部分不计入净消费；钱包到账情况可在退款流水中核对。'] },
      { heading: '结果待核对时', paragraphs: ['网络异常或结果未知时，先刷新订单与钱包状态。请勿因未看到即时反馈而重复取消、退款或支付。'] },
    ],
  },
]
