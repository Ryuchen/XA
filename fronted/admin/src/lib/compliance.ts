import { sha256 } from "@noble/hashes/sha2.js"
import { bytesToHex, utf8ToBytes } from "@noble/hashes/utils.js"
import { PlatformRequestError, platformMutationResultUnknown } from "./platform-session.ts"

export const COMPLIANCE_FIELDS = [
  "STORE_NAME",
  "STORE_INTRODUCTION",
  "SERVICE_RULES",
  "SERVICE_DETAILS",
  "GAMEPLAY_RULES",
  "GAMEPLAY_DETAILS",
  "PROVIDER_DISPLAY_NAME",
  "PROVIDER_REGISTRATION_DISPLAY_NAME",
] as const

export const COMPLIANCE_CATEGORIES = [
  "EXTREME_WORD",
  "INTERPRETATION_CLAUSE",
  "NICKNAME_RESERVED",
] as const

export type ComplianceField = (typeof COMPLIANCE_FIELDS)[number]
export type ComplianceCategory = (typeof COMPLIANCE_CATEGORIES)[number]

export type ComplianceRule = {
  code: string
  category: ComplianceCategory
  applies_to: ComplianceField[]
  terms: string[]
  message: string
  enabled: boolean
}

export type ComplianceRuleVersion = {
  version: number
  status: "DRAFT" | "PUBLISHED"
  rules: ComplianceRule[]
  rulesSha256: string
  ruleCount: number
  createdAt: string | null
  publishedAt: string | null
}

export type ComplianceRuleDirectoryContext = {
  page: number
  pageSize: number
  origin?: string
}

export type ComplianceRuleDirectory = {
  current: ComplianceRuleVersion | null
  versions: ComplianceRuleVersion[]
  count: number
  next: string | null
  previous: string | null
  latestVersion: number
  page: number
  pageSize: number
}

export type ComplianceReceipt = {
  id: number
  storeId: number
  storeSlug: string
  storeName: string
  fieldName: ComplianceField
  contentSha256: string
  ruleVersion: number
  ruleSetSha256: string
  matchedRuleCodes: string[]
  exemptedRuleCodes: string[]
  actorType: string
  actorId: string | null
  actorLabel: string
  createdAt: string
}

export type ComplianceReceiptPage = {
  count: number
  next: string | null
  previous: string | null
  results: ComplianceReceipt[]
}

export type ComplianceReceiptPageContext = {
  storeId: number
  storeSlug: string
  fieldName: "" | ComplianceField
  page: number
  pageSize: number
  origin?: string
}

export const complianceFieldLabels: Record<ComplianceField, string> = {
  STORE_NAME: "门店名称",
  STORE_INTRODUCTION: "门店介绍",
  SERVICE_RULES: "服务价目说明",
  SERVICE_DETAILS: "服务详情",
  GAMEPLAY_RULES: "玩法须知",
  GAMEPLAY_DETAILS: "玩法详情",
  PROVIDER_DISPLAY_NAME: "陪玩昵称",
  PROVIDER_REGISTRATION_DISPLAY_NAME: "陪玩注册昵称",
}

export const complianceCategoryLabels: Record<ComplianceCategory, string> = {
  EXTREME_WORD: "极限词与价格引流",
  INTERPRETATION_CLAUSE: "单方解释权条款",
  NICKNAME_RESERVED: "昵称保留词",
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function exactKeys(value: Record<string, unknown>, keys: string) {
  return Object.keys(value).sort().join(",") === keys
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
}

function digest(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value)
}

const ISO_DATE_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/

function dateValue(value: unknown, nullable = false): value is string | null {
  if (nullable && value === null) return true
  if (typeof value !== "string") return false
  const match = ISO_DATE_TIME.exec(value)
  if (!match || !Number.isFinite(Date.parse(value))) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  const offsetHour = match[8] === undefined ? 0 : Number(match[8])
  const offsetMinute = match[9] === undefined ? 0 : Number(match[9])
  if (year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) return false
  const calendar = new Date(0)
  calendar.setUTCHours(0, 0, 0, 0)
  calendar.setUTCFullYear(year, month, 0)
  return day <= calendar.getUTCDate()
}

