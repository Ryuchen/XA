export type AuditionEntryStatus = 'ACTIVE' | 'DISABLED' | 'EXPIRED'
export type AuditionBindingKind = 'account' | 'legacy' | 'unbound'

export interface AuditionBinding {
  kind: AuditionBindingKind
  accountId?: number
  name?: string
  accountNo?: string
  username?: string
  available?: boolean
  otpRequired?: boolean
}

export interface AdminAuditionLink {
  id: number
  title: string
  remark: string
  expireAt: string | null
  active: boolean
  entryStatus: AuditionEntryStatus
  bossBinding: AuditionBinding
  providerBinding: AuditionBinding
  bossUrlPlaceholder: boolean
  providerUrlPlaceholder: boolean
  createdAt: string
  hasSignups: boolean
}

export interface AuditionAccountOption {
  id: number
  role: 'boss' | 'provider'
  name: string
  accountNo: string
  username: string
}

export const initialAdminAuditionLinks: AdminAuditionLink[] = [
  {
    id: 108,
    title: '11 月端游陪玩招募',
    remark: '端游技术陪玩专场，报名后由客服安排试音时间。',
    expireAt: '2026-11-15T20:00',
    active: true,
    entryStatus: 'ACTIVE',
    bossBinding: { kind: 'account', accountId: 301, name: '林先生', accountNo: 'B-0301', username: 'boss_lin', available: true },
    providerBinding: { kind: 'unbound' },
    bossUrlPlaceholder: false,
    providerUrlPlaceholder: false,
    createdAt: '2026-11-03 14:20',
    hasSignups: true,
  },
  {
    id: 107,
    title: '手游技术陪玩试音',
    remark: '和平精英与王者荣耀方向，需准备常用设备与清晰麦克风。',
    expireAt: '2026-11-30T18:00',
    active: true,
    entryStatus: 'ACTIVE',
    bossBinding: { kind: 'unbound' },
    providerBinding: { kind: 'account', accountId: 102, name: '小岚', accountNo: 'PW-0102', username: 'provider_lan', available: true, otpRequired: true },
    bossUrlPlaceholder: false,
    providerUrlPlaceholder: false,
    createdAt: '2026-11-02 10:05',
    hasSignups: false,
  },
  {
    id: 106,
    title: '十月资深陪玩补招',
    remark: '历史补招入口，已停止接受新的报名。',
    expireAt: null,
    active: false,
    entryStatus: 'DISABLED',
    bossBinding: { kind: 'legacy', name: '旧版老板', username: 'legacy_boss' },
    providerBinding: { kind: 'unbound' },
    bossUrlPlaceholder: false,
    providerUrlPlaceholder: false,
    createdAt: '2026-10-18 16:40',
    hasSignups: true,
  },
  {
    id: 105,
    title: '九月新声体验营',
    remark: '已结束的新手体验活动，保留入口状态供核对。',
    expireAt: '2026-09-30T20:00',
    active: true,
    entryStatus: 'EXPIRED',
    bossBinding: { kind: 'account', accountId: 302, name: '周先生', accountNo: 'B-0302', username: 'boss_zhou', available: false },
    providerBinding: { kind: 'unbound' },
    bossUrlPlaceholder: false,
    providerUrlPlaceholder: false,
    createdAt: '2026-09-12 09:30',
    hasSignups: true,
  },
]

export const auditionAccountOptions: AuditionAccountOption[] = [
  { id: 301, role: 'boss', name: '林先生', accountNo: 'B-0301', username: 'boss_lin' },
  { id: 303, role: 'boss', name: '岚声体验', accountNo: 'B-0303', username: 'boss_demo' },
  { id: 102, role: 'provider', name: '小岚', accountNo: 'PW-0102', username: 'provider_lan' },
  { id: 108, role: 'provider', name: '青禾', accountNo: 'PW-0108', username: 'provider_qinghe' },
]
