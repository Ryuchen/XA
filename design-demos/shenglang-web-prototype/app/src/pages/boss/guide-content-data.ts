export type GuideKind = 'RIGHTS' | 'POINT_RULES'

export interface GuideContentSection {
  heading?: string
  paragraphs?: string[]
  bullets?: string[]
  quote?: string
}

export interface GuideContentItem {
  id: number
  kind: GuideKind
  title: string
  summary: string
  date: string
  eyebrow: string
  sections: GuideContentSection[]
}

export const guideContentItems: GuideContentItem[] = [
  {
    id: 201,
    kind: 'RIGHTS',
    eyebrow: '会员礼遇',
    title: '会员折扣与专属券包使用说明',
    summary: '了解等级优惠、优惠券取优与适用核验',
    date: '2026-11-01',
    sections: [
      { paragraphs: ['会员身份与卡券是两类独立权益。下单时，系统会根据当前会员等级、优惠券状态和适用服务进行结算核验。'] },
      { heading: '01  会员折扣', paragraphs: ['当前可享受的折扣以“我的会员”页面展示为准。有效报单确认实销后，等级与对应权益会按平台规则更新。'], bullets: ['待确认实销不计入当前会员等级。', '订单报价与最终优惠以结算页面为准。'] },
      { heading: '02  专属券包', paragraphs: ['会员专属优惠券需在有效期内使用，并满足对应门槛与服务范围。'], bullets: ['会员折扣与优惠券按结算规则取优，不重复叠加。', '卡券状态变化时，请以“我的券”最新记录为准。'] },
      { quote: '优惠结果不明确时，先返回订单或卡券页面核对，不要重复提交订单或支付。' },
    ],
  },
  {
    id: 202,
    kind: 'RIGHTS',
    eyebrow: '生日心意',
    title: '生日祝福与当日积分翻倍',
    summary: '生日登记、首页祝福与积分发放说明',
    date: '2026-10-28',
    sections: [
      { paragraphs: ['登记生日后，老板可选择是否在生日当天接受首页祝福。首页仅展示昵称与祝福内容，不公开展示出生日期。'] },
      { heading: '01  生日资料', bullets: ['首次登记前请仔细核对日期。', '登记后如需修改生日，请联系客服核对。', '可随时调整是否接受首页生日祝福。'] },
      { heading: '02  当日积分翻倍', paragraphs: ['符合活动条件的生日当日消费，可按平台规则获得翻倍积分。积分是否到账及具体数量，以积分明细为准。'], quote: '生日权益与会员等级相互独立；实际适用范围以当期平台发布内容为准。' },
    ],
  },
  {
    id: 203,
    kind: 'RIGHTS',
    eyebrow: '俱乐部身份',
    title: '会员名片与专属展示标签',
    summary: '设置名片标签，展示你的俱乐部身份',
    date: '2026-10-20',
    sections: [
      { paragraphs: ['已获得的名片标签可在“我的会员”中自由选择展示，让俱乐部身份与个人游戏偏好更清晰。'] },
      { heading: '标签设置', bullets: ['最多选择 5 个已获得标签用于展示。', '未手动指定时，优先展示高等级已获得标签。', '保存成功后可在个人中心查看更新结果。'] },
      { heading: '身份说明', paragraphs: ['会员等级、成就标签与游戏资料是不同信息。标签展示不会改变订单权益、积分余额或账号绑定状态。'] },
    ],
  },
  {
    id: 301,
    kind: 'POINT_RULES',
    eyebrow: '积分获取',
    title: '有效报单与生日活动积分',
    summary: '了解确认实销、积分入账与翻倍活动',
    date: '2026-11-01',
    sections: [
      { paragraphs: ['积分用于记录老板在俱乐部的参与和成长。不同来源的积分会在对应业务完成后进入积分明细。'] },
      { heading: '01  有效报单', paragraphs: ['有效报单在确认实销后按平台规则计入积分。待确认金额、已退款部分不计入当前净消费口径。'] },
      { heading: '02  生日活动', paragraphs: ['符合生日活动条件的消费可获得额外积分。实际入账时间和数量以积分明细为准。'] },
      { quote: '网络异常时不要重复下单或支付；先核对订单、积分与钱包记录。' },
    ],
  },
  {
    id: 302,
    kind: 'POINT_RULES',
    eyebrow: '社区互动',
    title: 'KOOK 绑定、在线与每日签到',
    summary: '绑定本人账号后参与社区积分活动',
    date: '2026-10-26',
    sections: [
      { paragraphs: ['在积分页面获取绑定码后，可按页面提示在 KOOK 完成本人账号绑定。绑定成功后，在线与签到状态才会同步到积分账户。'] },
      { heading: '绑定与签到', bullets: ['绑定码具有时效，请在页面提示的有效期内使用。', '每日签到结果以 KOOK 与积分账户的最新记录为准。', '请勿将绑定码或账号验证码转发给他人。'] },
      { heading: '积分同步', paragraphs: ['在线和签到积分可能存在同步时间。未看到即时变化时，先刷新积分明细，不要反复绑定或创建新账号。'] },
    ],
  },
  {
    id: 303,
    kind: 'POINT_RULES',
    eyebrow: '兑换与交付',
    title: '积分兑换、库存与奖品交付规则',
    summary: '兑换前核对积分、库存与交付资料',
    date: '2026-10-22',
    sections: [
      { paragraphs: ['兑换前请核对所需积分、库存与权益内容。提交后可在权益记录中查看结果，并按奖励类型补充交付资料。'] },
      { heading: '01  库存与扣除', bullets: ['实物、虚拟物品与服务权益可能受库存限制。', '兑换结果待确认时，只重试原申请，避免重复扣除积分。', '积分支出与退回都会同步出现在积分明细。'] },
      { heading: '02  奖励交付', paragraphs: ['余额奖励自动到账；其他奖励需根据交付记录补充游戏资料、标签内容或收件信息。'], bullets: ['实物奖励需填写完整收件资料。', '虚拟物品与服务权益以交付记录的处理状态为准。', '取消权益如符合退回条件，积分将按处理结果返还。'] },
      { quote: '兑换记录与钱包流水是结果核对依据，请勿因页面反馈延迟而重复创建兑换。' },
    ],
  },
]

export function getGuideItems(kind: GuideKind) {
  return guideContentItems.filter(item => item.kind === kind)
}
