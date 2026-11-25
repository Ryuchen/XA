import { ApiError } from "@xa/shared"

export const H5_PAGE_KINDS = ["PRICE_LIST", "ORDER_NOTICE", "INTRODUCTION"] as const
export type H5PageKind = (typeof H5_PAGE_KINDS)[number]
export type H5ComplianceStatus = "CHECKED" | "DISABLED"
export type H5StoreContext = { slug: string; name: string; bossUrl: string }

export const H5_PAGE_LABELS: Record<H5PageKind, string> = {
  PRICE_LIST: "价目表",
  ORDER_NOTICE: "下单须知",
  INTRODUCTION: "门店介绍",
}

export type H5PageSummary = {
  kind: H5PageKind
  label: string
  revision: number
  published: boolean
  publicToken: string | null
  publicPath: string | null
  jsonPath: string | null
  currentVersion: number | null
  sourceStatus: "BLOCKED" | "READY"
  sourceIssues: string[]
  draftDigest: string
}
export type H5PageDirectory = { list: H5PageSummary[]; total: number }

type H5SnapshotBase = {
  schemaVersion: 1
  kind: H5PageKind
  title: string
  store: { name: string; currencyName: string }
  generatedAt: string
}
export type H5PriceItem = {
  id: number
  name: string
  description: string
  categoryName: string
  gameName: string
  priceUnits: number
  unitPriceUnits: number
  priceText: string
  unitPriceText: string
  priceUnit: string
  billingProviderCount: number
  purchaseNote: string
  rules: string
  details: string
}
export type H5Snapshot = H5SnapshotBase & (
  | { kind: "PRICE_LIST"; items: H5PriceItem[] }
  | { kind: "ORDER_NOTICE"; sections: Array<{ id: number; title: string; content: string }> }
  | { kind: "INTRODUCTION"; body: string }
)
export type H5Preview = {
  kind: H5PageKind
  publishable: boolean
  issues: string[]
  draftDigest: string
  snapshot: H5Snapshot
}
export type H5VersionSummary = {
  version: number
  contentSha256: string
  complianceStatus: H5ComplianceStatus
  complianceReceiptIds: number[]
  copyrightConfirmed: true
  publishedAt: string
}
export type H5VersionList = { list: H5VersionSummary[]; total: number }
export type H5VersionDetail = H5VersionSummary & { snapshot: H5Snapshot }
export type H5ComplianceReceipt = {
  id: number
  contentType: "h5_page"
  objectId: string
  fieldName: string
  contentSha256: string
  ruleVersion: number
  ruleSetSha256: string
  matchedRuleCodes: string[]
  exemptedRuleCodes: string[]
  actorLabel: string
  createdAt: string
}

export type H5PublishPayload = {
  client_request_id: string
  expected_version: number
  copyright_confirmed: true
}
export type H5WithdrawPayload = {
  client_request_id: string
  expected_version: number
  reason: string
}
export type H5WriteIntent = {
  version: 1
  storeSlug: string
  sessionId: string
  kind: H5PageKind
} & (
  | { operation: "publish"; payload: H5PublishPayload }
  | { operation: "withdraw"; payload: H5WithdrawPayload }
)
export type H5PublishResult = {
  kind: H5PageKind
  publicToken: string
  publicPath: string
  jsonPath: string
  revision: number
  published: true
  currentVersion: number
  version: number
  contentSha256: string
  publishedAt: string
  complianceStatus: H5ComplianceStatus
  copyrightConfirmed: true
  replayed: boolean
}
export type H5WithdrawResult = {
  kind: H5PageKind
  publicToken: string
  publicPath: string
  jsonPath: string
  revision: number
  published: false
  currentVersion: null
  version: null
  contentSha256: null
  publishedAt: null
  complianceStatus: null
  copyrightConfirmed: null
  replayed: boolean
  withdrawnVersion: number
  withdrawnAt: string
}
export type H5MutationResult = H5PublishResult | H5WithdrawResult

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const DIGEST = /^[a-f0-9]{64}$/
const STORE_SLUG = /^[a-z][a-z0-9-]{0,47}$/
const ISO_DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
function exactKeys(value: Record<string, unknown>, keys: string) {
  return Object.keys(value).sort().join(",") === keys
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
function digest(value: unknown): value is string {
  return typeof value === "string" && DIGEST.test(value)
}
function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text)
}
function kind(value: unknown): value is H5PageKind {
  return typeof value === "string" && H5_PAGE_KINDS.includes(value as H5PageKind)
}
function publicIdentity(value: Record<string, unknown>, token: string) {
  return value.publicToken === token && value.publicPath === `/h5/pages/${token}/` &&
    value.jsonPath === `/api/club/h5-pages/${token}/`
}
function invalid(message: string): never {
  throw new Error(message)
}