function stringList(value: unknown, maxLength: number): value is string[] {
  return Array.isArray(value) && value.length <= maxLength && value.every(item => typeof item === "string")
}

const htmlEntities: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
}

export function normalizeComplianceText(value: string): string {
  const decoded = value.normalize("NFKC").replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]+);/gi, (whole, entity: string) => {
    if (entity.startsWith("#")) {
      const hexadecimal = entity[1]?.toLowerCase() === "x"
      const point = Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10)
      return point >= 0 && point <= 0x10ffff && !(point >= 0xd800 && point <= 0xdfff)
        ? String.fromCodePoint(point)
        : whole
    }
    return htmlEntities[entity.toLowerCase()] || whole
  })
  return (decoded.replace(/<[^>]*>/g, "").toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ").match(/[\p{L}\p{N}]/gu) || []).join("")
}

export function parseComplianceRules(value: unknown): ComplianceRule[] {
  if (!Array.isArray(value) || value.length > 500) throw new Error("规则须为不超过 500 条的数组")
  const codes = new Set<string>()
  return value.map((item, index) => {
    if (!record(item)) throw new Error(`第 ${index + 1} 条规则结构无效`)
    const keys = Object.keys(item).sort()
    if (keys.join(",") !== "applies_to,category,code,enabled,message,terms") {
      throw new Error(`第 ${index + 1} 条规则包含缺失或未知字段`)
    }
    if (typeof item.code !== "string" || !item.code.trim() || item.code.trim().length > 80 ||
        !/^[\p{L}\p{N}._:-]+$/u.test(item.code.trim()) || codes.has(item.code.trim())) {
      throw new Error(`第 ${index + 1} 条规则编号为空、格式错误、过长或重复`)
    }
    if (!COMPLIANCE_CATEGORIES.includes(item.category as ComplianceCategory)) {
      throw new Error(`第 ${index + 1} 条规则类别无效`)
    }
    if (!Array.isArray(item.applies_to) || !item.applies_to.length ||
        !item.applies_to.every(field => COMPLIANCE_FIELDS.includes(field as ComplianceField)) ||
        new Set(item.applies_to).size !== item.applies_to.length) {
      throw new Error(`第 ${index + 1} 条规则适用字段无效`)
    }
    if (!Array.isArray(item.terms) || !item.terms.length || item.terms.length > 30 ||
        !item.terms.every(term => typeof term === "string" && !!term.trim() && term.trim().length <= 100)) {
      throw new Error(`第 ${index + 1} 条规则匹配词为空、过长或重复`)
    }
    const terms = item.terms.map(term => (term as string).trim())
    const normalizedTerms = terms.map(normalizeComplianceText)
    if (normalizedTerms.some(term => !term) || new Set(normalizedTerms).size !== normalizedTerms.length) {
      throw new Error(`第 ${index + 1} 条规则匹配词为空、过长或重复`)
    }
    if (typeof item.message !== "string" || !item.message.trim() || item.message.trim().length > 300) {
      throw new Error(`第 ${index + 1} 条规则提示为空或过长`)
    }
    if (typeof item.enabled !== "boolean") throw new Error(`第 ${index + 1} 条规则启用状态无效`)
    const code = item.code.trim()
    codes.add(code)
    return {
      code,
      category: item.category as ComplianceCategory,
      applies_to: [...item.applies_to] as ComplianceField[],
      terms: normalizedTerms,
      message: item.message.trim(),
      enabled: item.enabled,
    }
  })
}

export function parseComplianceRulesJson(raw: string): ComplianceRule[] {
  if (raw.length > 200_000) throw new Error("规则 JSON 超过 200000 字符，请拆分为更小的规则集")
  try {
    return parseComplianceRules(JSON.parse(raw))
  } catch (error) {
    if (error instanceof Error && (error.message.startsWith("第 ") || error.message === "规则须为不超过 500 条的数组")) throw error
    throw new Error("规则 JSON 格式无效，请检查逗号、引号和括号")
  }
}

export function complianceRulesSha256(rules: ComplianceRule[]): string {
  const serialized = `[${rules.map(rule => JSON.stringify({
    applies_to: rule.applies_to,
    category: rule.category,
    code: rule.code,
    enabled: rule.enabled,
    message: rule.message,
    terms: rule.terms,
  })).join(",")}]`
  return bytesToHex(sha256(utf8ToBytes(serialized)))
}

