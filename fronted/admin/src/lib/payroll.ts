import { formatRmbFen, parseRmbFen } from "@xa/shared"
import { request, type PageResult } from "@/api/request"
import type { Row } from "@/components/business"

export const activityKinds: Array<[string, string]> = [
  ["DISPATCH", "派单业绩"],
  ["RECHARGE", "预存充值"],
  ["AFTERSALE", "已完成售后"],
  ["EXAM", "已核实考试"],
]

export const kindLabel = (kind: string) =>
  activityKinds.find(([code]) => code === kind)?.[1] || kind

/** Only cross the RMB boundary here; these inputs never represent 浪花. */
export function rmbInput(value: unknown, label: string): number {
  const text = String(value ?? "").trim()
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(text))
    throw new Error(`${label}请填写非负人民币金额，最多两位小数。`)
  const fen = parseRmbFen(text)
  if (!Number.isSafeInteger(fen) || (fen === 0 && Number(text) !== 0))
    throw new Error(`${label}超出可录入范围。`)
  return fen
}

export function percentInput(value: unknown, label: string): number {
  const bps = rmbInput(value, label)
  if (bps > 10000) throw new Error(`${label}须在 0% 至 100% 之间。`)
  return bps
}

export const hasMoneyAmount = (value: unknown) =>
  (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) &&
  Number.isSafeInteger(Number(value))

export const rmb = (value: unknown) =>
  hasMoneyAmount(value) ? `¥${formatRmbFen(Number(value))}` : "—"
export const rmbText = (value: unknown) => formatRmbFen(Number(value ?? 0))
export const percentText = (value: unknown) => hasMoneyAmount(value) ? `${rmbText(value)}%` : "—"

export function beijingDay(): string {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date())
}

export const currentMonth = () => beijingDay().slice(0, 7)

export async function allPayrollOptions(
  url: string,
  params: Row = {}
): Promise<Row[]> {
  const rows: Row[] = []
  let page = 1
  for (;;) {
    const { data } = await request<PageResult<Row> | Row[]>({
      url,
      params: { ...params, page, page_size: 100 },
    })
    if (Array.isArray(data)) return data
    rows.push(...data.list)
    if (!Number.isFinite(data.total)) return rows
    if (rows.length >= data.total || !data.list.length) return rows
    page += 1
  }
}

export const dictionaryOptions = (type: string) =>
  allPayrollOptions("/dictionary-options/", { type })

/** Keep an inactive historical selection readable when fetching active options. */
export function withCurrentOption(
  options: Row[],
  id: unknown,
  name: unknown
): Row[] {
  if (!id || options.some((option) => Number(option.id) === Number(id)))
    return options
  return [
    { id, name: `${name || `#${id}`}（当前记录，未在启用选项中）` },
    ...options,
  ]
}
