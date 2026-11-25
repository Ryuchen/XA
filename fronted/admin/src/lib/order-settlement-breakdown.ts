import { formatXaCoin } from "@xa/money"

type Data = Record<string, unknown>

export const SETTLEMENT_STATUSES = ["REVIEW", "PENDING", "PARTIAL", "FROZEN", "POSTED"] as const
export type SettlementStatus = typeof SETTLEMENT_STATUSES[number]

export type SettlementAmounts = {
  net: number | null
  pending: number | null
  frozen: number | null
  posted: number | null
}

export type SettlementUnknownItem = {
  code: string | null
  message: string
  segmentId: string | null
  paymentId: string | null
}

export type SettlementTransaction = {
  key: string
  transactionNo: string | null
  txType: string | null
  status: string | null
  amountRaw: number | null
  createdAt: string | null
  scope: string | null
}

export type SettlementLine = SettlementAmounts & {
  key: string
  lineId: string | null
  kind: "main" | "addon"
  title: string
  detail: string
  segmentId: string | null
  paymentId: string | null
  assignmentId: string | null
  slot: number | null
  addonName: string | null
  startedAt: string | null
  endedAt: string | null
  freezeUntil: string | null
  amountBasis: "SEGMENT_SETTLEMENT_MANIFEST" | "VERIFIED_ORDINARY_SETTLEMENT" | "UNKNOWN" | null
  status: SettlementStatus | null
  transactions: SettlementTransaction[]
  unknownItems: SettlementUnknownItem[]
}

export type SettlementTotals = SettlementAmounts & {
  mainNet: number | null
  addonNet: number | null
  total: number | null
  unknown: boolean
}

export type SettlementBreakdownView = {
  state: "ready" | "empty" | "unsupported" | "mismatch"
  orderId: number | null
  orderNo: string | null
  storeSlug: string | null
  providerAccountId: string | null
  scope: string | null
  generatedAt: string | null
  status: SettlementStatus | null
  freezeUntil: string | null
  lines: SettlementLine[]
  totals: SettlementTotals
  transactionRefs: SettlementTransaction[]
  unknownItems: SettlementUnknownItem[]
}

const record = (value: unknown): Data | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Data
    : null

const text = (value: unknown): string | null => {
  if (typeof value !== "string") return null
  const normalized = value.trim()
  return normalized || null
}

const identityText = (value: unknown): string | null => {
  if (typeof value !== "string") return null
  const normalized = value.trim()
  return normalized && normalized === value ? normalized : null
}

/** Missing, null, negative, fractional and unsafe values stay unknown, never zero. */
export function settlementAmount(value: unknown): number | null {
  if (typeof value === "number") return Number.isSafeInteger(value) && value >= 0 ? value : null
  if (typeof value === "string" && /^(?:0|[1-9]\d*)$/.test(value)) {
    const parsed = Number(value)
    return Number.isSafeInteger(parsed) ? parsed : null
  }
  return null
}

const positiveId = (value: unknown): string | null => {
  const parsed = settlementAmount(value)
  return parsed !== null && parsed > 0 ? String(parsed) : null
}

const status = (value: unknown): SettlementStatus | null =>
  typeof value === "string" && (SETTLEMENT_STATUSES as readonly string[]).includes(value)
    ? value as SettlementStatus
    : null

const amountBasis = (value: unknown): SettlementLine["amountBasis"] =>
  value === "SEGMENT_SETTLEMENT_MANIFEST" || value === "VERIFIED_ORDINARY_SETTLEMENT" || value === "UNKNOWN"
    ? value
    : null

const emptyAmounts = (): SettlementAmounts => ({ net: null, pending: null, frozen: null, posted: null })

const emptyTotals = (): SettlementTotals => ({
  ...emptyAmounts(),
  mainNet: null,
  addonNet: null,
  total: null,
  unknown: false,
})

const stopped = (state: "unsupported" | "mismatch", message: string, orderId: number | null = null): SettlementBreakdownView => ({
  state,
  orderId,
  orderNo: null,
  storeSlug: null,
  providerAccountId: null,
  scope: null,
  generatedAt: null,
  status: null,
  freezeUntil: null,
  lines: [],
  totals: emptyTotals(),
  transactionRefs: [],
  unknownItems: [{ code: null, message, segmentId: null, paymentId: null }],
})

const unknownItem = (value: unknown): SettlementUnknownItem | null => {
  if (typeof value === "string" && value.trim()) {
    return { code: null, message: value.trim(), segmentId: null, paymentId: null }
  }
  const item = record(value)
  if (!item) return null
  const code = text(item.code)
  const message = text(item.message) || code || "服务端标记了未知项，具体原因待核对。"
  return {
    code,
    message,
    segmentId: positiveId(item.segment_id),
    paymentId: positiveId(item.payment_id),
  }
}

