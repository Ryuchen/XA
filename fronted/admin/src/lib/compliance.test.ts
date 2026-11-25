import assert from "node:assert/strict"
import test from "node:test"
import {
  complianceRulesSha256,
  complianceWriteFailure,
  parseComplianceDraftResult,
  parseCompliancePublishResult,
  parseComplianceReceiptPage,
  parseComplianceRuleDirectory,
  parseComplianceRuleVersion,
  parseComplianceRules,
  parseComplianceRulesJson,
  normalizeComplianceText,
  nextComplianceDraftVersion,
  type ComplianceReceiptPageContext,
  type ComplianceRule,
  type ComplianceRuleDirectoryContext,
  type ComplianceRuleVersion,
} from "./compliance.ts"
import { PlatformNetworkError, PlatformRequestError } from "./platform-session.ts"

const rule: ComplianceRule = {
  code: "official_name",
  category: "NICKNAME_RESERVED",
  applies_to: ["PROVIDER_DISPLAY_NAME", "PROVIDER_REGISTRATION_DISPLAY_NAME"],
  terms: ["官方", "客服"],
  message: "昵称不得冒用官方或客服身份",
  enabled: true,
}

const version: ComplianceRuleVersion = {
  version: 1,
  status: "PUBLISHED",
  rules: [rule],
  rulesSha256: complianceRulesSha256([rule]),
  ruleCount: 1,
  createdAt: "2026-11-25T00:00:00Z",
  publishedAt: "2026-11-25T00:01:00Z",
}

const receipt = {
  id: 1,
  storeId: 2,
  storeSlug: "shenglang",
  storeName: "声浪电竞",
  fieldName: "PROVIDER_DISPLAY_NAME",
  contentSha256: "b".repeat(64),
  ruleVersion: 1,
  ruleSetSha256: "a".repeat(64),
  matchedRuleCodes: ["official_name"],
  exemptedRuleCodes: [],
  actorType: "PLATFORM_ACCOUNT",
  actorId: 42,
  actorLabel: "平台管理员",
  createdAt: "2026-11-25T00:02:00Z",
}

const receiptContext: ComplianceReceiptPageContext = {
  storeId: 2,
  storeSlug: "shenglang",
  fieldName: "PROVIDER_DISPLAY_NAME",
  page: 1,
  pageSize: 20,
  origin: "https://admin.esports.club",
}

function receiptLink(page: number, fieldName = "PROVIDER_DISPLAY_NAME", pageSize = 20, storeId = 2) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (fieldName) params.set("field_name", fieldName)
  return `https://admin.esports.club/api/admin/platform/store-management/${storeId}/content-compliance-receipts?${params}`
}

const directoryContext: ComplianceRuleDirectoryContext = {
  page: 2,
  pageSize: 20,
  origin: "https://admin.esports.club",
}

function numberedVersion(number: number): ComplianceRuleVersion {
  return { ...version, version: number }
}

function directoryLink(page: number, pageSize = 20, origin = "https://admin.esports.club") {
  return `${origin}/api/admin/platform/compliance-rules?${new URLSearchParams({ page: String(page), page_size: String(pageSize) })}`
}

function laterDirectoryPage(overrides: Record<string, unknown> = {}) {
  return {
    current: numberedVersion(1),
    versions: Array.from({ length: 20 }, (_, index) => numberedVersion(25 - index)),
    count: 45,
    latestVersion: 45,
    next: directoryLink(3),
    previous: directoryLink(1),
    ...overrides,
  }
}

test("规则 JSON 保留字面匹配词并拒绝重复编号", () => {
  assert.deepEqual(parseComplianceRulesJson(JSON.stringify([rule])), [rule])
  assert.throws(() => parseComplianceRules([rule, rule]), /重复/)
  assert.throws(() => parseComplianceRules([{ ...rule, code: "非法 编码" }]), /格式错误/)
  assert.deepEqual(parseComplianceRules([{ ...rule, code: " official_name " }]), [rule])
  assert.deepEqual(parseComplianceRules([{ ...rule, terms: [" ＯＦＦＩＣＩＡＬ "] }])[0].terms, ["official"])
})

