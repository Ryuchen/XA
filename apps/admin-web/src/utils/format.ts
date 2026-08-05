import { formatXaCoin, toRawAmount } from '@xa/money'

/** 后端账务单位转兴安币。 */
export function amountToXaCoin(amount?: number | null): string {
  return formatXaCoin(amount)
}

/** 兴安币转后端账务单位。 */
export function xaCoinToAmount(coins?: number | string | null): number {
  return toRawAmount(coins)
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`
}
