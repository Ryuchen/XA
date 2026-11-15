import { coinUnit } from "@xa/shared"
import { amountToXaCoin } from "@/lib/format"
type Report = Record<string, any>
export function validReportAmount(amount: unknown): number | null {
  if (
    (typeof amount !== "number" && typeof amount !== "string") ||
    String(amount).trim() === ""
  )
    return null
  const value = Number(amount)
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}
export function reportPayoutValue(row: Report): unknown {
  return row.platform_subsidy_income != null
    ? row.expected_income
    : row.payout_amount
}
export function reportPayoutDisplay(row: Report) {
  const value = validReportAmount(
    row.status === "PENDING" ? row.expected_income : reportPayoutValue(row)
  )
  if (row.status === "REJECTED") return "—"
  if (value === null) return "收益待核对"
  if (row.status === "PENDING") return `预计 ${amountToXaCoin(value)} ${coinUnit()}`
  const refunded = ["PARTIALLY_REFUNDED", "REFUNDED"].includes(
    row.order_payment_status
  )
  const label =
    row.negotiated_settlement === true
      ? "协商保留收益"
      : row.platform_subsidy_income != null
      ? refunded
        ? "协商报酬"
        : "平台补贴报酬"
      : refunded
        ? "原核定收益"
        : "核定收益"
  return `${label} ${amountToXaCoin(value)} ${coinUnit()}`
}
export function reportSettlementLabel(row: Report) {
  if (row.status !== "APPROVED")
    return row.status === "PENDING" ? "待审核" : "未通过审核"
  if (row.platform_funding_insufficient) return "待平台补款"
  const refunded = ["PARTIALLY_REFUNDED", "REFUNDED"].includes(
    row.order_payment_status
  )
  if (row.negotiated_settlement !== true && row.platform_subsidy_income == null && refunded)
    return row.order_payment_status === "PARTIALLY_REFUNDED"
      ? "已部分退款，净收益以钱包流水为准"
      : "已退款，收益变动以钱包流水为准"
  const status =
    row.platform_subsidy_income != null && refunded
      ? row.settled_at
        ? "SETTLED"
        : "WAITING"
      : row.settlement_status
  if (row.negotiated_settlement === true) {
    if (status === "WAITING") return "待本人结算条件满足"
    if (status === "SETTLED") {
      const value = validReportAmount(reportPayoutValue(row))
      return value === null ? "收益待核对" : value === 0 ? "已结算（收益为 0）" : "已结算（以钱包为准）"
    }
    return "收益待核对"
  }
  if (status === "WAITING") return "待整单结算"
  if (status === "SETTLED") {
    const value = validReportAmount(reportPayoutValue(row))
    return value === null
      ? "收益待核对"
      : value === 0
        ? "已结算（收益为 0）"
        : "已入账（可能冻结）"
  }
  return "收益待核对"
}