test("规则 JSON 拒绝未知字段、非法类别、超限词条和正则式任意配置", () => {
  assert.throws(() => parseComplianceRules([{ ...rule, pattern: ".*" }]), /未知字段/)
  assert.throws(() => parseComplianceRules([{ ...rule, category: "REGEX" }]), /类别无效/)
  assert.throws(() => parseComplianceRules([{ ...rule, applies_to: ["ALL"] }]), /适用字段无效/)
  assert.throws(() => parseComplianceRules([{ ...rule, terms: [" "] }]), /匹配词/)
  assert.throws(() => parseComplianceRules([{ ...rule, terms: ["官方", " 官方 "] }]), /重复/)
  assert.throws(() => parseComplianceRules([{ ...rule, terms: Array.from({ length: 31 }, (_, index) => `词${index}`) }]), /匹配词/)
  assert.throws(() => parseComplianceRules([{ ...rule, terms: ["词".repeat(101)] }]), /匹配词/)
  assert.throws(() => parseComplianceRules([{ ...rule, message: "提示".repeat(151) }]), /提示/)
  assert.throws(() => parseComplianceRulesJson("{"), /JSON 格式无效/)
})

test("规则词条按服务端口径拒绝归一化后重复和空词", () => {
  assert.equal(normalizeComplianceText(" ＡＢ\u200bＣ "), "abc")
  assert.equal(normalizeComplianceText("&lt;b&gt;官方&lt;/b&gt;"), "官方")
  assert.equal(normalizeComplianceText("Straße"), "strasse")
  for (const terms of [["官方", "官方！"], ["官方", "&lt;b&gt;官方&lt;/b&gt;"], ["Ａ", "a"], ["Straße", "STRASSE"], ["！", "？"]]) {
    assert.throws(() => parseComplianceRules([{ ...rule, terms }]), /匹配词/)
  }
})

test("规则目录保留真实总数和服务端最新版本，当前版本可以不在本页", () => {
  const parsed = parseComplianceRuleDirectory(laterDirectoryPage(), directoryContext)
  assert.equal(parsed.count, 45)
  assert.equal(parsed.versions.length, 20)
  assert.equal(parsed.latestVersion, 45)
  assert.equal(parsed.current?.version, 1)
  assert.equal(parsed.page, 2)
  assert.equal(parsed.pageSize, 20)
  assert.equal(parsed.next, directoryLink(3))
  assert.equal(parsed.previous, directoryLink(1))
  assert.equal(nextComplianceDraftVersion(parsed), 46)
})

test("空规则目录使用服务端内置版本基准并禁用相邻页", () => {
  const parsed = parseComplianceRuleDirectory({
    current: version,
    versions: [],
    count: 0,
    latestVersion: 1,
    next: null,
    previous: null,
  }, { ...directoryContext, page: 1 })
  assert.deepEqual(parsed, {
    current: version,
    versions: [],
    count: 0,
    latestVersion: 1,
    next: null,
    previous: null,
    page: 1,
    pageSize: 20,
  })
  assert.equal(nextComplianceDraftVersion(parsed), 2)
  assert.equal(nextComplianceDraftVersion(null), 1)
})

test("规则目录拒绝总数、页长度、最新版本和当前版本漂移", () => {
  const listedCurrent = numberedVersion(25)
  const invalidPages = [
    { current: version, versions: [version] },
    laterDirectoryPage({ count: 4 }),
    laterDirectoryPage({ count: 39 }),
    laterDirectoryPage({ versions: [] }),
    laterDirectoryPage({ versions: [...laterDirectoryPage().versions as ComplianceRuleVersion[], numberedVersion(25)] }),
    laterDirectoryPage({ latestVersion: 24 }),
    laterDirectoryPage({ current: numberedVersion(46) }),
    laterDirectoryPage({
      current: numberedVersion(20),
      versions: laterDirectoryPage().versions.map(item => item.version === 20 ? numberedVersion(5) : item),
    }),
    laterDirectoryPage({ current: { ...listedCurrent, status: "DRAFT", publishedAt: null }, versions: [listedCurrent] }),
    laterDirectoryPage({ current: listedCurrent, versions: [{ ...listedCurrent, rulesSha256: "f".repeat(64) }] }),
    laterDirectoryPage({ unexpected: true }),
  ]
  for (const value of invalidPages) {
    assert.throws(() => parseComplianceRuleDirectory(value, directoryContext), /合规规则目录|摘要与规则内容/)
  }
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage(), { ...directoryContext, page: 1 }), /页|版本/)
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage(), { ...directoryContext, page: 3 }), /页|版本/)
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage(), { ...directoryContext, pageSize: 0 }), /分页上下文/)
})

