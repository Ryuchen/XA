export type AdminDisposeType = 'REWARD' | 'PENALTY'

export interface AdminDisposeRecord {
  id: number
  accountNo: string
  nickname: string
  username: string
  disposeType: AdminDisposeType
  amount: number
  reason: string
  operatorName: string
  createdAt: string
}

export const adminDisposeTypeLabel: Record<AdminDisposeType, string> = {
  REWARD: '奖励',
  PENALTY: '罚款',
}

export const adminDisposeRecords: AdminDisposeRecord[] = [
  { id: 3108, accountNo: 'PW-0102', nickname: '奈奈', username: 'provider_nana', disposeType: 'REWARD', amount: 200, reason: '本周服务评价稳定，主动协助完成高难度双陪订单。', operatorName: '运营管理员', createdAt: '2026-11-04 10:26' },
  { id: 3107, accountNo: 'PW-0106', nickname: '北辰', username: 'provider_beichen', disposeType: 'PENALTY', amount: 20, reason: '已确认接单后迟到 12 分钟，老板仍在场并完成服务。', operatorName: '值班客服', createdAt: '2026-11-04 09:48' },
  { id: 3106, accountNo: 'PW-0101', nickname: '小鹿', username: 'provider_deer', disposeType: 'REWARD', amount: 120, reason: '周末活动服务质量优秀，结单凭证完整。', operatorName: '运营管理员', createdAt: '2026-11-03 22:15' },
  { id: 3105, accountNo: 'PW-0103', nickname: '阿澈', username: 'provider_ache', disposeType: 'PENALTY', amount: 60, reason: '服务期间未按约定保持沟通，经订单记录与双方说明核实。', operatorName: '值班主管', createdAt: '2026-11-03 20:06' },
  { id: 3104, accountNo: 'PW-0104', nickname: '可可', username: 'provider_coco', disposeType: 'REWARD', amount: 80, reason: '新老板首单引导清晰，服务后收到专项表扬。', operatorName: '运营管理员', createdAt: '2026-11-03 16:42' },
  { id: 3103, accountNo: 'PW-0107', nickname: '洛洛', username: 'provider_luoluo', disposeType: 'REWARD', amount: 60, reason: '俱乐部活动协助奖励。', operatorName: '活动运营', createdAt: '2026-11-02 23:30' },
  { id: 3102, accountNo: 'PW-0102', nickname: '奈奈', username: 'provider_nana', disposeType: 'PENALTY', amount: 30, reason: '未在约定时间更新接单状态，经提醒后补录。', operatorName: '值班客服', createdAt: '2026-11-02 18:08' },
  { id: 3101, accountNo: 'PW-0105', nickname: '青禾', username: 'provider_qinghe', disposeType: 'REWARD', amount: 50, reason: '', operatorName: '运营管理员', createdAt: '2026-11-01 14:20' },
]
