export type NoticeType = 'ORDER' | 'SYSTEM' | 'SUPPORT' | 'PROMOTION'

export interface Notice {
  id: number
  type: NoticeType
  title: string
  preview: string
  detail: string
  time: string
  isRead: boolean
  orderId?: string
  target?: 'wallet' | 'claims'
}

export const initialNotices: Notice[] = [
  { id: 301, type: 'ORDER', title: '陪玩已接单，请准备开局', preview: '小澈已接受王者双陪欢乐局，请在开局前确认游戏大区和昵称。', detail: '小澈已接受王者双陪欢乐局。服务计划于今天 20:00 开始，请提前确认微信区、游戏昵称与 UID；如需调整时间，请尽快通过订单客服沟通。', time: '今天 18:42', isRead: false, orderId: 'SL202611021024' },
  { id: 298, type: 'SUPPORT', title: '你的售后申请已有新进展', preview: '客服已完成材料核对，退款将按原实付与剩余服务金额计算。', detail: '客服已完成你提交的售后材料核对。后续处理结果与退款金额会以订单详情和钱包流水为准，请留意最新进度。', time: '今天 17:18', isRead: false, orderId: 'SL202611021019' },
  { id: 294, type: 'SYSTEM', title: '账号安全提醒', preview: '你的微信绑定状态已更新，如非本人操作请及时联系客服。', detail: '你的账号微信绑定状态已更新。绑定用于登录与找回账号；如非本人操作，请立即联系客服核验，不要向他人提供验证码。', time: '今天 12:06', isRead: false },
  { id: 287, type: 'PROMOTION', title: '本周会员权益已更新', preview: '星海会员可查看新的专属服务与冠名权益。', detail: '本周会员权益已更新，包含专属服务与冠名相关权益。具体适用范围与有效期以会员页面展示为准。', time: '昨天 20:30', isRead: true },
  { id: 281, type: 'ORDER', title: '服务已完成，欢迎评价', preview: '感谢这一局的陪伴，你的评价会帮助其他老板做出选择。', detail: '王者双陪欢乐局已完成。你可以分别评价本次服务的陪玩，也可以在订单详情中挑选礼物表达感谢。', time: '昨天 22:06', isRead: true, orderId: 'SL202611021024' },
  { id: 276, type: 'SYSTEM', title: '充值到账提醒', preview: '200 浪花已入账，可在钱包查看充值流水与最新余额。', detail: '200 浪花已入账。请前往钱包核对充值流水与最新余额；如金额不符，请保留付款凭证并联系充值客服。', time: '11 月 1 日 16:20', isRead: true, target: 'wallet' },
]
