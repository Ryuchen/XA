export type AdminCommissionType = 'PERCENT' | 'FIXED'

export interface AdminCommissionConfig {
  type: AdminCommissionType
  rate: number
  fixedRaw: number
  updatedAt: string
}

export interface AdminCommissionShare {
  provider: string
  baseRaw: number
  commissionRaw: number
  incomeRaw: number
}

export interface AdminCommissionQuote {
  amountRaw: number
  commissionRaw: number
  incomeRaw: number
  shares: AdminCommissionShare[]
}

export const initialAdminCommissionConfig: AdminCommissionConfig = {
  type: 'PERCENT',
  rate: 20,
  fixedRaw: 0,
  updatedAt: '2026-11-04 16:30',
}

export const fixedAdminCommissionConfig: AdminCommissionConfig = {
  type: 'FIXED',
  rate: 20,
  fixedRaw: 300,
  updatedAt: '2026-11-04 16:30',
}

export const adminCommissionPriority = [
  { name: '已有订单', rule: '订单分账快照', description: '历史配置变更不回写已创建订单。', locked: true },
  { name: '礼物订单', rule: '礼物独立抽成', description: '不使用订单默认固定金额或百分比。', locked: true },
  { name: '商品与活动', rule: '商品自定义／活动配置', description: '命中明确配置时，以派单试算结果为准。', locked: false },
  { name: '自动策略', rule: '订单档位／体验／陪玩', description: '自动规则优先于普通订单全局默认值。', locked: false },
  { name: '后续普通订单', rule: '本页全局默认配置', description: '仅在未命中上述规则时使用。', locked: false },
]

import { formatWaveAmount } from '@/lib/format'

export function formatCommissionRaw(value: number) {
  return formatWaveAmount(value / 10)
}

export function parseCommissionCoins(value: string) {
  if (!/^(?:0|[1-9]\d{0,8})$/.test(value)) return null
  const raw = Number(value) * 10
  return Number.isSafeInteger(raw) && raw > 0 ? raw : null
}

export function quoteAdminCommission(amountRaw: number, providerCount: 1 | 2, config: AdminCommissionConfig): AdminCommissionQuote {
  const base = Math.floor(amountRaw / providerCount)
  const remainder = amountRaw % providerCount
  const bases = Array.from({ length: providerCount }, (_, index) => base + (index < remainder ? 1 : 0))
  let cuts: number[]
  if (config.type === 'FIXED') {
    const total = Math.min(config.fixedRaw, amountRaw)
    cuts = bases.map(value => Math.floor(total * value / amountRaw))
    let remaining = total - cuts.reduce((sum, value) => sum + value, 0)
    const indices = bases.map((value, index) => ({ index, remainder: (total * value) % amountRaw }))
      .sort((a, b) => b.remainder - a.remainder || a.index - b.index)
    for (const item of indices) {
      if (remaining <= 0) break
      cuts[item.index] += 1
      remaining -= 1
    }
  } else {
    cuts = bases.map(value => Math.floor(value * config.rate / 100))
  }
  const names = providerCount === 1 ? ['陪玩'] : ['主陪', '副陪']
  const shares = bases.map((baseRaw, index) => ({
    provider: names[index],
    baseRaw,
    commissionRaw: cuts[index],
    incomeRaw: baseRaw - cuts[index],
  }))
  const commissionRaw = cuts.reduce((sum, value) => sum + value, 0)
  return { amountRaw, commissionRaw, incomeRaw: amountRaw - commissionRaw, shares }
}
