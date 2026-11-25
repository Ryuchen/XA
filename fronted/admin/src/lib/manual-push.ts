import { ApiError } from "@xa/shared"

export const MANUAL_PUSH_TYPES = ["SYSTEM", "ORDER", "SUPPORT", "PROMOTION"] as const
export const MANUAL_PUSH_AUDIENCES = ["CUSTOM", "BOSS", "PROVIDER", "STAFF", "ALL"] as const
export const MANUAL_PUSH_CAMPAIGN_FILTERS = ["PENDING", "FAILED", "SKIPPED", "COMPLETED"] as const
export const MANUAL_PUSH_RECIPIENT_FILTERS = ["CREATED", "PENDING", "FAILED", "SKIPPED"] as const

export type ManualPushType = (typeof MANUAL_PUSH_TYPES)[number]
export type ManualPushAudience = (typeof MANUAL_PUSH_AUDIENCES)[number]
export type ManualPushCampaignFilter = (typeof MANUAL_PUSH_CAMPAIGN_FILTERS)[number]
export type ManualPushRecipientFilter = (typeof MANUAL_PUSH_RECIPIENT_FILTERS)[number]
export type ManualPushRecipientStatus = "CREATED" | "PENDING" | "SKIPPED"
export type ManualPushSelector =
  | { broadcast: true }
  | { audience: "BOSS" | "PROVIDER" | "STAFF" }
  | { accounts: number[]; users: number[] }
export type ManualPushPayload = {
  client_request_id: string
  broadcast: boolean
  audience: ManualPushAudience
  recipient_account_ids: number[]
  type: ManualPushType
  title: string
  preview: string
  detail: string
}
export type ManualPushIntent = {
  version: 1
  storeSlug: string
  creatorId: number
  payload: ManualPushPayload
}
export type ManualPushCampaign = {
  campaign_id: number
  client_request_id: string
  creator_id: number
  created_at: string
  title: string
  message_type: ManualPushType
  selector: ManualPushSelector
  total: number
  count: number
  pending_count: number
  failed_count: number
  skipped_count: number
}
export type ManualPushCampaignPage = {
  list: ManualPushCampaign[]
  total: number
  page: number
  page_size: number
}
export type ManualPushCampaignDetail = ManualPushCampaign & { preview: string; detail: string }
export type ManualPushRecipient = {
  id: number
  account_id: number
  account_no: string
  status: ManualPushRecipientStatus
  attempts: number
  last_error: string
  message_id: number | null
  next_attempt_at: string | null
}
export type ManualPushStatusPage = {
  list: ManualPushRecipient[]
  total: number
  page: number
  page_size: number
  campaign: ManualPushCampaignDetail
}
export type ManualPushResult = ManualPushCampaign & { request_replayed: boolean }

const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const CAMPAIGN_KEYS = [
  "campaign_id", "client_request_id", "creator_id", "created_at", "title", "message_type", "selector",
  "total", "count", "pending_count", "failed_count", "skipped_count",
] as const
const PAYLOAD_KEYS = [
  "client_request_id", "broadcast", "audience", "recipient_account_ids", "type", "title", "preview", "detail",
] as const

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}
function integer(value: unknown, positive = false): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && (positive ? value > 0 : value >= 0)
}
function text(value: unknown): value is string {
  return typeof value === "string"
}
function dateTime(value: unknown): value is string {
  return typeof value === "string" && ISO_DATE_TIME.test(value) && Number.isFinite(Date.parse(value))
}
function requestId(value: unknown): value is string {
  return typeof value === "string" && !!value && value.trim() === value && value.length <= 64
}
function idList(value: unknown): value is number[] {
  return Array.isArray(value) && value.every(item => integer(item, true)) &&
    new Set(value).size === value.length && value.every((item, index) => index === 0 || value[index - 1] < item)
}
function invalid(message: string): never {
  throw new Error(message)
}
function includes<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && values.includes(value as T)
}

export function manualPushSelector(payload: ManualPushPayload): ManualPushSelector {
  if (payload.broadcast) return { broadcast: true }
  if (payload.audience === "BOSS" || payload.audience === "PROVIDER" || payload.audience === "STAFF") {
    return { audience: payload.audience }
  }
  return { accounts: [...payload.recipient_account_ids], users: [] }
}