test("规则目录分页链接只接受同源、同端点和相邻页", () => {
  const invalidLinks = [
    "https://example.com/arbitrary",
    directoryLink(3, 20, "https://example.com"),
    "https://admin.esports.club/api/admin/platform/store-management/2/compliance-rules?page=3&page_size=20",
    directoryLink(4),
    directoryLink(3, 50),
    `${directoryLink(3)}&store=shenglang`,
    `${directoryLink(3)}&field_name=STORE_NAME`,
    `${directoryLink(3)}&page=4`,
    `${directoryLink(3)}#versions`,
    "https://user:pass@admin.esports.club/api/admin/platform/compliance-rules?page=3&page_size=20",
    "javascript:alert(1)",
  ]
  for (const next of invalidLinks) {
    assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage({ next }), directoryContext), /分页链接/)
  }
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage({ next: null }), directoryContext), /分页链接/)
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage({ previous: null }), directoryContext), /分页链接/)
  assert.throws(() => parseComplianceRuleDirectory(laterDirectoryPage({ previous: directoryLink(3) }), directoryContext), /分页链接/)
})

test("规则目录首页和末页严格校验相邻链接边界", () => {
  const firstVersions = Array.from({ length: 20 }, (_, index) => numberedVersion(45 - index))
  const first = parseComplianceRuleDirectory({
    current: numberedVersion(1), versions: firstVersions, count: 45, latestVersion: 45,
    next: directoryLink(2), previous: null,
  }, { ...directoryContext, page: 1 })
  assert.equal(first.previous, null)
  assert.equal(first.next, directoryLink(2))
  assert.throws(() => parseComplianceRuleDirectory({
    current: numberedVersion(1), versions: firstVersions, count: 45, latestVersion: 45,
    next: directoryLink(2), previous: directoryLink(0),
  }, { ...directoryContext, page: 1 }), /分页链接/)

  const last = parseComplianceRuleDirectory({
    current: numberedVersion(1), versions: Array.from({ length: 5 }, (_, index) => numberedVersion(5 - index)),
    count: 45, latestVersion: 45, next: null, previous: directoryLink(2),
  }, { ...directoryContext, page: 3 })
  assert.equal(last.next, null)
  assert.equal(last.previous, directoryLink(2))
  assert.throws(() => parseComplianceRuleDirectory({
    current: numberedVersion(1), versions: Array.from({ length: 5 }, (_, index) => numberedVersion(5 - index)),
    count: 45, latestVersion: 45, next: directoryLink(4), previous: directoryLink(2),
  }, { ...directoryContext, page: 3 }), /分页链接/)
})

test("规则分页组件显示真实总数和页码，并按边界、加载和失败状态禁用按钮", async () => {
  const { createServer } = await import("vite")
  const { fileURLToPath } = await import("node:url")
  const { createElement } = await import("react")
  const { renderToStaticMarkup } = await import("react-dom/server")
  const root = fileURLToPath(new URL("../../", import.meta.url))
  const server = await createServer({
    root,
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true },
    appType: "custom",
    resolve: { alias: { "@": `${root}/src` } },
  })
  try {
    const { ComplianceRulePagination } = await server.ssrLoadModule("/src/components/PlatformCompliance.tsx")
    const render = (directory: ReturnType<typeof parseComplianceRuleDirectory>, loading = false, error = "") =>
      renderToStaticMarkup(createElement(ComplianceRulePagination, { directory, loading, error, onChange() {} }))
    const button = (html: string, label: string) => {
      const match = html.match(new RegExp(`<button([^>]*)>${label}</button>`))
      assert.ok(match, `missing ${label} button`)
      return match[1]
    }
    const disabledAttribute = /(?:^|\s)disabled(?:=|\s|$)/
    const middle = render(parseComplianceRuleDirectory(laterDirectoryPage(), directoryContext))
    assert.match(middle.replace(/<[^>]*>/g, ""), /共 45 个版本 · 第 2 \/ 3 页/)
    assert.doesNotMatch(button(middle, "上一页"), disabledAttribute)
    assert.doesNotMatch(button(middle, "下一页"), disabledAttribute)

    const first = render(parseComplianceRuleDirectory({
      current: numberedVersion(1), versions: Array.from({ length: 20 }, (_, index) => numberedVersion(45 - index)),
      count: 45, latestVersion: 45, next: directoryLink(2), previous: null,
    }, { ...directoryContext, page: 1 }))
    assert.match(button(first, "上一页"), disabledAttribute)
    assert.doesNotMatch(button(first, "下一页"), disabledAttribute)

    const last = render(parseComplianceRuleDirectory({
      current: numberedVersion(1), versions: Array.from({ length: 5 }, (_, index) => numberedVersion(5 - index)),
      count: 45, latestVersion: 45, next: null, previous: directoryLink(2),
    }, { ...directoryContext, page: 3 }))
    assert.doesNotMatch(button(last, "上一页"), disabledAttribute)
    assert.match(button(last, "下一页"), disabledAttribute)

    const empty = render(parseComplianceRuleDirectory({
      current: version, versions: [], count: 0, latestVersion: 1, next: null, previous: null,
    }, { ...directoryContext, page: 1 }))
    assert.match(empty.replace(/<[^>]*>/g, ""), /共 0 个版本 · 第 1 \/ 1 页/)
    assert.match(button(empty, "上一页"), disabledAttribute)
    assert.match(button(empty, "下一页"), disabledAttribute)
    assert.match(button(render(parseComplianceRuleDirectory(laterDirectoryPage(), directoryContext), true), "下一页"), disabledAttribute)
    assert.match(button(render(parseComplianceRuleDirectory(laterDirectoryPage(), directoryContext), false, "读取失败"), "上一页"), disabledAttribute)
  } finally {
    await server.close()
  }
})