function parseSnapshot(value: unknown, expected: { kind: H5PageKind; storeName: string }): H5Snapshot {
  if (!record(value) || value.schemaVersion !== 1 || value.kind !== expected.kind ||
      !text(value.title) || !dateTime(value.generatedAt) || !record(value.store) ||
      !exactKeys(value.store, "currencyName,name") || value.store.name !== expected.storeName ||
      !text(value.store.currencyName)) {
    invalid("H5 快照的门店、类型或基础结构不一致")
  }
  const base = {
    schemaVersion: 1 as const,
    kind: expected.kind,
    title: value.title,
    store: { name: value.store.name, currencyName: value.store.currencyName },
    generatedAt: value.generatedAt,
  }
  if (expected.kind === "INTRODUCTION") {
    if (!exactKeys(value, "body,generatedAt,kind,schemaVersion,store,title") || !text(value.body)) {
      invalid("门店介绍快照结构不一致")
    }
    return { ...base, kind: "INTRODUCTION", body: value.body }
  }
  if (expected.kind === "ORDER_NOTICE") {
    if (!exactKeys(value, "generatedAt,kind,schemaVersion,sections,store,title") || !Array.isArray(value.sections)) {
      invalid("下单须知快照结构不一致")
    }
    const sections = value.sections.map((item, index) => {
      if (!record(item) || !exactKeys(item, "content,id,title") || !integer(item.id, true) ||
          !text(item.title) || !text(item.content)) invalid(`下单须知第 ${index + 1} 段结构不一致`)
      return { id: item.id, title: item.title, content: item.content }
    })
    return { ...base, kind: "ORDER_NOTICE", sections }
  }
  if (!exactKeys(value, "generatedAt,items,kind,schemaVersion,store,title") || !Array.isArray(value.items)) {
    invalid("价目表快照结构不一致")
  }
  const itemKeys = "billingProviderCount,categoryName,description,details,gameName,id,name,priceText,priceUnit,priceUnits,purchaseNote,rules,unitPriceText,unitPriceUnits"
  const items = value.items.map((item, index) => {
    if (!record(item) || !exactKeys(item, itemKeys) || !integer(item.id, true) ||
        !text(item.name) || !text(item.description) || !text(item.categoryName) || !text(item.gameName) ||
        !integer(item.priceUnits, true) || !integer(item.unitPriceUnits, true) ||
        !text(item.priceText) || !text(item.unitPriceText) || !text(item.priceUnit) ||
        !integer(item.billingProviderCount, true) || !text(item.purchaseNote) ||
        !text(item.rules) || !text(item.details) ||
        item.priceUnits !== item.unitPriceUnits * item.billingProviderCount) {
      invalid(`价目表第 ${index + 1} 项结构或金额不一致`)
    }
    return {
      id: item.id, name: item.name, description: item.description,
      categoryName: item.categoryName, gameName: item.gameName,
      priceUnits: item.priceUnits, unitPriceUnits: item.unitPriceUnits,
      priceText: item.priceText, unitPriceText: item.unitPriceText,
      priceUnit: item.priceUnit, billingProviderCount: item.billingProviderCount,
      purchaseNote: item.purchaseNote, rules: item.rules, details: item.details,
    }
  })
  return { ...base, kind: "PRICE_LIST", items }
}

