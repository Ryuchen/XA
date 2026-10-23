import { amountToXaCoin, xaCoinToAmount } from './format'

export type CommissionType = 'PERCENT' | 'FIXED'
export interface CommissionConfig {
  commission_type?: string | null
  commission_rate?: number | null
  commission_fixed?: number | null
  rate?: number | null
}

export function readCommission(config: CommissionConfig = {}, fallbackRate = 0) {
  return {
    type: (config.commission_type === 'FIXED' ? 'FIXED' : 'PERCENT') as CommissionType,
    rate: config.commission_rate ?? config.rate ?? fallbackRate,
    fixed: Number(amountToXaCoin(config.commission_fixed ?? 0)),
  }
}

export function commissionConfigured(config: CommissionConfig): boolean {
  return config.commission_type === 'FIXED' || config.commission_rate != null || config.rate != null
}

export function commissionLabel(config: CommissionConfig, inherited = '按默认规则'): string {
  if (config.commission_type === 'FIXED') return `整单固定 ${amountToXaCoin(config.commission_fixed ?? 0)} 兴安币`
  const rate = config.commission_rate ?? config.rate
  return rate == null ? inherited : `${rate}%`
}

export function commissionError(type: CommissionType, rate: unknown, fixed: unknown): string {
  if (type === 'PERCENT') {
    return typeof rate === 'number' && Number.isInteger(rate) && rate >= 0 && rate <= 100
      ? '' : '抽成比例须为 0 到 100 的整数'
  }
  if (type !== 'FIXED') return '请选择抽成方式'
  if (typeof fixed !== 'number' || !Number.isFinite(fixed) || fixed < 0) return '请填写不小于 0 的固定抽成金额'
  const amount = xaCoinToAmount(fixed)
  if (Math.abs(fixed - Number(amountToXaCoin(amount))) > 0.0000001) return '固定抽成最多保留 1 位小数'
  if (!Number.isSafeInteger(amount) || amount > 2147483647) return '固定抽成金额过大'
  return ''
}

export function commissionPayload(type: CommissionType, rate: unknown, fixed: unknown) {
  const error = commissionError(type, rate, fixed)
  if (error) throw new Error(error)
  return type === 'FIXED'
    ? { commission_type: 'FIXED' as const, commission_rate: 0, commission_fixed: xaCoinToAmount(fixed as number) }
    : { commission_type: 'PERCENT' as const, commission_rate: rate as number, commission_fixed: 0 }
}

export function inheritedCommission() {
  return { commission_type: 'PERCENT' as const, commission_rate: null, commission_fixed: 0 }
}