test("空草稿规则数和内置版本空创建时间可解析，数量漂移被拒绝", () => {
  const draft = { ...version, status: "DRAFT" as const, rules: [], ruleCount: 0, rulesSha256: complianceRulesSha256([]), createdAt: null, publishedAt: null }
  assert.deepEqual(parseComplianceRuleVersion(draft), draft)
  assert.throws(() => parseComplianceRuleVersion({ ...draft, ruleCount: 1 }), /数量或状态/)
  assert.throws(() => parseComplianceRuleVersion({ ...draft, created_by: "unexpected" }), /结构待核对/)
  assert.throws(() => parseComplianceRuleVersion({ ...draft, rulesSha256: "f".repeat(64) }), /摘要与规则内容不一致/)
  for (const createdAt of ["2026-11-25", "yesterday", "2026-02-30T00:00:00Z", "2026-11-25T24:00:00Z"]) {
    assert.throws(() => parseComplianceRuleVersion({ ...draft, createdAt }), /结构待核对/)
  }
})

test("回执列表验证门店归属、摘要并规范化操作者编号", () => {
  const parsed = parseComplianceReceiptPage({ count: 1, next: null, previous: null, results: [receipt] }, receiptContext)
  assert.equal(parsed.results[0].storeId, 2)
  assert.equal(parsed.results[0].actorId, "42")
  assert.deepEqual(parsed.results[0].matchedRuleCodes, ["official_name"])
  assert.throws(() => parseComplianceReceiptPage({
    count: 1, next: null, previous: null, results: [{ ...receipt, contentSha256: "" }],
  }, receiptContext), /结构待核对/)
  assert.throws(() => parseComplianceReceiptPage({
    count: 1, next: null, previous: null, results: [{ ...receipt, storeId: undefined }],
  }, receiptContext), /结构待核对/)
  assert.throws(() => parseComplianceReceiptPage({
    count: 0, next: null, previous: null, results: [receipt],
  }, receiptContext), /总数待核对/)
  assert.throws(() => parseComplianceReceiptPage({
    count: -1, next: null, previous: null, results: [],
  }, receiptContext), /结构待核对/)
})

test("草稿和发布回执必须与请求的版本、规则、摘要和数量一致", () => {
  const draft: ComplianceRuleVersion = {
    ...version,
    version: 2,
    status: "DRAFT",
    createdAt: "2026-11-25T00:00:00Z",
    publishedAt: null,
  }
  assert.deepEqual(parseComplianceDraftResult(draft, { version: 2, rules: [rule] }), draft)
  assert.throws(() => parseComplianceDraftResult(draft, { version: 3, rules: [rule] }), /响应与请求不一致/)
  assert.throws(() => parseComplianceDraftResult({ ...draft, ruleCount: 2 }, { version: 2, rules: [rule] }), /数量或状态/)
  const changed = { ...rule, message: "另一条提示" }
  const changedDraft = { ...draft, rules: [changed], rulesSha256: complianceRulesSha256([changed]) }
  assert.throws(() => parseComplianceDraftResult(changedDraft, { version: 2, rules: [rule] }), /响应与请求不一致/)

  const published: ComplianceRuleVersion = {
    ...draft,
    status: "PUBLISHED",
    publishedAt: "2026-11-25T00:01:00Z",
  }
  assert.deepEqual(parseCompliancePublishResult(published, draft), published)
  assert.throws(() => parseCompliancePublishResult({ ...published, version: 3 }, draft), /响应与请求不一致/)
  assert.throws(() => parseCompliancePublishResult({ ...published, publishedAt: null }, draft), /响应与请求不一致/)
  assert.throws(() => parseCompliancePublishResult(changedDraft, draft), /响应与请求不一致/)
})