export function parseH5PageDirectory(value: unknown): H5PageDirectory {
  if (!record(value) || !exactKeys(value, "list,total") || !Array.isArray(value.list) || value.total !== H5_PAGE_KINDS.length || value.list.length !== value.total) {
    invalid("H5 页面目录结构待核对")
  }
  const keys = "currentVersion,draftDigest,jsonPath,kind,label,publicPath,publicToken,published,revision,sourceIssues,sourceStatus"
  const seen = new Set<H5PageKind>()
  const list = value.list.map(item => {
    if (!record(item) || !exactKeys(item, keys) || !kind(item.kind) || seen.has(item.kind) ||
        item.label !== H5_PAGE_LABELS[item.kind] || !integer(item.revision) ||
        typeof item.published !== "boolean" || !digest(item.draftDigest) ||
        !(item.sourceStatus === "READY" || item.sourceStatus === "BLOCKED") ||
        !stringList(item.sourceIssues) || (item.sourceStatus === "READY") !== (item.sourceIssues.length === 0) ||
        !(item.publicToken === null || (typeof item.publicToken === "string" && UUID.test(item.publicToken))) ||
        !(item.currentVersion === null || integer(item.currentVersion, true))) {
      invalid("H5 页面目录记录待核对")
    }
    seen.add(item.kind)
    if (item.publicToken === null) {
      if (item.revision !== 0 || item.published || item.currentVersion !== null || item.publicPath !== null || item.jsonPath !== null) {
        invalid("未创建 H5 页面的公开身份待核对")
      }
    } else if (!publicIdentity(item, item.publicToken) || item.revision < 1 ||
               (item.published ? item.currentVersion !== item.revision : item.currentVersion !== null)) {
      invalid("H5 页面公开身份或当前版本待核对")
    }
    return {
      kind: item.kind, label: item.label, revision: item.revision, published: item.published,
      publicToken: item.publicToken, publicPath: item.publicPath as string | null,
      jsonPath: item.jsonPath as string | null, currentVersion: item.currentVersion,
      sourceStatus: item.sourceStatus, sourceIssues: [...item.sourceIssues], draftDigest: item.draftDigest,
    } satisfies H5PageSummary
  })
  if (seen.size !== H5_PAGE_KINDS.length) invalid("H5 页面目录缺少页面类型")
  return { list, total: value.total }
}

export function parseH5Preview(value: unknown, expected: { kind: H5PageKind; storeName: string }): H5Preview {
  if (!record(value) || !exactKeys(value, "draftDigest,issues,kind,publishable,snapshot") ||
      value.kind !== expected.kind || typeof value.publishable !== "boolean" ||
      !stringList(value.issues) || !digest(value.draftDigest) ||
      value.publishable !== (value.issues.length === 0)) invalid("H5 预览响应结构待核对")
  const snapshot = parseSnapshot(value.snapshot, expected)
  return { kind: expected.kind, publishable: value.publishable, issues: [...value.issues], draftDigest: value.draftDigest, snapshot }
}

function parseVersionSummary(value: unknown): H5VersionSummary {
  const keys = "complianceReceiptIds,complianceStatus,contentSha256,copyrightConfirmed,publishedAt,version"
  if (!record(value) || !exactKeys(value, keys) || !integer(value.version, true) || !digest(value.contentSha256) ||
      !(value.complianceStatus === "CHECKED" || value.complianceStatus === "DISABLED") ||
      !Array.isArray(value.complianceReceiptIds) || value.copyrightConfirmed !== true || !dateTime(value.publishedAt) ||
      !value.complianceReceiptIds.every(id => integer(id, true)) ||
      new Set(value.complianceReceiptIds).size !== value.complianceReceiptIds.length ||
      (value.complianceStatus === "DISABLED" && value.complianceReceiptIds.length !== 0)) {
    invalid("H5 历史版本结构待核对")
  }
  return {
    version: value.version, contentSha256: value.contentSha256,
    complianceStatus: value.complianceStatus,
    complianceReceiptIds: [...value.complianceReceiptIds],
    copyrightConfirmed: true, publishedAt: value.publishedAt,
  }
}

export function parseH5VersionList(value: unknown, expected: { latestRevision: number }): H5VersionList {
  if (!record(value) || !exactKeys(value, "list,total") || !Array.isArray(value.list) ||
      !integer(value.total) || value.total !== value.list.length) invalid("H5 历史版本目录待核对")
  const list = value.list.map(parseVersionSummary)
  const versions = new Set<number>()
  list.forEach((item, index) => {
    if (versions.has(item.version) || item.version > expected.latestRevision ||
        (index > 0 && list[index - 1].version <= item.version)) invalid("H5 历史版本顺序或归属待核对")
    versions.add(item.version)
  })
  return { list, total: value.total }
}