const unknownItems = (value: unknown): SettlementUnknownItem[] | null => {
  if (!Array.isArray(value)) return null
  const result: SettlementUnknownItem[] = []
  for (const entry of value) {
    const item = unknownItem(entry)
    if (!item) return null
    result.push(item)
  }
  return result
}

const dedupeUnknowns = (items: SettlementUnknownItem[]): SettlementUnknownItem[] => {
  const seen = new Set<string>()
  return items.filter(item => {
    const key = JSON.stringify(item)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const transaction = (value: unknown): SettlementTransaction | null => {
  const item = record(value)
  if (!item) return null
  const id = positiveId(item.id)
  const transactionNo = text(item.transaction_no)
  if (!id && !transactionNo) return null
  return {
    key: transactionNo || `#${id}`,
    transactionNo,
    txType: text(item.tx_type),
    status: text(item.status),
    amountRaw: settlementAmount(item.amount_raw),
    createdAt: text(item.created_at),
    scope: text(item.scope),
  }
}

const transactions = (value: unknown): SettlementTransaction[] => {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  return value.map(transaction).filter((item): item is SettlementTransaction => {
    if (!item || seen.has(item.key)) return false
    seen.add(item.key)
    return true
  })
}

const providerScope = (data: Data): { accountId: string; label: string; rawScope: string } | null => {
  const provider = record(data.current_provider)
  const accountId = positiveId(provider?.account_id)
  const rawScope = text(data.scope)
  if (!provider || !accountId || rawScope !== `PROVIDER#${accountId}`) return null
  const label = text(provider.display_name) || text(provider.nickname) || text(provider.username) ||
    text(provider.account_no) || `#${accountId}`
  return { accountId, label, rawScope }
}

function line(value: unknown, index: number, kind: "main" | "addon"): SettlementLine | null {
  const row = record(value)
  if (!row) return null
  const rowUnknowns = unknownItems(row.unknown_items)
  if (!rowUnknowns) return null
  const segmentId = positiveId(row.segment_id)
  const paymentId = positiveId(row.payment_id)
  const addonName = text(row.addon_name)
  const startedAt = text(row.started_at)
  const endedAt = text(row.ended_at)
  const timeRange = [startedAt, endedAt].filter(Boolean).join(" – ")
  const title = kind === "main"
    ? `主单履约段${segmentId ? ` #${segmentId}` : `（第 ${index + 1} 条）`}`
    : `加购付款${paymentId ? ` #${paymentId}` : `（第 ${index + 1} 条）`}${addonName ? ` · ${addonName}` : ""}`
  const detail = [
    kind === "addon" && segmentId ? `履约段 #${segmentId}` : null,
    timeRange || null,
  ].filter(Boolean).join(" · ")
  return {
    key: text(row.line_id) || `${kind}-${segmentId || paymentId || index + 1}`,
    lineId: text(row.line_id),
    kind,
    title,
    detail,
    segmentId,
    paymentId,
    assignmentId: positiveId(row.provider_assignment_id),
    slot: settlementAmount(row.slot),
    addonName,
    startedAt,
    endedAt,
    freezeUntil: text(row.freeze_until),
    amountBasis: amountBasis(row.amount_basis),
    status: status(row.settlement_status),
    ...emptyAmounts(),
    net: settlementAmount(row.net_income_raw),
    pending: settlementAmount(row.pending_income_raw),
    frozen: settlementAmount(row.frozen_income_raw),
    posted: settlementAmount(row.posted_income_raw),
    transactions: transactions(row.transactions),
    unknownItems: rowUnknowns,
  }
}

const lineLabel = (item: SettlementLine, field: string) => `${item.title}的${field}待核对。`

function validationUnknowns(view: Omit<SettlementBreakdownView, "unknownItems">, serverUnknowns: SettlementUnknownItem[]): SettlementUnknownItem[] {
  if (serverUnknowns.length) return serverUnknowns
  const result: SettlementUnknownItem[] = []
  if (!view.status) result.push({ code: null, message: "服务端结算状态待核对。", segmentId: null, paymentId: null })
  if (view.status === "REVIEW") result.push({ code: null, message: "服务端将本单结算标记为待核对，但未提供未知原因。", segmentId: null, paymentId: null })
  view.lines.forEach(item => {
    const prefix = item.kind === "main" ? "主单净收益" : "加购净收益"
    if (item.net === null) result.push({ code: null, message: lineLabel(item, prefix), segmentId: item.segmentId, paymentId: item.paymentId })
    if (item.pending === null) result.push({ code: null, message: lineLabel(item, "待入账收益"), segmentId: item.segmentId, paymentId: item.paymentId })
    if (item.frozen === null) result.push({ code: null, message: lineLabel(item, "冻结收益"), segmentId: item.segmentId, paymentId: item.paymentId })
    if (item.posted === null) result.push({ code: null, message: lineLabel(item, "已入账收益"), segmentId: item.segmentId, paymentId: item.paymentId })
    if (!item.status) result.push({ code: null, message: `${item.title}的入账状态待核对。`, segmentId: item.segmentId, paymentId: item.paymentId })
    if (!item.amountBasis) result.push({ code: null, message: `${item.title}的金额依据待核对。`, segmentId: item.segmentId, paymentId: item.paymentId })
    if (item.transactions.length === 0 && item.posted !== 0) {
      result.push({ code: null, message: `${item.title}的收益流水引用待核对。`, segmentId: item.segmentId, paymentId: item.paymentId })
    }
  })
  if (view.totals.mainNet === null) result.push({ code: null, message: "服务端主单净收益合计待核对。", segmentId: null, paymentId: null })
  if (view.totals.addonNet === null) result.push({ code: null, message: "服务端加购收益合计待核对。", segmentId: null, paymentId: null })
  if (view.totals.net === null) result.push({ code: null, message: "服务端净收益总计待核对。", segmentId: null, paymentId: null })
  if (view.totals.total === null) result.push({ code: null, message: "服务端收益总额待核对。", segmentId: null, paymentId: null })
  if (view.totals.pending === null) result.push({ code: null, message: "服务端待入账收益合计待核对。", segmentId: null, paymentId: null })
  if (view.totals.frozen === null) result.push({ code: null, message: "服务端冻结收益合计待核对。", segmentId: null, paymentId: null })
  if (view.totals.posted === null) result.push({ code: null, message: "服务端已入账收益合计待核对。", segmentId: null, paymentId: null })
  if (view.totals.unknown) result.push({ code: null, message: "服务端标记合计存在未知项，但未提供具体原因。", segmentId: null, paymentId: null })
  return result
}

/** Adapt only the provider-scoped read contract; never derive a total from lines. */
export function normalizeSettlementBreakdown(payload: unknown, expectedOrderId?: number): SettlementBreakdownView {
  const data = record(payload)
  const orderIdValue = settlementAmount(data?.order_id)
  const orderId = orderIdValue !== null && orderIdValue > 0 ? orderIdValue : null
  if (!data || orderId === null || !Array.isArray(data.main_segments) || !Array.isArray(data.addon_earnings)) {
    return stopped("unsupported", "逐段收益响应结构待核对，未展示金额。", orderId)
  }
  const totalRow = record(data.totals)
  const provider = providerScope(data)
  const person = record(data.person_settlement)
  if (!totalRow || !provider || !person || positiveId(person.account_id) !== provider.accountId) {
    return stopped("unsupported", "逐段收益的订单、陪玩范围或合计结构待核对，未展示金额。", orderId)
  }
  if (expectedOrderId !== undefined && orderId !== expectedOrderId) {
    return stopped("mismatch", "逐段收益响应属于其他订单，已停止展示。", orderId)
  }
  const orderUnknowns = unknownItems(data.unknown_items)
  const personUnknowns = unknownItems(person.unknown_items)
  if (!orderUnknowns || !personUnknowns) {
    return stopped("unsupported", "逐段收益的未知项元数据待核对，未展示金额。", orderId)
  }
  if (typeof totalRow.unknown !== "boolean") {
    return stopped("unsupported", "逐段收益的未知标记待核对，未展示金额。", orderId)
  }

  const lines: SettlementLine[] = []
  const malformed: SettlementUnknownItem[] = []
  const readLines = (values: unknown[], kind: "main" | "addon") => values.forEach((value, index) => {
    const item = line(value, index, kind)
    if (item) lines.push(item)
    else malformed.push({
      code: null,
      message: `${kind === "main" ? "主单履约段" : "加购收益"}第 ${index + 1} 条记录结构或未知项元数据待核对。`,
      segmentId: null,
      paymentId: null,
    })
  })
  readLines(data.main_segments, "main")
  readLines(data.addon_earnings, "addon")
  if (malformed.length) return stopped("unsupported", malformed[0].message, orderId)

  const serverUnknowns = dedupeUnknowns([
    ...orderUnknowns,
    ...personUnknowns,
    ...lines.flatMap(item => item.unknownItems),
  ])
  const totals: SettlementTotals = {
    mainNet: settlementAmount(totalRow.main_net_income_raw),
    addonNet: settlementAmount(totalRow.addon_net_income_raw),
    net: settlementAmount(totalRow.net_income_raw),
    total: settlementAmount(totalRow.total_income_raw),
    pending: settlementAmount(totalRow.pending_income_raw),
    frozen: settlementAmount(totalRow.frozen_income_raw),
    posted: settlementAmount(totalRow.posted_income_raw),
    unknown: totalRow.unknown,
  }
  const view: Omit<SettlementBreakdownView, "unknownItems"> = {
    state: lines.length ? "ready" : "empty",
    orderId,
    orderNo: text(data.order_no),
    storeSlug: text(data.store_slug),
    providerAccountId: provider.accountId,
    scope: provider.label,
    generatedAt: text(data.generated_at),
    status: status(data.status),
    freezeUntil: text(person.freeze_until),
    lines,
    totals,
    transactionRefs: transactions(data.transaction_refs),
  }
  return { ...view, unknownItems: dedupeUnknowns(validationUnknowns(view, serverUnknowns)) }
}

export type StaffSettlementProvider = SettlementBreakdownView

export type StaffSettlementBreakdownView = {
  state: "ready" | "empty" | "unsupported" | "mismatch"
  orderId: number | null
  orderNo: string | null
  storeSlug: string | null
  providers: StaffSettlementProvider[]
  generatedAt: string | null
  unknownItems: SettlementUnknownItem[]
}

const staffStopped = (state: "unsupported" | "mismatch", message: string, orderId: number | null = null): StaffSettlementBreakdownView => ({
  state,
  orderId,
  orderNo: null,
  storeSlug: null,
  providers: [],
  generatedAt: null,
  unknownItems: [{ code: null, message, segmentId: null, paymentId: null }],
})

/** Adapt the staff order scope while preserving each provider's own totals. */
export function normalizeStaffSettlementBreakdown(payload: unknown, expectedOrderId?: number): StaffSettlementBreakdownView {
  const data = record(payload)
  const orderIdValue = settlementAmount(data?.order_id)
  const orderId = orderIdValue !== null && orderIdValue > 0 ? orderIdValue : null
  const orderNo = identityText(data?.order_no)
  const storeSlug = identityText(data?.store_slug)
  const staffUnknowns = unknownItems(data?.unknown_items)
  if (!data || orderId === null || !orderNo || !storeSlug || data.scope !== "STAFF_ORDER" ||
      !Array.isArray(data.providers) || !staffUnknowns) {
    return staffStopped("unsupported", "员工逐段收益的订单身份或响应结构待核对，未展示金额。", orderId)
  }
  if (expectedOrderId !== undefined && orderId !== expectedOrderId) {
    return staffStopped("mismatch", "员工逐段收益响应属于其他订单，已停止展示。", orderId)
  }
  const providers: StaffSettlementProvider[] = []
  const providerIds = new Set<string>()
  for (const value of data.providers) {
    const item = record(value)
    const providerOrderId = settlementAmount(item?.order_id)
    const providerOrderNo = identityText(item?.order_no)
    const providerStoreSlug = identityText(item?.store_slug)
    if (!item || providerOrderId !== orderId || providerOrderNo !== orderNo || providerStoreSlug !== storeSlug ||
        !Array.isArray(item.main_segments) || !Array.isArray(item.addon_earnings) || !record(item.totals) ||
        !record(item.person_settlement) || !record(item.current_provider)) {
      return staffStopped("unsupported", "订单内陪玩逐段收益的订单身份或结构待核对，已停止展示全部金额。", orderId)
    }
    const provider = normalizeSettlementBreakdown(value, orderId)
    if (provider.state === "unsupported" || provider.state === "mismatch") {
      return staffStopped(provider.state, provider.unknownItems[0]?.message || "陪玩逐段收益待核对，未展示金额。", orderId)
    }
    const providerId = provider.providerAccountId
    if (!providerId || providerIds.has(providerId)) {
      return staffStopped("unsupported", "订单内陪玩账号范围重复或缺失，已停止展示全部金额。", orderId)
    }
    providerIds.add(providerId)
    providers.push(provider)
  }
  return {
    state: providers.length ? "ready" : "empty",
    orderId,
    orderNo,
    storeSlug,
    providers,
    generatedAt: text(data.generated_at),
    unknownItems: dedupeUnknowns([
      ...staffUnknowns,
      ...providers.flatMap(provider => provider.unknownItems),
    ]),
  }
}

export function settlementMoney(value: number | null): string {
  return value === null ? "—" : formatXaCoin(value)
}
