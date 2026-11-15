import { initialAdminLevels } from '@/pages/admin/admin-level-data'

export type AdminRegistrationStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type AdminDepositMode = 'CASH' | 'EARNINGS'

export interface AdminRegistration {
  id: number
  username: string
  displayName: string
  realName: string
  phone: string
  idNumber: string
  idNumberMasked: string
  status: AdminRegistrationStatus
  depositMode: AdminDepositMode
  depositRequired: number
  depositPaid: number
  levelName: string
  submittedAt: string
  reviewedAt: string
  reviewNote: string
  reviewerName: string
  revision: number
  idFrontAvailable: boolean
  idBackAvailable: boolean
  depositProofAvailable: boolean
}

export const adminRegistrationStatusLabel: Record<AdminRegistrationStatus, string> = {
  PENDING: '待审核',
  APPROVED: '已通过',
  REJECTED: '需补充资料',
}

export const adminDepositModeLabel: Record<AdminDepositMode, string> = {
  CASH: '现金缴纳',
  EARNINGS: '单抵双倍',
}

export const adminRegistrationLevels = initialAdminLevels
  .filter(level => level.active)
  .map(({ id, name }) => ({ id, name }))

export const adminRegistrations: AdminRegistration[] = [
  {
    id: 2048, username: 'qingning_game', displayName: '青柠', realName: '林清宁', phone: '138****6208',
    idNumber: '330106********1624', idNumberMasked: '330106********1624', status: 'PENDING',
    depositMode: 'CASH', depositRequired: 500, depositPaid: 0, levelName: '', submittedAt: '2026-11-04 16:42',
    reviewedAt: '', reviewNote: '', reviewerName: '', revision: 3,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: true,
  },
  {
    id: 2047, username: 'xiaoyu_77', displayName: '小屿', realName: '陈雨泽', phone: '186****3712',
    idNumber: '310115********0837', idNumberMasked: '310115********0837', status: 'PENDING',
    depositMode: 'EARNINGS', depositRequired: 1000, depositPaid: 0, levelName: '', submittedAt: '2026-11-04 15:18',
    reviewedAt: '', reviewNote: '', reviewerName: '', revision: 1,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: false,
  },
  {
    id: 2046, username: 'moli_play', displayName: '茉莉', realName: '周茉', phone: '159****4086',
    idNumber: '440305********2042', idNumberMasked: '440305********2042', status: 'PENDING',
    depositMode: 'CASH', depositRequired: 500, depositPaid: 0, levelName: '', submittedAt: '2026-11-04 11:06',
    reviewedAt: '', reviewNote: '', reviewerName: '', revision: 2,
    idFrontAvailable: true, idBackAvailable: false, depositProofAvailable: true,
  },
  {
    id: 2045, username: 'beichen_gg', displayName: '北辰', realName: '顾北辰', phone: '137****9521',
    idNumber: '320102********3516', idNumberMasked: '320102********3516', status: 'REJECTED',
    depositMode: 'CASH', depositRequired: 500, depositPaid: 0, levelName: '', submittedAt: '2026-11-03 19:26',
    reviewedAt: '2026-11-04 10:12', reviewNote: '身份证国徽面反光，有效期无法辨认；请重新上传清晰的原件照片。',
    reviewerName: '审核管理员 · 岚', revision: 2,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: true,
  },
  {
    id: 2044, username: 'taotao_win', displayName: '桃桃', realName: '唐晓桃', phone: '135****1180',
    idNumber: '510107********4829', idNumberMasked: '510107********4829', status: 'REJECTED',
    depositMode: 'EARNINGS', depositRequired: 1000, depositPaid: 0, levelName: '', submittedAt: '2026-11-03 16:40',
    reviewedAt: '2026-11-03 18:05', reviewNote: '结单昵称与已有陪玩重复，请修改昵称后重新提交。',
    reviewerName: '审核管理员 · 岚', revision: 1,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: false,
  },
  {
    id: 2043, username: 'deer_companion', displayName: '小鹿', realName: '陆晓鹿', phone: '138****2601',
    idNumber: '330103********1228', idNumberMasked: '330103********1228', status: 'APPROVED',
    depositMode: 'CASH', depositRequired: 500, depositPaid: 500, levelName: '正式陪玩', submittedAt: '2026-11-02 14:32',
    reviewedAt: '2026-11-02 16:08', reviewNote: '实名与押金凭证核对无误。', reviewerName: '审核管理员 · 岚', revision: 4,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: true,
  },
  {
    id: 2042, username: 'nana_game', displayName: '奈奈', realName: '许奈', phone: '135****8082',
    idNumber: '310104********2026', idNumberMasked: '310104********2026', status: 'APPROVED',
    depositMode: 'EARNINGS', depositRequired: 1600, depositPaid: 0, levelName: '资深陪玩', submittedAt: '2026-11-01 10:20',
    reviewedAt: '2026-11-01 11:45', reviewNote: '实名材料完整，按单抵方式开通。', reviewerName: '审核管理员 · 岚', revision: 3,
    idFrontAvailable: true, idBackAvailable: true, depositProofAvailable: false,
  },
]
