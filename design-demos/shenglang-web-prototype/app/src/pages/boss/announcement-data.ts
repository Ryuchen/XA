export interface Announcement {
  id: number
  title: string
  content: string
  isPinned: boolean
  createdAt: string
}

export const bossAnnouncements: Announcement[] = [
  { id: 1, title: '11 月俱乐部活动与下单须知', content: '本月俱乐部活动已开启。下单前请确认服务项目、陪玩安排与游戏资料，服务时间以订单页为准。\n\n完成订单后，如有服务问题，请从订单详情进入售后或联系客服。请勿重复提交支付、下单或退款申请。\n\n感谢你与声浪一起维护清楚、友善的游戏陪伴体验。', isPinned: true, createdAt: '2026-11-01' },
  { id: 2, title: '陪玩服务评价与售后说明更新', content: '服务完成后，可在订单详情对本次服务进行评价。多人服务需要分别评价每位陪玩。\n\n如需售后，请保留订单编号并说明实际情况，平台会依据订单记录处理。', isPinned: false, createdAt: '2026-10-28' },
  { id: 3, title: '浪花钱包充值核对指引', content: '充值结果尚未确认时，请先查看钱包流水，不要重复付款。\n\n如流水长时间未更新，请通过客服与帮助提交充值核对咨询。', isPinned: false, createdAt: '2026-10-24' },
  { id: 4, title: '俱乐部社区文明互动规范', content: '请尊重每一位老板与陪玩，保持友善沟通，不发布违法、骚扰或与游戏服务无关的内容。', isPinned: false, createdAt: '2026-10-20' },
]
