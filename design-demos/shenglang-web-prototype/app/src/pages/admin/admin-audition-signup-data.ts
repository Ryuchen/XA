export type AuditionSignupStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export interface AdminAuditionSignup {
  id: number
  linkId: number
  linkTitle: string
  applicantName: string
  applicantNo: string
  applicantUsername: string
  legacy?: boolean
  contact: string
  game: string
  remark: string
  status: AuditionSignupStatus
  auditRemark: string
  auditorName: string
  auditedAt: string | null
  createdAt: string
}

export const initialAdminAuditionSignups: AdminAuditionSignup[] = [
  {
    id: 5011,
    linkId: 108,
    linkTitle: '11 月端游陪玩招募',
    applicantName: '青禾',
    applicantNo: 'PW-0108',
    applicantUsername: 'provider_qinghe',
    contact: '微信 qh_demo',
    game: '无畏契约',
    remark: '工作日晚间可试音，使用独立声卡与电容麦。',
    status: 'PENDING',
    auditRemark: '',
    auditorName: '',
    auditedAt: null,
    createdAt: '2026-11-04 09:42',
  },
  {
    id: 5010,
    linkId: 107,
    linkTitle: '手游技术陪玩试音',
    applicantName: '小岚',
    applicantNo: 'PW-0102',
    applicantUsername: 'provider_lan',
    contact: '微信 lan_demo',
    game: '王者荣耀',
    remark: '主玩打野，周末全天可安排试音。',
    status: 'PENDING',
    auditRemark: '',
    auditorName: '',
    auditedAt: null,
    createdAt: '2026-11-04 09:18',
  },
  {
    id: 5009,
    linkId: 108,
    linkTitle: '11 月端游陪玩招募',
    applicantName: '北岛',
    applicantNo: 'PW-0098',
    applicantUsername: 'provider_beidao',
    contact: '微信 bd_demo',
    game: '英雄联盟',
    remark: '语音清晰，可配合晚间试音。',
    status: 'APPROVED',
    auditRemark: '',
    auditorName: '客服岚',
    auditedAt: '2026-11-03 19:26',
    createdAt: '2026-11-03 18:40',
  },
  {
    id: 5008,
    linkId: 106,
    linkTitle: '十月资深陪玩补招',
    applicantName: '旧版试音账号',
    applicantNo: '',
    applicantUsername: 'audition_legacy',
    legacy: true,
    contact: '微信 legacy_demo',
    game: '绝地求生',
    remark: '历史报名记录。',
    status: 'REJECTED',
    auditRemark: '当前设备底噪较明显，请调整录音环境后重新报名。',
    auditorName: '客服岚',
    auditedAt: '2026-10-20 15:12',
    createdAt: '2026-10-20 14:35',
  },
  {
    id: 5007,
    linkId: 105,
    linkTitle: '九月新声体验营',
    applicantName: '阿泽',
    applicantNo: 'PW-0086',
    applicantUsername: 'provider_aze',
    contact: '微信 az_demo',
    game: '和平精英',
    remark: '体验营历史报名。',
    status: 'APPROVED',
    auditRemark: '',
    auditorName: '客服岚',
    auditedAt: '2026-09-16 20:05',
    createdAt: '2026-09-16 18:22',
  },
]
