import { initialAdminRewards, type AdminRewardItem } from '@/pages/admin/admin-reward-data'

export interface AdminLotteryPrize {
  id: number
  rewardId: number
  probability: number
  hasDraws: boolean
}

export interface AdminLotteryCampaign {
  id: number
  name: string
  pointCost: number
  enabled: boolean
  startsAt: string
  endsAt: string
  dailyLimit: number
  rules: string
  version: number
  prizes: AdminLotteryPrize[]
}

export const LOTTERY_PITY_REWARD_CODE = 'lottery-experience-68'
export const LOTTERY_PITY_LIMIT = 240

const extraRewards: AdminRewardItem[] = [
  {
    id: 109, code: LOTTERY_PITY_REWARD_CODE, vipQuotaKind: '', audience: 'BOSS', name: '68 体验单', kind: 'SERVICE', imageUrl: '',
    description: '抽奖专属服务权益，由俱乐部安排履约，不作为通用优惠券使用。', pointCost: 0, pointValue: 0, balanceCoins: 0,
    durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: null, redeemedCount: 72, isActive: true, inShop: false, sortOrder: 0,
  },
  {
    id: 110, code: 'lottery-points-20', vipQuotaKind: '', audience: 'ALL', name: '20 积分', kind: 'POINTS', imageUrl: '', description: '中奖后自动发放 20 积分。',
    pointCost: 0, pointValue: 20, balanceCoins: 0, durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: null, redeemedCount: 1680, isActive: true, inShop: false, sortOrder: 0,
  },
  {
    id: 111, code: 'lottery-points-68', vipQuotaKind: '', audience: 'ALL', name: '68 积分', kind: 'POINTS', imageUrl: '', description: '中奖后自动发放 68 积分。',
    pointCost: 0, pointValue: 68, balanceCoins: 0, durationDays: 0, durationMonths: 0, highlighted: false, serviceId: null, stock: null, redeemedCount: 620, isActive: true, inShop: false, sortOrder: 0,
  },
  {
    id: 112, code: 'lottery-highlight-tag', vipQuotaKind: '', audience: 'BOSS', name: '限定高亮 TAG', kind: 'TAG', imageUrl: '', description: '中奖后提交 TAG 文案，审核通过后展示。',
    pointCost: 0, pointValue: 0, balanceCoins: 0, durationDays: 30, durationMonths: 0, highlighted: true, serviceId: null, stock: null, redeemedCount: 94, isActive: true, inShop: false, sortOrder: 0,
  },
]

export const adminLotteryRewardOptions: AdminRewardItem[] = [...initialAdminRewards, ...extraRewards]

export const initialAdminLotteryCampaigns: AdminLotteryCampaign[] = [
  {
    id: 301, name: '海浪幸运季', pointCost: 60, enabled: true, startsAt: '2026-11-01T00:00:00', endsAt: '2026-12-31T23:59:00', dailyLimit: 20, version: 12,
    rules: '每抽消耗 60 积分，十连消耗 600 积分。积分奖励自动到账，其他奖励请前往中奖记录领取并补充交付资料。',
    prizes: [
      { id: 401, rewardId: 110, probability: 6200, hasDraws: true },
      { id: 402, rewardId: 111, probability: 2400, hasDraws: true },
      { id: 403, rewardId: 102, probability: 800, hasDraws: false },
      { id: 404, rewardId: 112, probability: 400, hasDraws: true },
      { id: 405, rewardId: 109, probability: 150, hasDraws: true },
      { id: 406, rewardId: 101, probability: 50, hasDraws: false },
    ],
  },
  {
    id: 300, name: '夏日浪花季', pointCost: 50, enabled: false, startsAt: '2026-08-01T00:00:00', endsAt: '2026-09-30T23:59:00', dailyLimit: 10, version: 8,
    rules: '历史活动已结束，仅用于核对往期奖池。',
    prizes: [
      { id: 399, rewardId: 110, probability: 8500, hasDraws: true },
      { id: 400, rewardId: 109, probability: 1500, hasDraws: true },
    ],
  },
]

