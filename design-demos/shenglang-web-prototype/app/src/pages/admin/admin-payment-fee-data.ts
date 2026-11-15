export type PaymentFeeBasis = 'SERVICE_AMOUNT' | 'PAYMENT_TOTAL'
export type PaymentFeeRounding = 'DOWN' | 'UP' | 'HALF_UP'

export interface PaymentFeeConfig {
  revision: number
  merchantId: string
  appid: string
  ratePercent: string
  feeBasis: PaymentFeeBasis
  rounding: PaymentFeeRounding
  updatedAt: string
}

export interface PaymentFeeHistory extends PaymentFeeConfig {
  operatorName: string
}

export interface PaymentFeePreview {
  revision: number
  serviceFen: number
  feeFen: number
  totalFen: number
}

export const initialPaymentFeeConfig: PaymentFeeConfig = {
  revision: 4,
  merchantId: '1900000121',
  appid: 'wx8f2a6c9e14b5073d',
  ratePercent: '0.6000',
  feeBasis: 'PAYMENT_TOTAL',
  rounding: 'UP',
  updatedAt: '2026-11-03 16:42',
}

export const initialPaymentFeeHistory: PaymentFeeHistory[] = [
  { ...initialPaymentFeeConfig, operatorName: '林岚' },
  {
    revision: 3,
    merchantId: '1900000121',
    appid: 'wx8f2a6c9e14b5073d',
    ratePercent: '0.6000',
    feeBasis: 'SERVICE_AMOUNT',
    rounding: 'UP',
    updatedAt: '2026-10-28 11:20',
    operatorName: '林岚',
  },
  {
    revision: 2,
    merchantId: '1900000121',
    appid: 'wx8f2a6c9e14b5073d',
    ratePercent: '0.5800',
    feeBasis: 'SERVICE_AMOUNT',
    rounding: 'HALF_UP',
    updatedAt: '2026-10-16 14:08',
    operatorName: '周澄',
  },
  {
    revision: 1,
    merchantId: '1900000121',
    appid: 'wx8f2a6c9e14b5073d',
    ratePercent: '0.6000',
    feeBasis: 'SERVICE_AMOUNT',
    rounding: 'HALF_UP',
    updatedAt: '2026-09-30 10:15',
    operatorName: '周澄',
  },
]

export const paymentFeeBasisLabels: Record<PaymentFeeBasis, string> = {
  SERVICE_AMOUNT: '服务成交金额',
  PAYMENT_TOTAL: '服务金额及手续费总额',
}

export const paymentFeeRoundingLabels: Record<PaymentFeeRounding, string> = {
  DOWN: '不足一分舍去',
  UP: '不足一分进一分',
  HALF_UP: '四舍五入到分',
}

export function normalizeRatePercent(value: string) {
  return Number(value).toFixed(4)
}

export function parsePaymentFeeYuan(value: string) {
  const normalized = value.trim()
  if (!/^(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?$/.test(normalized)) return 0
  const [yuan, fraction = ''] = normalized.split('.')
  const fen = Number(yuan) * 100 + Number(fraction.padEnd(2, '0'))
  return fen > 0 && fen <= 2147483647 ? fen : 0
}

export function formatPaymentFeeFen(value: number) {
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value / 100)
}

function roundedFraction(base: bigint, numerator: bigint, rounding: PaymentFeeRounding) {
  const denominator = 1000000n
  const product = base * numerator
  if (rounding === 'DOWN') return product / denominator
  if (rounding === 'UP') return (product + denominator - 1n) / denominator
  return (2n * product + denominator) / (2n * denominator)
}

export function quotePaymentFee(serviceFen: number, config: PaymentFeeConfig): PaymentFeePreview {
  const service = BigInt(serviceFen)
  const numerator = BigInt(Math.round(Number(config.ratePercent) * 10000))
  let fee: bigint

  if (config.feeBasis === 'SERVICE_AMOUNT') {
    fee = roundedFraction(service, numerator, config.rounding)
  } else {
    const maxFen = 9223372036854775807n
    let low = 0n
    let high = maxFen - service
    while (low < high) {
      const middle = (low + high) / 2n
      if (roundedFraction(service + middle, numerator, config.rounding) <= middle) high = middle
      else low = middle + 1n
    }
    fee = low
  }

  return {
    revision: config.revision,
    serviceFen,
    feeFen: Number(fee),
    totalFen: Number(service + fee),
  }
}