function parseSelector(value: unknown): ManualPushSelector {
  if (!record(value)) invalid("群发接收范围结构待核对")
  if (exactKeys(value, ["broadcast"]) && value.broadcast === true) return { broadcast: true }
  if (exactKeys(value, ["audience"]) &&
      (value.audience === "BOSS" || value.audience === "PROVIDER" || value.audience === "STAFF")) {
    return { audience: value.audience }
  }
  if (exactKeys(value, ["accounts", "users"]) && idList(value.accounts) && idList(value.users) &&
      (value.accounts.length > 0 || value.users.length > 0)) {
    return { accounts: [...value.accounts], users: [...value.users] }
  }
  invalid("群发接收范围或账号身份待核对")
}

function parseCounts(value: Record<string, unknown>) {
  if (!integer(value.total) || !integer(value.count) || !integer(value.pending_count) ||
      !integer(value.failed_count) || !integer(value.skipped_count) ||
      value.failed_count > value.pending_count ||
      value.total !== value.count + value.pending_count + value.skipped_count) {
    invalid("群发统计缺失、不一致或把失败数重复累加")
  }
  return {
    total: value.total,
    count: value.count,
    pending_count: value.pending_count,
    failed_count: value.failed_count,
    skipped_count: value.skipped_count,
  }
}

function parseCampaign(value: unknown): ManualPushCampaign {
  if (!record(value) || !exactKeys(value, CAMPAIGN_KEYS) || !integer(value.campaign_id, true) ||
      !requestId(value.client_request_id) || !integer(value.creator_id, true) || !dateTime(value.created_at) ||
      !text(value.title) || !includes(MANUAL_PUSH_TYPES, value.message_type)) {
    invalid("群发批次结构或版本身份待核对")
  }
  return {
    campaign_id: value.campaign_id,
    client_request_id: value.client_request_id,
    creator_id: value.creator_id,
    created_at: value.created_at,
    title: value.title,
    message_type: value.message_type,
    selector: parseSelector(value.selector),
    ...parseCounts(value),
  }
}

function pagination(value: unknown, context: { page: number; pageSize: number }) {
  if (!record(value) || !integer(value.total) || !integer(value.page, true) ||
      !integer(value.page_size, true) || value.page_size > 100 || !Array.isArray(value.list) ||
      context.page < 1 || context.pageSize < 1 || context.pageSize > 100 ||
      value.page !== context.page || value.page_size !== context.pageSize) {
    invalid("群发分页响应与请求不一致")
  }
  const pages = Math.max(1, Math.ceil(value.total / context.pageSize))
  const expectedLength = value.total === 0 ? 0 : Math.min(context.pageSize, value.total - (context.page - 1) * context.pageSize)
  if (context.page > pages || value.list.length !== expectedLength) invalid("群发分页总数、页码或页长度不一致")
  return { total: value.total, page: value.page, page_size: value.page_size }
}

export function parseManualPushCampaignPage(value: unknown, context: {
  page: number
  pageSize: number
  status?: "" | ManualPushCampaignFilter
  creatorId?: number
  clientRequestId?: string
}): ManualPushCampaignPage {
  if (!record(value) || !exactKeys(value, ["list", "total", "page", "page_size"]) || !Array.isArray(value.list)) invalid("群发批次分页结构待核对")
  const page = pagination(value, context)
  const ids = new Set<number>()
  const identities = new Set<string>()
  const list = value.list.map(item => {
    const campaign = parseCampaign(item)
    const identity = `${campaign.creator_id}:${campaign.client_request_id}`
    if (ids.has(campaign.campaign_id) || identities.has(identity)) invalid("群发批次编号或操作人请求号重复")
    ids.add(campaign.campaign_id)
    identities.add(identity)
    if (context.creatorId !== undefined && campaign.creator_id !== context.creatorId) invalid("群发批次操作人与筛选不一致")
    if (context.clientRequestId !== undefined && campaign.client_request_id !== context.clientRequestId) invalid("群发批次请求号与筛选不一致")
    if (context.status === "PENDING" && campaign.pending_count === 0) invalid("待处理批次筛选结果不一致")
    if (context.status === "FAILED" && campaign.failed_count === 0) invalid("失败批次筛选结果不一致")
    if (context.status === "SKIPPED" && campaign.skipped_count === 0) invalid("跳过批次筛选结果不一致")
    if (context.status === "COMPLETED" && campaign.pending_count !== 0) invalid("已完成批次仍包含待处理记录")
    return campaign
  })
  return { list, ...page }
}