test("403 解除未知结果锁并进入拒权状态，其他失败保持原判定", () => {
  assert.deepEqual(complianceWriteFailure(new PlatformRequestError("denied", 403)), { denied: true, uncertain: false })
  assert.deepEqual(complianceWriteFailure(new PlatformRequestError("invalid", 400)), { denied: false, uncertain: false })
  assert.deepEqual(complianceWriteFailure(new PlatformRequestError("busy", 503)), { denied: false, uncertain: true })
  assert.deepEqual(complianceWriteFailure(new PlatformNetworkError("offline")), { denied: false, uncertain: true })
  assert.deepEqual(complianceWriteFailure(new Error("contract mismatch")), { denied: false, uncertain: true })
})

test("回执拒绝非 ISO 时间、重复编号和与当前筛选不一致的字段", () => {
  for (const createdAt of ["2026-11-25", "yesterday", "2026-02-30T00:00:00Z", "2026-11-25T00:02:00"]) {
    assert.throws(() => parseComplianceReceiptPage({
      count: 1, next: null, previous: null, results: [{ ...receipt, createdAt }],
    }, receiptContext), /结构待核对/)
  }
  assert.throws(() => parseComplianceReceiptPage({
    count: 2, next: null, previous: null, results: [receipt, receipt],
  }, receiptContext), /编号重复/)
  assert.throws(() => parseComplianceReceiptPage({
    count: 1, next: null, previous: null, results: [{ ...receipt, fieldName: "STORE_NAME" }],
  }, receiptContext), /字段与当前筛选不一致/)
  assert.throws(() => parseComplianceReceiptPage({
    count: 1, next: null, previous: null, results: [{ ...receipt, storeId: 3 }],
  }, receiptContext), /门店归属/)
})

test("回执分页链接只接受同门店、同字段、同 page_size 的相邻页", () => {
  const first = parseComplianceReceiptPage({
    count: 41, next: receiptLink(2), previous: null, results: [receipt],
  }, receiptContext)
  assert.equal(first.next, receiptLink(2))
  const secondContext = { ...receiptContext, page: 2 }
  const second = parseComplianceReceiptPage({
    count: 41, next: receiptLink(3), previous: receiptLink(1), results: [receipt],
  }, secondContext)
  assert.equal(second.previous, receiptLink(1))
  const allFields = parseComplianceReceiptPage({
    count: 41, next: receiptLink(2, ""), previous: null, results: [receipt],
  }, { ...receiptContext, fieldName: "" })
  assert.equal(allFields.next, receiptLink(2, ""))

  const invalidLinks = [
    "https://example.com/arbitrary",
    `https://example.com/api/admin/platform/store-management/2/content-compliance-receipts?${new URLSearchParams({ page: "2", page_size: "20", field_name: "PROVIDER_DISPLAY_NAME" })}`,
    receiptLink(2, "PROVIDER_DISPLAY_NAME", 20, 3),
    receiptLink(2, "STORE_NAME"),
    receiptLink(2, ""),
    receiptLink(2, "PROVIDER_DISPLAY_NAME", 50),
    receiptLink(3),
    `${receiptLink(2)}&store=shenglang`,
    `${receiptLink(2)}&page=3`,
    "javascript:alert(1)",
  ]
  for (const next of invalidLinks) {
    assert.throws(() => parseComplianceReceiptPage({
      count: 41, next, previous: null, results: [receipt],
    }, receiptContext), /分页链接/)
  }
  assert.throws(() => parseComplianceReceiptPage({
    count: 41, next: null, previous: receiptLink(0), results: [receipt],
  }, receiptContext), /分页链接/)
})