export function getAdminLotteryReward(prize: Pick<AdminLotteryPrize, 'rewardId'>) {
  return adminLotteryRewardOptions.find(reward => reward.id === prize.rewardId)
}

export function getAdminLotteryRarity(reward: AdminRewardItem) {
  return reward.code === LOTTERY_PITY_REWARD_CODE ? 'SSR' : reward.kind === 'POINTS' ? 'R' : 'SR'
}

export function formatAdminLotteryTime(value: string) {
  return value ? value.replace('T', ' ').slice(0, 16) : '不限'
}

export function getAdminLotteryPublicState(campaign: AdminLotteryCampaign) {
  const now = new Date('2026-11-04T12:00:00').getTime()
  if (!campaign.enabled) return 'DISABLED'
  if (campaign.startsAt && new Date(campaign.startsAt).getTime() > now) return 'SCHEDULED'
  if (campaign.endsAt && new Date(campaign.endsAt).getTime() <= now) return 'ENDED'
  return 'OPEN'
}

export function getAdminLotteryValidation(campaign: AdminLotteryCampaign) {
  if (!campaign.name.trim()) return '请填写活动名称'
  if (campaign.name.trim().length > 100) return '活动名称最多 100 个字符'
  if (!Number.isInteger(campaign.pointCost) || campaign.pointCost < 1) return '请先配置每次抽奖消耗的积分'
  if (!Number.isInteger(campaign.dailyLimit) || campaign.dailyLimit < 0) return '每天次数上限须为不小于 0 的整数'
  if (campaign.startsAt && campaign.endsAt && new Date(campaign.endsAt).getTime() <= new Date(campaign.startsAt).getTime()) return '结束时间必须晚于开始时间'
  if (campaign.prizes.length < 2 || campaign.prizes.length > 20) return '奖项数量应为 2–20 个'
  const rewardIds = new Set<number>()
  for (const prize of campaign.prizes) {
    if (!prize.rewardId) return '请为每个奖项选择奖品'
    if (rewardIds.has(prize.rewardId)) return '同一奖品不能重复配置'
    rewardIds.add(prize.rewardId)
    if (!Number.isInteger(prize.probability) || prize.probability < 0 || prize.probability > 10000) return '中奖概率须为 0–10000 的整数'
  }
  if (campaign.enabled) {
    if (campaign.prizes.reduce((sum, prize) => sum + prize.probability, 0) !== 10000) return '所有奖项概率之和必须为 10000（100%）'
    const target = campaign.prizes.find(prize => getAdminLotteryReward(prize)?.code === LOTTERY_PITY_REWARD_CODE)
    if (target) {
      const reward = getAdminLotteryReward(target)
      if (reward && (reward.kind !== 'SERVICE' || reward.inShop)) return '68 体验单必须配置为不上架商城的专属服务权益'
    }
    const eligible = campaign.prizes.filter(prize => prize.probability > 0 || prize === target)
    if (eligible.some(prize => !getAdminLotteryReward(prize)?.isActive)) return '具有中奖概率的奖品必须全部启用'
    if (eligible.some(prize => !['ALL', 'BOSS'].includes(getAdminLotteryReward(prize)?.audience || ''))) return '抽奖奖品必须允许老板领取'
    if (eligible.some(prize => {
      const stock = getAdminLotteryReward(prize)?.stock
      return stock !== null && stock !== undefined && stock < 1
    })) return '奖品库存不足，抽奖暂时关闭，请联系俱乐部补货'
  }
  return ''
}

export function getAdminLotteryTenUnavailableReason(campaign: AdminLotteryCampaign) {
  const validation = getAdminLotteryValidation({ ...campaign, enabled: true })
  if (validation) return validation
  const candidates = campaign.prizes.filter(prize => {
    const reward = getAdminLotteryReward(prize)
    return prize.probability > 0 && reward && reward.kind !== 'POINTS' && reward.isActive && ['ALL', 'BOSS'].includes(reward.audience) && (reward.stock === null || reward.stock > 0)
  })
  return candidates.length ? '' : '奖池尚未配置正概率非积分奖，暂不支持十连抽'
}
