export interface AdminChatMessage {
  id: number
  sender: 'user' | 'staff'
  body: string
  createdAt: string
  image?: string
  clientMessageId?: string
}

export interface AdminChatConversation {
  id: number
  userId: number
  nickname: string
  username: string
  accountNo: string
  unreadSupport: number
  lastMessage: string
  lastMessageAt: string
  messages: AdminChatMessage[]
  olderMessages: AdminChatMessage[]
}

export const adminChatConversations: AdminChatConversation[] = [
  {
    id: 501,
    userId: 1001,
    nickname: '海边听风',
    username: 'boss_sea',
    accountNo: 'SL-U-1001',
    unreadSupport: 3,
    lastMessage: '开局前如果需要换区，麻烦提前告诉我。',
    lastMessageAt: '16:48',
    messages: [
      { id: 9101, sender: 'staff', body: '你好，这里是声浪电竞客服。订单和服务问题都可以留言，我们会按同一会话接续处理。', createdAt: '16:31' },
      { id: 9102, sender: 'user', body: '你好，请帮我核对订单 ORD6B21A90C4F7302D8E110 的开局时间。', createdAt: '16:42' },
      { id: 9103, sender: 'user', body: '这是下单时的服务说明截图。', image: './content-order-guide.svg', createdAt: '16:43' },
      { id: 9104, sender: 'staff', body: '已查到这笔订单，计划今天 20:00 开始，使用艾欧尼亚区。', createdAt: '16:45' },
      { id: 9105, sender: 'user', body: '开局前如果需要换区，麻烦提前告诉我。', createdAt: '16:48' },
    ],
    olderMessages: [
      { id: 9097, sender: 'user', body: '今晚想预约双排，需要先在这里登记游戏昵称吗？', createdAt: '昨天 19:12' },
      { id: 9098, sender: 'staff', body: '下单时会单独确认游戏区和昵称，无需重复登记。', createdAt: '昨天 19:15' },
    ],
  },
  {
    id: 502,
    userId: 1002,
    nickname: '一杯美式',
    username: 'coffee_daily',
    accountNo: 'SL-U-1002',
    unreadSupport: 1,
    lastMessage: '双陪两位都已经进队了，谢谢。',
    lastMessageAt: '16:26',
    messages: [
      { id: 9201, sender: 'user', body: '双陪订单需要分别确认两位陪玩进队吗？', createdAt: '16:19' },
      { id: 9202, sender: 'staff', body: '是的，两位陪玩会分别核对入队和结单凭证。', createdAt: '16:22' },
      { id: 9203, sender: 'user', body: '双陪两位都已经进队了，谢谢。', createdAt: '16:26' },
    ],
    olderMessages: [],
  },
  {
    id: 503,
    userId: 1003,
    nickname: '北纬二十七',
    username: 'north_27',
    accountNo: 'SL-U-1003',
    unreadSupport: 0,
    lastMessage: '那我完成支付后再联系你们。',
    lastMessageAt: '15:58',
    messages: [
      { id: 9301, sender: 'user', body: '未支付的订单可以先安排陪玩吗？订单片段 ORD123 能查到吗？', createdAt: '15:51' },
      { id: 9302, sender: 'staff', body: '需要完成支付后才会进入派单流程。片段订单号无法精确查询，请提供完整订单号。', createdAt: '15:55' },
      { id: 9303, sender: 'user', body: '那我完成支付后再联系你们。', createdAt: '15:58' },
    ],
    olderMessages: [],
  },
  {
    id: 504,
    userId: 1004,
    nickname: '星河入梦',
    username: 'star_dream',
    accountNo: 'SL-U-1004',
    unreadSupport: 2,
    lastMessage: '礼物的附言可以修改吗？',
    lastMessageAt: '15:36',
    messages: [
      { id: 9401, sender: 'user', body: '礼物单已经显示了，想确认附言是否一起展示。', createdAt: '15:31' },
      { id: 9402, sender: 'staff', body: '陪玩会在礼物单中看到附言，请留意内容不包含联系方式。', createdAt: '15:33' },
      { id: 9403, sender: 'user', body: '礼物的附言可以修改吗？', createdAt: '15:35' },
      { id: 9404, sender: 'user', body: '如果还没送出，想改成“今晚也要开心”。', createdAt: '15:36' },
    ],
    olderMessages: [],
  },
  {
    id: 505,
    userId: 1005,
    nickname: '橘子海',
    username: 'orange_sea',
    accountNo: 'SL-U-1005',
    unreadSupport: 0,
    lastMessage: '收到，我会按约定时间上线。',
    lastMessageAt: '14:52',
    messages: [
      { id: 9501, sender: 'staff', body: '已和陪玩确认 17:00 开局，请提前保持游戏在线。', createdAt: '14:48' },
      { id: 9502, sender: 'user', body: '收到，我会按约定时间上线。', createdAt: '14:52' },
    ],
    olderMessages: [],
  },
]

export const adminChatOrderNumber = 'ORD6B21A90C4F7302D8E110'
