export interface AdminLevel {
  id: number
  name: string
  commissionRate: number
  remark: string
  sortOrder: number
  active: boolean
  impact: {
    providers: number
    services: number
    registrations: number
  }
}

export const initialAdminLevels: AdminLevel[] = [
  { id: 1, name: '见习陪玩', commissionRate: 25, remark: '新入驻陪玩，完成基础实名与资料核验后开通。', sortOrder: 10, active: true, impact: { providers: 1, services: 2, registrations: 0 } },
  { id: 2, name: '正式陪玩', commissionRate: 20, remark: '默认正式等级，适用于常规陪玩与部分手工报单。', sortOrder: 20, active: true, impact: { providers: 4, services: 8, registrations: 1 } },
  { id: 3, name: '资深陪玩', commissionRate: 15, remark: '服务记录稳定、具备进阶接单能力的陪玩。', sortOrder: 30, active: true, impact: { providers: 2, services: 5, registrations: 1 } },
  { id: 4, name: '王牌陪玩', commissionRate: 12, remark: '高水平服务与稳定履约记录，由运营复核后调整。', sortOrder: 40, active: true, impact: { providers: 1, services: 3, registrations: 0 } },
  { id: 5, name: '荣誉陪玩', commissionRate: 10, remark: '历史活动等级，当前不再用于新申请。', sortOrder: 50, active: false, impact: { providers: 0, services: 1, registrations: 2 } },
]

export interface AdminScopeRule {
  id: string
  scope: string
  grades: { label: string; commissionRate: number | null }[]
}

export const adminScopeRules: AdminScopeRule[] = [
  { id: 'pc-companion', scope: '端游 · 陪玩', grades: [
    { label: '娱乐', commissionRate: 20 }, { label: '技术', commissionRate: 20 }, { label: '大师', commissionRate: 20 }, { label: '王者', commissionRate: 20 }, { label: '传奇', commissionRate: 20 }, { label: '荣耀', commissionRate: 20 }, { label: '明星', commissionRate: 20 },
  ] },
  { id: 'mobile-companion', scope: '手游 · 陪玩', grades: [
    { label: '娱乐', commissionRate: 20 }, { label: '技术', commissionRate: 20 }, { label: '大师', commissionRate: 20 }, { label: '王者', commissionRate: 20 }, { label: '传奇', commissionRate: 20 }, { label: '荣耀', commissionRate: 20 }, { label: '明星', commissionRate: 20 },
  ] },
  { id: 'pc-confidential-guard', scope: '端游 · 机密 · 护航', grades: [
    { label: '大师', commissionRate: 25 }, { label: '传奇', commissionRate: 20 },
  ] },
  { id: 'mobile-confidential-guard', scope: '手游 · 机密 · 护航', grades: [
    { label: '大师', commissionRate: 30 }, { label: '传奇', commissionRate: 25 },
  ] },
  { id: 'pc-top-secret-guard', scope: '端游 · 绝密 · 护航', grades: [
    { label: '王者', commissionRate: 24 }, { label: '大师', commissionRate: 22 }, { label: '传奇', commissionRate: 20 }, { label: '荣耀', commissionRate: 18 }, { label: '明星', commissionRate: 15 },
  ] },
  { id: 'mobile-top-secret-guard', scope: '手游 · 绝密 · 护航', grades: [
    { label: '大师', commissionRate: 25 }, { label: '传奇', commissionRate: 20 },
  ] },
  { id: 'pc-grind', scope: '端游 · 代肝', grades: [{ label: '不分档', commissionRate: null }] },
  { id: 'mobile-grind', scope: '手游 · 代肝', grades: [{ label: '不分档', commissionRate: null }] },
  { id: 'pc-rank', scope: '端游 · 上分', grades: [{ label: '不分档', commissionRate: null }] },
  { id: 'mobile-rank', scope: '手游 · 上分', grades: [{ label: '不分档', commissionRate: null }] },
]
