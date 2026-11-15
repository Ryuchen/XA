export type AdminBroadcastKind = 'NAMING' | 'RECHARGE' | 'UPGRADE' | 'REVIEW' | 'BIRTHDAY' | 'GIFT' | 'LOTTERY' | 'LOTTERY_SERVICE' | 'LOTTERY_NAMING' | 'LOTTERY_TAG'

export interface AdminBroadcastRuleItem {
  id: number
  kind: AdminBroadcastKind
  template: string
  enabled: boolean
  visibleHours: number
  sortOrder: number
  maskNickname: boolean
}

export const broadcastKindLabels: Record<AdminBroadcastKind, string> = {
  NAMING: '冠名播报',
  RECHARGE: '充值播报',
  UPGRADE: '升级播报',
  REVIEW: '好评广播',
  BIRTHDAY: '生日祝福',
  GIFT: '礼物单播报',
  LOTTERY: '转盘中奖播报',
  LOTTERY_SERVICE: '抽中体验单',
  LOTTERY_NAMING: '抽中冠名',
  LOTTERY_TAG: '抽中 TAG',
}

export const initialAdminBroadcastRules: AdminBroadcastRuleItem[] = [
  { id: 1, kind: 'NAMING', template: '{nickname} 获得了冠名权益「{reward}」', enabled: true, visibleHours: 72, sortOrder: 80, maskNickname: false },
  { id: 2, kind: 'RECHARGE', template: '{nickname} 充值成功，感谢对俱乐部的支持', enabled: true, visibleHours: 24, sortOrder: 70, maskNickname: true },
  { id: 3, kind: 'UPGRADE', template: '恭喜 {nickname} 升级为 {level}', enabled: true, visibleHours: 72, sortOrder: 60, maskNickname: false },
  { id: 4, kind: 'REVIEW', template: '{nickname} 给 {provider} 送上了好评', enabled: true, visibleHours: 48, sortOrder: 50, maskNickname: true },
  { id: 5, kind: 'BIRTHDAY', template: '今天是 {nickname} 的生日，声浪电竞祝你生日快乐！', enabled: true, visibleHours: 24, sortOrder: 40, maskNickname: false },
  { id: 6, kind: 'GIFT', template: '{nickname} 送给 {provider}「{reward}」', enabled: true, visibleHours: 72, sortOrder: 90, maskNickname: false },
  { id: 7, kind: 'LOTTERY', template: '恭喜 {nickname} 抽中「{reward}」', enabled: true, visibleHours: 72, sortOrder: 100, maskNickname: true },
  { id: 8, kind: 'LOTTERY_SERVICE', template: '恭喜 {nickname} 抽中「{reward}」', enabled: true, visibleHours: 72, sortOrder: 95, maskNickname: true },
  { id: 9, kind: 'LOTTERY_NAMING', template: '恭喜 {nickname} 抽中「{reward}」', enabled: true, visibleHours: 72, sortOrder: 94, maskNickname: false },
  { id: 10, kind: 'LOTTERY_TAG', template: '恭喜 {nickname} 抽中「{reward}」', enabled: false, visibleHours: 24, sortOrder: 93, maskNickname: true },
]