function sameComplianceRules(left: ComplianceRule[], right: ComplianceRule[]) {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function parseComplianceRuleVersion(value: unknown): ComplianceRuleVersion {
  if (!record(value) || !exactKeys(value, "createdAt,publishedAt,ruleCount,rules,rulesSha256,status,version") ||
      !positiveInteger(value.version) || (value.status !== "DRAFT" && value.status !== "PUBLISHED") ||
      !digest(value.rulesSha256) || !nonNegativeInteger(value.ruleCount) ||
      !dateValue(value.createdAt, true) || !dateValue(value.publishedAt, true)) {
    throw new Error("合规规则版本响应结构待核对")
  }
  const rules = parseComplianceRules(value.rules)
  if (value.ruleCount !== rules.length || (value.status === "DRAFT" && value.publishedAt !== null)) {
    throw new Error("合规规则版本的数量或状态待核对")
  }
  if (value.rulesSha256 !== complianceRulesSha256(rules)) throw new Error("合规规则版本的摘要与规则内容不一致")
  return {
    version: value.version,
    status: value.status,
    rules,
    rulesSha256: value.rulesSha256,
    ruleCount: value.ruleCount,
    createdAt: value.createdAt,
    publishedAt: value.publishedAt,
  }
}

function ruleDirectoryPageLink(value: unknown, context: ComplianceRuleDirectoryContext, direction: 1 | -1): string | null {
  if (value === null) return null
  if (typeof value !== "string" || !value) throw new Error("合规规则目录分页链接待核对")
  let url: URL
  try { url = new URL(value) }
  catch { throw new Error("合规规则目录分页链接待核对") }
  const expectedOrigin = context.origin || globalThis.location?.origin
  if (!expectedOrigin || (url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password || url.hash ||
      url.pathname !== "/api/admin/platform/compliance-rules") {
    throw new Error("合规规则目录分页链接待核对")
  }
  let origin: URL
  try { origin = new URL(expectedOrigin) }
  catch { throw new Error("合规规则目录分页链接待核对") }
  if (url.origin !== origin.origin) throw new Error("合规规则目录分页链接待核对")
  const seen = new Set<string>()
  for (const name of url.searchParams.keys()) {
    if (name !== "page" && name !== "page_size") throw new Error("合规规则目录分页链接待核对")
    if (seen.has(name)) throw new Error("合规规则目录分页链接待核对")
    seen.add(name)
  }
  const targetPage = context.page + direction
  if (seen.size !== 2 || !positiveInteger(targetPage) || url.searchParams.get("page") !== String(targetPage) ||
      url.searchParams.get("page_size") !== String(context.pageSize)) {
    throw new Error("合规规则目录分页链接未保持分页上下文")
  }
  return value
}

export function parseComplianceRuleDirectory(value: unknown, context: ComplianceRuleDirectoryContext): ComplianceRuleDirectory {
  if (!positiveInteger(context.page) || !positiveInteger(context.pageSize) || context.pageSize > 100) {
    throw new Error("合规规则目录分页上下文待核对")
  }
  if (!record(value) || !exactKeys(value, "count,current,latestVersion,next,previous,versions") ||
      !Array.isArray(value.versions) || !nonNegativeInteger(value.count) || value.count > 500 ||
      !positiveInteger(value.latestVersion) ||
      !(value.next === null || typeof value.next === "string") ||
      !(value.previous === null || typeof value.previous === "string") ||
      value.versions.length > context.pageSize) {
    throw new Error("合规规则目录响应结构待核对")
  }
  const versions = value.versions.map(parseComplianceRuleVersion)
  const current = value.current === null ? null : parseComplianceRuleVersion(value.current)
  const totalPages = Math.max(1, Math.ceil(value.count / context.pageSize))
  const expectedLength = value.count === 0 ? 0 : Math.min(context.pageSize, value.count - (context.page - 1) * context.pageSize)
  if (context.page > totalPages || versions.length !== expectedLength || value.count < versions.length) {
    throw new Error("合规规则目录的总数或页长度待核对")
  }
  for (let index = 0; index < versions.length; index += 1) {
    if (versions[index].version > value.latestVersion ||
        (index > 0 && versions[index - 1].version <= versions[index].version)) {
      throw new Error("合规规则目录的版本顺序或最新版本待核对")
    }
  }
  if (context.page === 1 && versions.length && versions[0].version !== value.latestVersion) {
    throw new Error("合规规则目录的最新版本与首页不一致")
  }
  if (current) {
    if (current.status !== "PUBLISHED" || current.version > value.latestVersion) {
      throw new Error("合规规则目录：当前生效规则不是有效版本")
    }
    const listed = versions.find(item => item.version === current.version)
    if (!listed && versions.length && current.version <= versions[0].version &&
        current.version >= versions[versions.length - 1].version) {
      throw new Error("合规规则目录：当前生效规则未出现在所在页")
    }
    if (listed && (listed.status !== current.status || listed.rulesSha256 !== current.rulesSha256 ||
        listed.ruleCount !== current.ruleCount || listed.createdAt !== current.createdAt ||
        listed.publishedAt !== current.publishedAt || !sameComplianceRules(listed.rules, current.rules))) {
      throw new Error("合规规则目录：当前生效规则与版本目录不一致")
    }
  }
  const hasNext = value.count > context.page * context.pageSize
  const hasPrevious = context.page > 1
  if ((value.next !== null) !== hasNext || (value.previous !== null) !== hasPrevious) {
    throw new Error("合规规则目录分页链接待核对")
  }
  return {
    current,
    versions,
    count: value.count,
    next: ruleDirectoryPageLink(value.next, context, 1),
    previous: ruleDirectoryPageLink(value.previous, context, -1),
    latestVersion: value.latestVersion,
    page: context.page,
    pageSize: context.pageSize,
  }
}

export function nextComplianceDraftVersion(directory: ComplianceRuleDirectory | null): number {
  return (directory?.latestVersion || 0) + 1
}

export function parseComplianceDraftResult(value: unknown, expected: { version: number; rules: ComplianceRule[] }): ComplianceRuleVersion {
  const created = parseComplianceRuleVersion(value)
  if (created.status !== "DRAFT" || created.version !== expected.version ||
      !sameComplianceRules(created.rules, expected.rules) || created.ruleCount !== expected.rules.length ||
      created.rulesSha256 !== complianceRulesSha256(expected.rules)) {
    throw new Error("规则草稿响应与请求不一致，结果待核对，请刷新版本列表")
  }
  return created
}

export function parseCompliancePublishResult(value: unknown, expected: ComplianceRuleVersion): ComplianceRuleVersion {
  const published = parseComplianceRuleVersion(value)
  if (published.status !== "PUBLISHED" || published.version !== expected.version ||
      published.publishedAt === null || !sameComplianceRules(published.rules, expected.rules) ||
      published.ruleCount !== expected.ruleCount || published.rulesSha256 !== expected.rulesSha256 ||
      published.rulesSha256 !== complianceRulesSha256(expected.rules)) {
    throw new Error("规则发布响应与请求不一致，结果待核对，请刷新版本列表")
  }
  return published
}

export function complianceWriteFailure(cause: unknown): { denied: boolean; uncertain: boolean } {
  const denied = cause instanceof PlatformRequestError && cause.status === 403
  return { denied, uncertain: !denied && platformMutationResultUnknown(cause) }
}

function parseReceipt(value: unknown): ComplianceReceipt {
  const keys = "actorId,actorLabel,actorType,contentSha256,createdAt,exemptedRuleCodes,fieldName,id,matchedRuleCodes,ruleSetSha256,ruleVersion,storeId,storeName,storeSlug"
  if (!record(value) || !exactKeys(value, keys) || !positiveInteger(value.id) || !positiveInteger(value.storeId) ||
      typeof value.storeSlug !== "string" || !value.storeSlug || typeof value.storeName !== "string" || !value.storeName ||
      !COMPLIANCE_FIELDS.includes(value.fieldName as ComplianceField) ||
      !digest(value.contentSha256) || !positiveInteger(value.ruleVersion) ||
      !digest(value.ruleSetSha256) || !stringList(value.matchedRuleCodes, 500) ||
      !stringList(value.exemptedRuleCodes, 500) || typeof value.actorType !== "string" || !value.actorType ||
      !(value.actorId === null || typeof value.actorId === "string" || positiveInteger(value.actorId)) ||
      typeof value.actorLabel !== "string" || !dateValue(value.createdAt)) {
    throw new Error("门店内容合规回执响应结构待核对")
  }
  return {
    id: value.id,
    storeId: value.storeId,
    storeSlug: value.storeSlug,
    storeName: value.storeName,
    fieldName: value.fieldName as ComplianceField,
    contentSha256: value.contentSha256,
    ruleVersion: value.ruleVersion,
    ruleSetSha256: value.ruleSetSha256,
    matchedRuleCodes: value.matchedRuleCodes,
    exemptedRuleCodes: value.exemptedRuleCodes,
    actorType: value.actorType,
    actorId: value.actorId === null ? null : String(value.actorId),
    actorLabel: value.actorLabel,
    createdAt: value.createdAt as string,
  }
}

function receiptPageLink(value: unknown, context: ComplianceReceiptPageContext, direction: 1 | -1): string | null {
  if (value === null) return null
  if (typeof value !== "string" || !value) throw new Error("门店内容合规回执分页链接待核对")
  let url: URL
  try { url = new URL(value) }
  catch { throw new Error("门店内容合规回执分页链接待核对") }
  const expectedOrigin = context.origin || globalThis.location?.origin
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password || url.hash ||
      url.pathname !== `/api/admin/platform/store-management/${context.storeId}/content-compliance-receipts`) {
    throw new Error("门店内容合规回执分页链接待核对")
  }
  if (expectedOrigin) {
    let origin: URL
    try { origin = new URL(expectedOrigin) }
    catch { throw new Error("门店内容合规回执分页链接待核对") }
    if (url.origin !== origin.origin) throw new Error("门店内容合规回执分页链接待核对")
  }
  const allowed = new Set(["page", "page_size", ...(context.fieldName ? ["field_name"] : [])])
  const seen = new Set<string>()
  for (const name of url.searchParams.keys()) {
    if (!allowed.has(name) || seen.has(name)) throw new Error("门店内容合规回执分页链接待核对")
    seen.add(name)
  }
  const targetPage = context.page + direction
  if (!positiveInteger(targetPage) || url.searchParams.get("page") !== String(targetPage) ||
      url.searchParams.get("page_size") !== String(context.pageSize) ||
      (context.fieldName ? url.searchParams.get("field_name") !== context.fieldName : url.searchParams.has("field_name"))) {
    throw new Error("门店内容合规回执分页链接未保持当前筛选")
  }
  return value
}

export function parseComplianceReceiptPage(value: unknown, context: ComplianceReceiptPageContext): ComplianceReceiptPage {
  if (!record(value) || !exactKeys(value, "count,next,previous,results") ||
      typeof value.count !== "number" || !Number.isSafeInteger(value.count) || value.count < 0 ||
      !(value.next === null || typeof value.next === "string") ||
      !(value.previous === null || typeof value.previous === "string") ||
      !Array.isArray(value.results) || value.results.length > 100 ||
      !positiveInteger(context.storeId) || typeof context.storeSlug !== "string" ||
      !/^[a-z][a-z0-9-]{0,47}$/.test(context.storeSlug) ||
      (context.fieldName !== "" && !COMPLIANCE_FIELDS.includes(context.fieldName)) ||
      !positiveInteger(context.page) || !positiveInteger(context.pageSize) || context.pageSize > 100) {
    throw new Error("门店内容合规回执列表响应结构待核对")
  }
  const results = value.results.map(parseReceipt)
  if (value.count < results.length) throw new Error("门店内容合规回执总数待核对")
  if (new Set(results.map(item => item.id)).size !== results.length) throw new Error("门店内容合规回执编号重复")
  for (const item of results) {
    if (item.storeId !== context.storeId || item.storeSlug !== context.storeSlug) {
      throw new Error("门店内容合规回执的门店归属待核对")
    }
    if (context.fieldName && item.fieldName !== context.fieldName) throw new Error("门店内容合规回执的字段与当前筛选不一致")
  }
  return {
    count: value.count,
    next: receiptPageLink(value.next, context, 1),
    previous: receiptPageLink(value.previous, context, -1),
    results,
  }
}