function parseRecipient(value: unknown): ManualPushRecipient {
  const keys = ["id", "account_id", "account_no", "status", "attempts", "last_error", "message_id", "next_attempt_at"]
  if (!record(value) || !exactKeys(value, keys) || !integer(value.id, true) || !integer(value.account_id, true) ||
      !text(value.account_no) || !(value.status === "CREATED" || value.status === "PENDING" || value.status === "SKIPPED") ||
      !integer(value.attempts) || !text(value.last_error) ||
      !(value.message_id === null || integer(value.message_id, true)) ||
      !(value.next_attempt_at === null || dateTime(value.next_attempt_at))) {
    invalid("逐人接收记录结构待核对")
  }
  if ((value.status === "CREATED" ? !integer(value.message_id, true) : value.message_id !== null) ||
      (value.status === "PENDING" ? !dateTime(value.next_attempt_at) : value.next_attempt_at !== null)) {
    invalid("逐人状态与消息记录或下次处理时间不一致")
  }
  return {
    id: value.id,
    account_id: value.account_id,
    account_no: value.account_no,
    status: value.status,
    attempts: value.attempts,
    last_error: value.last_error,
    message_id: value.message_id,
    next_attempt_at: value.next_attempt_at,
  }
}

export function parseManualPushStatusPage(value: unknown, context: {
  campaignId: number
  page: number
  pageSize: number
  status?: "" | ManualPushRecipientFilter
}): ManualPushStatusPage {
  if (!record(value) || !exactKeys(value, ["list", "total", "page", "page_size", "campaign"]) ||
      !Array.isArray(value.list) || !record(value.campaign) || !exactKeys(value.campaign, [...CAMPAIGN_KEYS, "preview", "detail"])) {
    invalid("逐人状态分页结构待核对")
  }
  const page = pagination(value, context)
  const campaignBase = parseCampaign(Object.fromEntries(CAMPAIGN_KEYS.map(key => [key, (value.campaign as Record<string, unknown>)[key]])))
  if (campaignBase.campaign_id !== context.campaignId || !text(value.campaign.preview) || !text(value.campaign.detail)) {
    invalid("逐人状态与所选批次或正文不一致")
  }
  const campaign: ManualPushCampaignDetail = { ...campaignBase, preview: value.campaign.preview, detail: value.campaign.detail }
  const ids = new Set<number>()
  const accounts = new Set<number>()
  const list = value.list.map(item => {
    const recipient = parseRecipient(item)
    if (ids.has(recipient.id) || accounts.has(recipient.account_id)) invalid("逐人接收记录或账号重复")
    ids.add(recipient.id)
    accounts.add(recipient.account_id)
    if (context.status === "FAILED" && (recipient.status !== "PENDING" || recipient.last_error === "")) invalid("失败筛选包含非失败记录")
    if (context.status && context.status !== "FAILED" && recipient.status !== context.status) invalid("逐人状态筛选结果不一致")
    return recipient
  })
  return { list, ...page, campaign }
}

