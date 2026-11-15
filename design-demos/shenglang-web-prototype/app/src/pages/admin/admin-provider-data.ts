export type AdminProviderStatus = 'AVAILABLE' | 'BUSY' | 'OFFLINE'
export type AdminProviderKind = 'COMPANION' | 'GUARD' | 'GRIND' | 'RANK'

export interface AdminProviderQualification {
  id: number
  game: string
  kind: AdminProviderKind
  platform: 'PC' | 'MOBILE'
  securityLevel: 'ANY' | 'CONFIDENTIAL' | 'TOP_SECRET'
  grade: string
  pricePerHour: number | null
  active: boolean
}

export interface AdminProvider {
  id: number
  escortNo: string
  username: string
  displayName: string
  phone: string
  gender: 'MALE' | 'FEMALE' | 'UNKNOWN'
  city: string
  serviceArea: string
  status: AdminProviderStatus
  verified: boolean
  passTier: 'BLACK' | 'GOLD' | 'SILVER' | 'BRONZE' | 'NONE'
  recommendationSlot: number | null
  pricePerHour: number
  rating: number
  completedOrders: number
  rewardTotal: number
  penaltyTotal: number
  walletBalance: number
  depositMode: 'CASH' | 'EARNINGS'
  depositRequired: number
  depositPaid: number
  activeOrderCount: number
  bio: string
  rankTier: string
  winRate: number
  games: string[]
  serviceItems: string[]
  qualifications: AdminProviderQualification[]
}

export const adminProviderStatusLabel: Record<AdminProviderStatus, string> = {
  AVAILABLE: '空闲', BUSY: '忙碌', OFFLINE: '离线',
}

export const adminProviderKindLabel: Record<AdminProviderKind, string> = {
  COMPANION: '陪玩', GUARD: '护航', GRIND: '代肝', RANK: '上分',
}

export const adminPassTierLabel: Record<AdminProvider['passTier'], string> = {
  BLACK: '黑金通行证', GOLD: '黄金通行证', SILVER: '白银通行证', BRONZE: '青铜通行证', NONE: '无通行证',
}

const qualification = (
  id: number,
  game: string,
  grade: string,
  pricePerHour: number,
  overrides: Partial<AdminProviderQualification> = {},
): AdminProviderQualification => ({
  id, game, kind: 'COMPANION', platform: 'PC', securityLevel: 'ANY', grade, pricePerHour, active: true, ...overrides,
})