export function parseH5VersionDetail(value: unknown, expected: { kind: H5PageKind; storeName: string; version: number }): H5VersionDetail {
  if (!record(value) || !exactKeys(value, "complianceReceiptIds,complianceStatus,contentSha256,copyrightConfirmed,publishedAt,snapshot,version")) {
    invalid("H5 历史版本详情待核对")
  }
  const summary = parseVersionSummary({
    version: value.version, contentSha256: value.contentSha256,
    complianceStatus: value.complianceStatus, complianceReceiptIds: value.complianceReceiptIds,
    copyrightConfirmed: value.copyrightConfirmed, publishedAt: value.publishedAt,
  })
  if (summary.version !== expected.version) invalid("H5 历史版本编号与请求不一致")
  return { ...summary, snapshot: parseSnapshot(value.snapshot, expected) }
}

export function parseH5ReceiptList(value: unknown, expected: { receiptIds: number[] }): { list: H5ComplianceReceipt[]; total: number } {
  if (!record(value) || !exactKeys(value, "list,total") || !Array.isArray(value.list) ||
      value.total !== expected.receiptIds.length || value.list.length !== value.total) invalid("H5 合规回执数量与版本不一致")
  const keys = "actorLabel,contentSha256,contentType,createdAt,exemptedRuleCodes,fieldName,id,matchedRuleCodes,objectId,ruleSetSha256,ruleVersion"
  const seen = new Set<number>()
  let objectId = ""
  const list = value.list.map(item => {
    if (!record(item) || !exactKeys(item, keys) || !integer(item.id, true) || seen.has(item.id) ||
        !expected.receiptIds.includes(item.id) || item.contentType !== "h5_page" ||
        !text(item.objectId) || !item.objectId || (objectId && objectId !== item.objectId) ||
        !text(item.fieldName) || !digest(item.contentSha256) || !integer(item.ruleVersion, true) ||
        !digest(item.ruleSetSha256) || !stringList(item.matchedRuleCodes) ||
        !stringList(item.exemptedRuleCodes) || !text(item.actorLabel) || !dateTime(item.createdAt)) {
      invalid("H5 合规回执结构或版本身份待核对")
    }
    seen.add(item.id)
    objectId = item.objectId
    return {
      id: item.id, contentType: "h5_page" as const, objectId: item.objectId,
      fieldName: item.fieldName, contentSha256: item.contentSha256, ruleVersion: item.ruleVersion,
      ruleSetSha256: item.ruleSetSha256, matchedRuleCodes: [...item.matchedRuleCodes],
      exemptedRuleCodes: [...item.exemptedRuleCodes], actorLabel: item.actorLabel, createdAt: item.createdAt,
    }
  })
  if (seen.size !== new Set(expected.receiptIds).size) invalid("H5 合规回执与版本记录不完整")
  return { list, total: value.total }
}

function mutationBase(value: unknown, expectedKind: H5PageKind, extraKeys: string): Record<string, unknown> & {
  kind: H5PageKind
  publicToken: string
  publicPath: string
  jsonPath: string
  revision: number
  replayed: boolean
} {
  if (!record(value) || !exactKeys(value, `complianceStatus,contentSha256,copyrightConfirmed,currentVersion,jsonPath,kind,publicPath,publicToken,published,publishedAt,replayed,revision,version${extraKeys}`) ||
      value.kind !== expectedKind || typeof value.publicToken !== "string" || !UUID.test(value.publicToken) ||
      !publicIdentity(value, value.publicToken) || !integer(value.revision, true) || typeof value.replayed !== "boolean") {
    invalid("H5 写入回执的门店页面或版本身份待核对")
  }
  return {
    ...value,
    kind: expectedKind,
    publicToken: value.publicToken,
    publicPath: value.publicPath as string,
    jsonPath: value.jsonPath as string,
    revision: value.revision,
    replayed: value.replayed,
  }
}