export function createManualPushIntent(
  context: { storeSlug: string; creatorId: number },
  form: {
    audience: ManualPushAudience
    recipientAccountIds: number[]
    type: ManualPushType
    title: string
    preview: string
    detail: string
  },
  clientRequestId: string,
): ManualPushIntent {
  if (!STORE_SLUG.test(context.storeSlug) || !integer(context.creatorId, true) || !requestId(clientRequestId) ||
      !includes(MANUAL_PUSH_AUDIENCES, form.audience) || !includes(MANUAL_PUSH_TYPES, form.type) ||
      !text(form.title) || !text(form.preview) || !text(form.detail)) invalid("群发请求身份或字段无效")
  const title = form.title.trim()
  const preview = form.preview.trim()
  if (!title || title.length > 100 || preview.length > 255) invalid("请填写 100 字以内标题和 255 字以内摘要")
  const recipientAccountIds = form.audience === "CUSTOM" ? [...new Set(form.recipientAccountIds)].sort((a, b) => a - b) : []
  if (!idList(recipientAccountIds) || (form.audience === "CUSTOM" && recipientAccountIds.length === 0)) invalid("请选择有效的接收业务账号")
  return {
    version: 1,
    storeSlug: context.storeSlug,
    creatorId: context.creatorId,
    payload: {
      client_request_id: clientRequestId,
      broadcast: form.audience === "ALL",
      audience: form.audience,
      recipient_account_ids: recipientAccountIds,
      type: form.type,
      title,
      preview,
      detail: form.detail,
    },
  }
}

export function parseManualPushIntent(raw: string | null, context: { storeSlug: string; creatorId: number }): ManualPushIntent | null {
  if (raw === null) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (!record(value) || !exactKeys(value, ["version", "storeSlug", "creatorId", "payload"]) ||
        value.version !== 1 || value.storeSlug !== context.storeSlug || value.creatorId !== context.creatorId ||
        !record(value.payload) || !exactKeys(value.payload, PAYLOAD_KEYS)) invalid("invalid")
    const payload = value.payload
    const intent = createManualPushIntent(context, {
      audience: payload.audience as ManualPushAudience,
      recipientAccountIds: payload.recipient_account_ids as number[],
      type: payload.type as ManualPushType,
      title: payload.title as string,
      preview: payload.preview as string,
      detail: payload.detail as string,
    }, payload.client_request_id as string)
    if (JSON.stringify(intent.payload) !== JSON.stringify(payload)) invalid("invalid")
    return intent
  } catch {
    throw new Error("站内群发的待核对请求无法解析，已阻止新发送；请保留浏览器记录并联系管理员。")
  }
}

export function manualPushCampaignMatchesIntent(campaign: ManualPushCampaign | ManualPushCampaignDetail, intent: ManualPushIntent, withBody = false) {
  const base = campaign.client_request_id === intent.payload.client_request_id && campaign.creator_id === intent.creatorId &&
    campaign.title === intent.payload.title && campaign.message_type === intent.payload.type &&
    JSON.stringify(campaign.selector) === JSON.stringify(manualPushSelector(intent.payload))
  if (!base) return false
  if (!withBody) return true
  return "preview" in campaign && campaign.preview === intent.payload.preview && campaign.detail === intent.payload.detail
}

export function parseManualPushResult(value: unknown, intent: ManualPushIntent): ManualPushResult {
  if (!record(value) || !exactKeys(value, [...CAMPAIGN_KEYS, "request_replayed"]) || typeof value.request_replayed !== "boolean") {
    invalid("群发回执结构待核对")
  }
  const campaign = parseCampaign(Object.fromEntries(CAMPAIGN_KEYS.map(key => [key, value[key]])))
  if (!manualPushCampaignMatchesIntent(campaign, intent)) invalid("群发回执与原请求的门店操作人、内容或接收范围不一致")
  return { ...campaign, request_replayed: value.request_replayed }
}

export function parseManualPushRetryResult(value: unknown, expected: { campaignId: number; clientRequestId: string }): ManualPushCampaign {
  const campaign = parseCampaign(value)
  if (campaign.campaign_id !== expected.campaignId || campaign.client_request_id !== expected.clientRequestId) {
    invalid("失败重试回执未沿用原群发批次")
  }
  return campaign
}