export const adminProviders: AdminProvider[] = [
  {
    id: 101, escortNo: 'PW-0101', username: 'provider_deer', displayName: '小鹿', phone: '138****2601', gender: 'FEMALE', city: '杭州', serviceArea: '电信一区 · 微信区',
    status: 'AVAILABLE', verified: true, passTier: 'GOLD', recommendationSlot: 1, pricePerHour: 76, rating: 4.98, completedOrders: 386, rewardTotal: 320, penaltyTotal: 0, walletBalance: 1280,
    depositMode: 'CASH', depositRequired: 500, depositPaid: 500, activeOrderCount: 0, bio: '温柔耐心，擅长辅助与双排指挥，可按老板节奏沟通。', rankTier: '超凡大师', winRate: 63.5,
    games: ['英雄联盟', '金铲铲之战'], serviceItems: ['英雄联盟 · 双排', '英雄联盟 · 进阶', '金铲铲之战 · 双人'],
    qualifications: [qualification(1, '英雄联盟', '大师', 76), qualification(2, '金铲铲之战', '技术', 68, { platform: 'MOBILE' })],
  },
  {
    id: 102, escortNo: 'PW-0102', username: 'provider_nana', displayName: '奈奈', phone: '135****8082', gender: 'FEMALE', city: '上海', serviceArea: '微信区 · QQ 区',
    status: 'BUSY', verified: true, passTier: 'BLACK', recommendationSlot: 2, pricePerHour: 88, rating: 4.96, completedOrders: 542, rewardTotal: 680, penaltyTotal: 30, walletBalance: 2360,
    depositMode: 'CASH', depositRequired: 800, depositPaid: 800, activeOrderCount: 1, bio: '主营王者荣耀与礼物互动，双陪时可负责气氛和沟通。', rankTier: '荣耀王者', winRate: 67.2,
    games: ['王者荣耀', '和平精英'], serviceItems: ['王者荣耀 · 双陪', '和平精英 · 双排', '礼物互动'],
    qualifications: [qualification(3, '王者荣耀', '王者', 88, { platform: 'MOBILE' }), qualification(4, '和平精英', '技术', 82, { platform: 'MOBILE' })],
  },
  {
    id: 103, escortNo: 'PW-0103', username: 'provider_ache', displayName: '阿澈', phone: '186****1077', gender: 'MALE', city: '成都', serviceArea: '艾欧尼亚 · 黑色玫瑰',
    status: 'BUSY', verified: true, passTier: 'GOLD', recommendationSlot: null, pricePerHour: 108, rating: 4.93, completedOrders: 468, rewardTotal: 260, penaltyTotal: 60, walletBalance: 1890,
    depositMode: 'EARNINGS', depositRequired: 600, depositPaid: 600, activeOrderCount: 1, bio: '打野与上路主力，适合进阶上分和双陪协作。', rankTier: '最强王者', winRate: 71.8,
    games: ['英雄联盟', '无畏契约'], serviceItems: ['英雄联盟 · 进阶', '无畏契约 · 上分', '双陪协作'],
    qualifications: [qualification(5, '英雄联盟', '王者', 108), qualification(6, '无畏契约', '传奇', 96, { kind: 'RANK' })],
  },
  {
    id: 104, escortNo: 'PW-0104', username: 'provider_coco', displayName: '可可', phone: '159****4418', gender: 'FEMALE', city: '广州', serviceArea: '微信区',
    status: 'AVAILABLE', verified: true, passTier: 'SILVER', recommendationSlot: 3, pricePerHour: 72, rating: 4.91, completedOrders: 295, rewardTotal: 180, penaltyTotal: 0, walletBalance: 960,
    depositMode: 'CASH', depositRequired: 500, depositPaid: 500, activeOrderCount: 1, bio: '中路与辅助均可，双陪订单负责入队确认和结单凭证。', rankTier: '荣耀王者', winRate: 65.1,
    games: ['王者荣耀', '金铲铲之战'], serviceItems: ['王者荣耀 · 双陪', '金铲铲之战 · 双人'],
    qualifications: [qualification(7, '王者荣耀', '王者', 72, { platform: 'MOBILE' }), qualification(8, '金铲铲之战', '娱乐', 58, { platform: 'MOBILE' })],
  },
  {
    id: 105, escortNo: 'PW-0105', username: 'provider_qinghe', displayName: '青禾', phone: '177****3206', gender: 'FEMALE', city: '南京', serviceArea: '国服',
    status: 'OFFLINE', verified: false, passTier: 'NONE', recommendationSlot: null, pricePerHour: 52, rating: 4.82, completedOrders: 74, rewardTotal: 50, penaltyTotal: 0, walletBalance: 320,
    depositMode: 'EARNINGS', depositRequired: 500, depositPaid: 200, activeOrderCount: 0, bio: '新入驻陪玩，资料已保存，等待实名与资格复核。', rankTier: '钻石', winRate: 58.4,
    games: ['无畏契约'], serviceItems: ['无畏契约 · 体验'],
    qualifications: [qualification(9, '无畏契约', '娱乐', 52, { active: false })],
  },
  {
    id: 106, escortNo: 'PW-0106', username: 'provider_beichen', displayName: '北辰', phone: '132****9071', gender: 'MALE', city: '武汉', serviceArea: '国服 · 电信区',
    status: 'AVAILABLE', verified: true, passTier: 'BRONZE', recommendationSlot: null, pricePerHour: 66, rating: 4.88, completedOrders: 182, rewardTotal: 80, penaltyTotal: 20, walletBalance: 760,
    depositMode: 'CASH', depositRequired: 500, depositPaid: 500, activeOrderCount: 0, bio: '主接永劫无间三排与和平精英，晚间档期稳定。', rankTier: '无间修罗', winRate: 61.7,
    games: ['永劫无间', '和平精英'], serviceItems: ['永劫无间 · 三排', '和平精英 · 双排'],
    qualifications: [qualification(10, '永劫无间', '技术', 66), qualification(11, '和平精英', '技术', 62, { platform: 'MOBILE' })],
  },
  {
    id: 107, escortNo: 'PW-0107', username: 'provider_luoluo', displayName: '洛洛', phone: '150****1169', gender: 'FEMALE', city: '苏州', serviceArea: '微信区',
    status: 'OFFLINE', verified: true, passTier: 'SILVER', recommendationSlot: 4, pricePerHour: 62, rating: 4.95, completedOrders: 238, rewardTotal: 140, penaltyTotal: 0, walletBalance: 1100,
    depositMode: 'CASH', depositRequired: 500, depositPaid: 500, activeOrderCount: 0, bio: '轻松娱乐与连麦互动，支持礼物单和双人娱乐局。', rankTier: '至尊星耀', winRate: 59.6,
    games: ['王者荣耀', '金铲铲之战'], serviceItems: ['王者荣耀 · 娱乐', '礼物互动'],
    qualifications: [qualification(12, '王者荣耀', '娱乐', 62, { platform: 'MOBILE' })],
  },
  {
    id: 108, escortNo: 'PW-0108', username: 'provider_shiqi', displayName: '十七', phone: '未记录', gender: 'UNKNOWN', city: '西安', serviceArea: '待完善',
    status: 'OFFLINE', verified: false, passTier: 'NONE', recommendationSlot: null, pricePerHour: 0, rating: 0, completedOrders: 0, rewardTotal: 0, penaltyTotal: 0, walletBalance: 0,
    depositMode: 'CASH', depositRequired: 500, depositPaid: 0, activeOrderCount: 0, bio: '账号已创建，基础资料与服务资格待补充。', rankTier: '待完善', winRate: 0,
    games: [], serviceItems: [], qualifications: [],
  },
]