export function parseH5MutationResult(value: unknown, intent: H5WriteIntent): H5MutationResult {
  if (intent.operation === "publish") {
    const row = mutationBase(value, intent.kind, "")
    const expectedVersion = intent.payload.expected_version + 1
    if (row.published !== true || row.revision !== expectedVersion || row.version !== expectedVersion ||
        row.currentVersion !== expectedVersion || !digest(row.contentSha256) || !dateTime(row.publishedAt) ||
        !(row.complianceStatus === "CHECKED" || row.complianceStatus === "DISABLED") || row.copyrightConfirmed !== true) {
      invalid("H5 发布回执与请求的版本身份不一致")
    }
    return {
      kind: intent.kind, publicToken: row.publicToken, publicPath: row.publicPath as string,
      jsonPath: row.jsonPath as string, revision: row.revision, published: true,
      currentVersion: expectedVersion, version: expectedVersion, contentSha256: row.contentSha256,
      publishedAt: row.publishedAt, complianceStatus: row.complianceStatus,
      copyrightConfirmed: true, replayed: row.replayed,
    }
  }
  const row = mutationBase(value, intent.kind, ",withdrawnAt,withdrawnVersion")
  if (row.published !== false || row.revision !== intent.payload.expected_version ||
      row.currentVersion !== null || row.version !== null || row.contentSha256 !== null ||
      row.publishedAt !== null || row.complianceStatus !== null || row.copyrightConfirmed !== null ||
      row.withdrawnVersion !== intent.payload.expected_version || !dateTime(row.withdrawnAt)) {
    invalid("H5 撤回回执与请求的版本身份不一致")
  }
  return {
    kind: intent.kind, publicToken: row.publicToken, publicPath: row.publicPath as string,
    jsonPath: row.jsonPath as string, revision: row.revision, published: false,
    currentVersion: null, version: null, contentSha256: null, publishedAt: null,
    complianceStatus: null, copyrightConfirmed: null, replayed: row.replayed,
    withdrawnVersion: row.withdrawnVersion, withdrawnAt: row.withdrawnAt,
  }
}

export function createH5WriteIntent(
  context: { storeSlug: string; sessionId: string },
  kindValue: H5PageKind,
  operation: "publish" | "withdraw",
  expectedVersion: number,
  clientRequestId: string,
  reason = "",
): H5WriteIntent {
  if (!STORE_SLUG.test(context.storeSlug) || !context.sessionId || context.sessionId.length > 128 ||
      !kind(kindValue) || !integer(expectedVersion) || !clientRequestId.trim() ||
      clientRequestId.trim() !== clientRequestId || clientRequestId.length > 64) invalid("H5 写入身份或请求编号无效")
  const base = { version: 1 as const, storeSlug: context.storeSlug, sessionId: context.sessionId, kind: kindValue }
  if (operation === "publish") return { ...base, operation, payload: { client_request_id: clientRequestId, expected_version: expectedVersion, copyright_confirmed: true } }
  const trimmed = reason.trim()
  if (!trimmed || trimmed.length > 255) invalid("请填写 255 字以内的撤回原因")
  return { ...base, operation, payload: { client_request_id: clientRequestId, expected_version: expectedVersion, reason: trimmed } }
}

export function parseH5WriteIntent(raw: string | null, context: { storeSlug: string; sessionId: string }): H5WriteIntent | null {
  if (raw === null) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (!record(value) || !exactKeys(value, "kind,operation,payload,sessionId,storeSlug,version") ||
        value.version !== 1 || value.storeSlug !== context.storeSlug || value.sessionId !== context.sessionId ||
        !kind(value.kind) || !record(value.payload)) invalid("invalid")
    const base = { version: 1 as const, storeSlug: value.storeSlug, sessionId: value.sessionId, kind: value.kind }
    const payload = value.payload
    if (typeof payload.client_request_id !== "string" || !payload.client_request_id.trim() ||
        payload.client_request_id.trim() !== payload.client_request_id || payload.client_request_id.length > 64 ||
        !integer(payload.expected_version)) invalid("invalid")
    if (value.operation === "publish") {
      if (!exactKeys(payload, "client_request_id,copyright_confirmed,expected_version") || payload.copyright_confirmed !== true) invalid("invalid")
      return { ...base, operation: "publish", payload: { client_request_id: payload.client_request_id, expected_version: payload.expected_version, copyright_confirmed: true } }
    }
    if (value.operation === "withdraw" && exactKeys(payload, "client_request_id,expected_version,reason") &&
        typeof payload.reason === "string" && payload.reason.trim() && payload.reason.trim() === payload.reason && payload.reason.length <= 255) {
      return { ...base, operation: "withdraw", payload: { client_request_id: payload.client_request_id, expected_version: payload.expected_version, reason: payload.reason } }
    }
    invalid("invalid")
  } catch {
    throw new Error("H5 待核对请求无法解析，已阻止新写入；请保留浏览器记录并联系管理员。")
  }
}