export async function findManualPushRecovery(intent: ManualPushIntent, io: {
  lookup: (value: ManualPushIntent) => Promise<ManualPushCampaignPage>
  detail: (campaignId: number) => Promise<ManualPushStatusPage>
}): Promise<{ status: "NOT_FOUND" } | { status: "FOUND"; campaign: ManualPushCampaignDetail; page: ManualPushStatusPage }> {
  const candidates = await io.lookup(intent)
  const matches = candidates.list.filter(item => item.client_request_id === intent.payload.client_request_id && item.creator_id === intent.creatorId)
  if (matches.length === 0) return { status: "NOT_FOUND" }
  if (matches.length !== 1) throw new Error("原请求号对应多个批次，不能自动恢复，请联系管理员核对")
  const page = await io.detail(matches[0].campaign_id)
  if (!manualPushCampaignMatchesIntent(page.campaign, intent, true)) {
    throw new Error("原请求号对应的消息内容或接收范围不同，不能覆盖原批次")
  }
  return { status: "FOUND", campaign: page.campaign, page }
}

export function isManualPushRequestConflict(cause: unknown) {
  const error = cause as { status?: number; message?: string; body?: { msg?: string; errors?: { detail?: string } } } | null
  const message = error?.body?.errors?.detail || error?.body?.msg || error?.message || ""
  return error?.status === 409 && message.includes("请求编号已用于其他消息")
}

export function manualPushWriteFailure(cause: unknown, recovering = false): { kind: "denied" | "conflict" | "rejected" | "unknown"; clear: boolean } {
  const status = (cause as { status?: number } | null)?.status || 0
  if (status === 409) return { kind: "conflict", clear: true }
  if (status === 400) return { kind: "rejected", clear: true }
  if (status === 403) return { kind: "denied", clear: !recovering }
  if (!recovering && (status === 401 || status === 404)) return { kind: "rejected", clear: true }
  return { kind: "unknown", clear: false }
}

export async function submitManualPushIntent(intent: ManualPushIntent, io: {
  save: (value: ManualPushIntent) => void
  send: (value: ManualPushIntent) => Promise<ManualPushResult>
  clear: () => void
}, options: { recovering?: boolean } = {}): Promise<ManualPushResult> {
  io.save(intent)
  try {
    const result = await io.send(intent)
    io.clear()
    return result
  } catch (cause) {
    if (manualPushWriteFailure(cause, options.recovering).clear) {
      try { io.clear() } catch { /* Retain the original request if storage cannot be updated. */ }
    }
    throw cause
  }
}

export function updateManualRetrySelection(selected: number[], changed: number[], checked: boolean): number[] {
  if (!idList([...selected].sort((a, b) => a - b)) || !idList([...changed].sort((a, b) => a - b))) invalid("重试接收记录编号无效或重复")
  const next = checked ? [...new Set([...selected, ...changed])] : selected.filter(id => !changed.includes(id))
  next.sort((a, b) => a - b)
  if (next.length > 200) throw new Error("每次最多选择 200 个失败接收记录")
  return next
}

export function manualPushReadMode(state: { hasSnapshot: boolean; loading: boolean; error: string; itemCount?: number }) {
  if (state.hasSnapshot && state.error) return "stale" as const
  if (!state.hasSnapshot && state.loading) return "loading" as const
  if (!state.hasSnapshot && state.error) return "error" as const
  if (state.hasSnapshot && state.itemCount === 0) return "empty" as const
  return "ready" as const
}

export function manualPushErrorMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 403) return `当前账号没有执行此操作的权限。${cause.message}`
  return cause instanceof Error ? cause.message : "请求未完成，请重试"
}

export function manualPushWriteMessage(cause: unknown, recovering = false): string {
  const failure = manualPushWriteFailure(cause, recovering)
  if (isManualPushRequestConflict(cause)) return "请求编号已用于其他消息内容，本次请求未覆盖原批次。请核对批次后重新确认另一请求。"
  if (failure.kind === "conflict") return `接收账号或请求状态已变化，本次未完成发送。${manualPushErrorMessage(cause)}`
  if (failure.kind === "denied") return `${manualPushErrorMessage(cause)}${recovering ? " 原请求仍保留，恢复权限后继续按原号核对。" : ""}`
  if (failure.kind === "unknown") return `${manualPushErrorMessage(cause)} 结果未知，已保存原请求号和完整内容；请先按原号查询批次，不要换号重发。`
  return manualPushErrorMessage(cause)
}