export function h5WriteFailure(cause: unknown, recovering = false): { kind: "denied" | "conflict" | "rejected" | "unknown"; clear: boolean } {
  const error = cause as { status?: number; errorCode?: string; body?: { errorCode?: string } } | null
  const status = error?.status || 0
  const code = error?.errorCode || error?.body?.errorCode
  if (code === "H5_REVISION_CONFLICT" || code === "H5_PAGE_NOT_PUBLISHED") return { kind: "conflict", clear: true }
  if (code === "REQUEST_ID_CONFLICT" || code === "H5_RESULT_UNAVAILABLE") return { kind: "unknown", clear: false }
  if (status === 400) return { kind: "rejected", clear: true }
  if (status === 403) return { kind: "denied", clear: !recovering }
  if (!recovering && (status === 401 || status === 404)) return { kind: "rejected", clear: true }
  return { kind: "unknown", clear: false }
}

export async function submitH5Write(intent: H5WriteIntent, io: {
  save: (value: H5WriteIntent) => void
  send: (value: H5WriteIntent) => Promise<H5MutationResult>
  clear: () => void
}, options: { recovering?: boolean } = {}): Promise<H5MutationResult> {
  io.save(intent)
  try {
    const result = await io.send(intent)
    io.clear()
    return result
  } catch (cause) {
    if (h5WriteFailure(cause, options.recovering).clear) {
      try { io.clear() } catch { /* An uncertain local record is safer than silently replacing it. */ }
    }
    throw cause
  }
}

export function h5PublicUrl(publicPath: string, store: H5StoreContext): string {
  if (!STORE_SLUG.test(store.slug) || !store.name || !store.bossUrl) invalid("当前门店尚未配置可用的老板端源站")
  let base: URL
  try { base = new URL(store.bossUrl) } catch { invalid("当前门店老板端源站无效") }
  if ((base.protocol !== "http:" && base.protocol !== "https:") || base.username || base.password) invalid("当前门店老板端源站无效")
  const target = new URL(publicPath, base.origin)
  if (target.origin !== base.origin || !/^\/h5\/pages\/[0-9a-f-]+\/$/i.test(target.pathname) || target.search || target.hash) {
    invalid("H5 公开路径未保持当前门店源站")
  }
  return target.href
}

export function h5ErrorMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 403) return `当前账号没有执行此操作的权限。${cause.message}`
  return cause instanceof Error ? cause.message : "请求未完成，请重试"
}

export function h5WriteMessage(cause: unknown, recovering = false): string {
  const error = cause as { status?: number; errorCode?: string; body?: { errorCode?: string } } | null
  const code = error?.errorCode || error?.body?.errorCode
  const failure = h5WriteFailure(cause, recovering)
  if (code === "H5_REVISION_CONFLICT") return "页面版本已变化，本次写入未执行。请核对刷新后的内容，再重新确认。"
  if (code === "H5_PAGE_NOT_PUBLISHED") return "页面当前已未发布，本次撤回未重复执行。请刷新核对。"
  if (code === "REQUEST_ID_CONFLICT") return "请求编号已用于其他操作，不能生成新发布掩盖结果；请联系管理员核对原请求。"
  if (code === "H5_RESULT_UNAVAILABLE") return "服务端已受理原请求但回执暂不可用，请继续使用同号恢复或联系管理员。"
  if (failure.kind === "denied") return `操作被权限策略拒绝。${h5ErrorMessage(cause)}${recovering ? " 原请求仍保留，恢复权限后继续同号核对。" : ""}`
  if (failure.kind === "unknown") return `${h5ErrorMessage(cause)} 操作结果未知，已保留原请求编号和完整内容，请使用同号恢复，不要重新发布。`
  return h5ErrorMessage(cause)
}
