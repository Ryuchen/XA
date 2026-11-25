/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import { ApiError } from "@xa/shared"
import {
  createH5WriteIntent,
  h5PublicUrl,
  h5WriteFailure,
  h5WriteMessage,
  parseH5MutationResult,
  parseH5PageDirectory,
  parseH5Preview,
  parseH5ReceiptList,
  parseH5VersionDetail,
  parseH5VersionList,
  parseH5WriteIntent,
  submitH5Write,
  type H5MutationResult,
  type H5PageKind,
  type H5Snapshot,
  type H5WriteIntent,
} from "./h5-pages.ts"

const token = "123e4567-e89b-42d3-a456-426614174000"
const secondToken = "223e4567-e89b-42d3-a456-426614174000"
const sha = "a".repeat(64)
const otherSha = "b".repeat(64)
const identity = { storeSlug: "shenglang", sessionId: "login-a" }
const generatedAt = "2026-11-25T10:00:00+08:00"

function summary(kind: H5PageKind, overrides: Record<string, unknown> = {}) {
  const labels = { PRICE_LIST: "价目表", ORDER_NOTICE: "下单须知", INTRODUCTION: "门店介绍" }
  return {
    kind, label: labels[kind], revision: 0, published: false,
    publicToken: null, publicPath: null, jsonPath: null, currentVersion: null,
    sourceStatus: "READY", sourceIssues: [], draftDigest: sha, ...overrides,
  }
}
function publishedSummary() {
  return summary("INTRODUCTION", {
    revision: 1, published: true, publicToken: token,
    publicPath: `/h5/pages/${token}/`, jsonPath: `/api/club/h5-pages/${token}/`, currentVersion: 1,
  })
}
function directory() {
  return {
    total: 3,
    list: [
      summary("PRICE_LIST"),
      summary("ORDER_NOTICE", {
        revision: 1, publicToken: secondToken, publicPath: `/h5/pages/${secondToken}/`,
        jsonPath: `/api/club/h5-pages/${secondToken}/`, sourceStatus: "BLOCKED", sourceIssues: ["暂无须知"],
      }),
      publishedSummary(),
    ],
  }
}
function baseSnapshot(kind: H5PageKind) {
  return { schemaVersion: 1 as const, kind, title: "页面", store: { name: "声浪电竞", currencyName: "浪花" }, generatedAt }
}
function introduction(): H5Snapshot {
  return { ...baseSnapshot("INTRODUCTION"), kind: "INTRODUCTION", body: "认真陪玩，按时履约。" }
}
function notice(): H5Snapshot {
  return { ...baseSnapshot("ORDER_NOTICE"), kind: "ORDER_NOTICE", sections: [{ id: 9, title: "预约须知", content: "请提前十分钟上线。" }] }
}
function price(): H5Snapshot {
  return {
    ...baseSnapshot("PRICE_LIST"), kind: "PRICE_LIST", items: [{
      id: 7, name: "双陪上分", description: "两人一起陪你玩", categoryName: "陪玩", gameName: "王者荣耀",
      priceUnits: 2468, unitPriceUnits: 1234, priceText: "246.8 浪花", unitPriceText: "123.4 浪花",
      priceUnit: "局", billingProviderCount: 2, purchaseNote: "下单前确认档期",
      rules: "按约定时间开始", details: "服务结束由双方确认。",
    }],
  }
}
function preview(kind: H5PageKind, snapshot: H5Snapshot) {
  return { kind, publishable: true, issues: [], draftDigest: sha, snapshot }
}
function publishIntent(): H5WriteIntent {
  return createH5WriteIntent(identity, "INTRODUCTION", "publish", 0, "h5-publish-fixed")
}
function withdrawIntent(): H5WriteIntent {
  return createH5WriteIntent(identity, "INTRODUCTION", "withdraw", 1, "h5-withdraw-fixed", "内容需要更新")
}
function publishResult(overrides: Record<string, unknown> = {}) {
  return {
    kind: "INTRODUCTION", publicToken: token, publicPath: `/h5/pages/${token}/`,
    jsonPath: `/api/club/h5-pages/${token}/`, revision: 1, published: true,
    currentVersion: 1, version: 1, contentSha256: sha, publishedAt: generatedAt,
    complianceStatus: "CHECKED", copyrightConfirmed: true, replayed: false, ...overrides,
  }
}
function withdrawResult(overrides: Record<string, unknown> = {}) {
  return {
    kind: "INTRODUCTION", publicToken: token, publicPath: `/h5/pages/${token}/`,
    jsonPath: `/api/club/h5-pages/${token}/`, revision: 1, published: false,
    currentVersion: null, version: null, contentSha256: null, publishedAt: null,
    complianceStatus: null, copyrightConfirmed: null, replayed: false,
    withdrawnVersion: 1, withdrawnAt: generatedAt, ...overrides,
  }
}
function versionSummary(version = 1) {
  return { version, contentSha256: sha, complianceStatus: "CHECKED", complianceReceiptIds: [31], copyrightConfirmed: true, publishedAt: generatedAt }
}
function receipt(id = 31) {
  return {
    id, contentType: "h5_page", objectId: "18", fieldName: "STORE_INTRODUCTION",
    contentSha256: sha, ruleVersion: 3, ruleSetSha256: otherSha,
    matchedRuleCodes: [], exemptedRuleCodes: [], actorLabel: "门店管理员", createdAt: generatedAt,
  }
}

test("目录严格接受三类后端 camelCase 字段并核对公开身份", () => {
  const parsed = parseH5PageDirectory(directory())
  assert.equal(parsed.total, 3)
  assert.equal(parsed.list[2].currentVersion, 1)
  assert.equal(parsed.list[1].published, false)
  assert.equal(parsed.list[1].publicToken, secondToken)

  const snake = directory()
  snake.list[2] = { ...publishedSummary(), public_token: token } as ReturnType<typeof publishedSummary>
  assert.throws(() => parseH5PageDirectory(snake), /目录记录/)
  assert.throws(() => parseH5PageDirectory({ ...directory(), list: [...directory().list.slice(0, 2), publishedSummary(), publishedSummary()] }), /目录/)
  assert.throws(() => parseH5PageDirectory({
    ...directory(), list: [...directory().list.slice(0, 2), { ...publishedSummary(), currentVersion: 2 }],
  }), /当前版本/)
  assert.throws(() => parseH5PageDirectory({
    ...directory(), list: [...directory().list.slice(0, 2), { ...publishedSummary(), publicPath: "https://example.com/public" }],
  }), /公开身份/)
})

test("三类预览保持实际快照字段，拒绝 snake_case、错门店和错类型", () => {
  for (const [kind, snapshot] of [["INTRODUCTION", introduction()], ["ORDER_NOTICE", notice()], ["PRICE_LIST", price()]] as const) {
    const parsed = parseH5Preview(preview(kind, snapshot), { kind, storeName: "声浪电竞" })
    assert.equal(parsed.snapshot.store.name, "声浪电竞")
    assert.throws(() => parseH5Preview(preview(kind, { ...snapshot, store: { name: "分店电竞", currencyName: "星辉" } } as H5Snapshot), { kind, storeName: "声浪电竞" }), /门店/)
    const wrongKind = kind === "INTRODUCTION" ? "ORDER_NOTICE" : "INTRODUCTION"
    assert.throws(() => parseH5Preview(preview(kind, snapshot), { kind: wrongKind, storeName: "声浪电竞" }), /预览|快照/)
  }
  assert.throws(() => parseH5Preview({ ...preview("INTRODUCTION", introduction()), draft_digest: sha }, { kind: "INTRODUCTION", storeName: "声浪电竞" }), /预览响应/)
  const snakePrice = price()
  ;(snakePrice as any).items[0].price_units = 2468
  delete (snakePrice as any).items[0].priceUnits
  assert.throws(() => parseH5Preview(preview("PRICE_LIST", snakePrice), { kind: "PRICE_LIST", storeName: "声浪电竞" }), /价目表第 1 项/)
  assert.throws(() => parseH5Preview({ ...preview("INTRODUCTION", introduction()), publishable: false }, { kind: "INTRODUCTION", storeName: "声浪电竞" }), /预览响应/)
})

test("历史目录和只读详情绑定请求版本、门店及不可变快照", () => {
  const second = { ...versionSummary(2), contentSha256: otherSha }
  const parsed = parseH5VersionList({ list: [second, versionSummary(1)], total: 2 }, { latestRevision: 2 })
  assert.deepEqual(parsed.list.map(item => item.version), [2, 1])
  assert.throws(() => parseH5VersionList({ list: [versionSummary(1), second], total: 2 }, { latestRevision: 2 }), /顺序/)
  assert.throws(() => parseH5VersionList({ list: [{ ...versionSummary(1), content_sha256: sha }], total: 1 }, { latestRevision: 1 }), /结构/)
  assert.throws(() => parseH5VersionList({ list: [{ ...versionSummary(3) }], total: 1 }, { latestRevision: 2 }), /归属/)

  const detail = parseH5VersionDetail({ ...versionSummary(2), contentSha256: otherSha, snapshot: introduction() }, { kind: "INTRODUCTION", storeName: "声浪电竞", version: 2 })
  assert.equal(detail.snapshot.kind, "INTRODUCTION")
  assert.throws(() => parseH5VersionDetail({ ...versionSummary(1), snapshot: introduction() }, { kind: "INTRODUCTION", storeName: "声浪电竞", version: 2 }), /编号/)
  assert.throws(() => parseH5VersionDetail({ ...versionSummary(1), snapshot: notice() }, { kind: "INTRODUCTION", storeName: "声浪电竞", version: 1 }), /快照/)
})

test("合规回执必须属于所选版本的 receipt ids，且不接收额外身份字段", () => {
  const parsed = parseH5ReceiptList({ list: [receipt()], total: 1 }, { receiptIds: [31] })
  assert.equal(parsed.list[0].contentType, "h5_page")
  for (const value of [
    { list: [receipt(32)], total: 1 },
    { list: [], total: 0 },
    { list: [{ ...receipt(), field_name: "STORE_NAME" }], total: 1 },
    { list: [{ ...receipt(), version: 1 }], total: 1 },
    { list: [{ ...receipt(), contentType: "order" }], total: 1 },
  ]) assert.throws(() => parseH5ReceiptList(value, { receiptIds: [31] }), /回执/)
})

test("发布和撤回回执严格匹配 kind、expected revision 与公开路径", () => {
  const publish = publishIntent()
  const published = parseH5MutationResult(publishResult(), publish)
  assert.equal(published.kind, "INTRODUCTION")
  assert.equal(published.version, 1)
  for (const changed of [
    publishResult({ kind: "PRICE_LIST" }), publishResult({ version: 2 }),
    publishResult({ current_version: 1 }), publishResult({ published: false }),
    publishResult({ publicPath: "/h5/pages/other/" }),
  ]) assert.throws(() => parseH5MutationResult(changed, publish), /回执/)

  const withdraw = withdrawIntent()
  const withdrawn = parseH5MutationResult(withdrawResult(), withdraw)
  assert.equal(withdrawn.published, false)
  assert.equal(withdrawn.withdrawnVersion, 1)
  assert.throws(() => parseH5MutationResult(withdrawResult({ withdrawnVersion: 2 }), withdraw), /撤回回执/)
  assert.throws(() => parseH5MutationResult(withdrawResult({ published: true }), withdraw), /撤回回执/)
})

test("公开链接只使用当前门店 boss_url 源站", () => {
  const store = { slug: "shenglang", name: "声浪电竞", bossUrl: "https://sl.esports.club/boss/?campaign=qa" }
  assert.equal(h5PublicUrl(`/h5/pages/${token}/`, store), `https://sl.esports.club/h5/pages/${token}/`)
  assert.equal(h5PublicUrl(`/h5/pages/${token}/`, { ...store, bossUrl: "http://localhost:14101/boss/?store=shenglang" }), `http://localhost:14101/h5/pages/${token}/`)
  for (const [path, bossUrl] of [
    ["https://example.com/h5/pages/x/", store.bossUrl],
    [`/api/club/h5-pages/${token}/`, store.bossUrl],
    [`/h5/pages/${token}/?store=other`, store.bossUrl],
    [`/h5/pages/${token}/`, "javascript:alert(1)"],
    [`/h5/pages/${token}/`, "https://user:pass@sl.esports.club/boss/"],
  ]) assert.throws(() => h5PublicUrl(path, { ...store, bossUrl }), /源站|公开路径/)
})

test("请求写前持久化，网络未知后恢复完全相同的门店、版本、内容和请求号", async () => {
  let saved: string | null = null
  const sent: H5WriteIntent[] = []
  const storage = {
    save(value: H5WriteIntent) { saved = JSON.stringify(value) },
    clear() { saved = null },
  }
  const intent = publishIntent()
  await assert.rejects(submitH5Write(intent, {
    ...storage,
    async send(value) { sent.push(value); throw new ApiError("网络中断，操作结果尚未确认", 0) },
  }), /结果尚未确认/)
  const recovered = parseH5WriteIntent(saved, identity)
  assert.deepEqual(recovered, intent)

  for (const failure of [
    new ApiError("暂时无权限", 403, { code: 403, msg: "暂时无权限" }),
    new ApiError("规则不可用", 503, { code: 503, msg: "规则不可用", errorCode: "COMPLIANCE_RULES_UNAVAILABLE" }),
    new ApiError("请求较多", 429, { code: 429, msg: "请求较多" }),
  ]) {
    await assert.rejects(submitH5Write(parseH5WriteIntent(saved, identity)!, {
      ...storage,
      async send(value) { sent.push(value); throw failure },
    }, { recovering: true }), /权限|不可用|较多/)
    assert.deepEqual(parseH5WriteIntent(saved, identity), intent)
  }
  const result = await submitH5Write(parseH5WriteIntent(saved, identity)!, {
    ...storage,
    async send(value) { sent.push(value); return parseH5MutationResult(publishResult({ replayed: true }), value) },
  }, { recovering: true })
  assert.equal(saved, null)
  assert.equal(result.replayed, true)
  for (const value of sent) assert.deepEqual(value, intent)
})

test("409 版本冲突不会自动换号重试；首次 403 与恢复中 403 分别处理", async () => {
  for (const [recovering, code] of [[false, "H5_REVISION_CONFLICT"], [true, "H5_REVISION_CONFLICT"], [false, "H5_PAGE_NOT_PUBLISHED"]] as const) {
    let cleared = 0
    let calls = 0
    await assert.rejects(submitH5Write(code === "H5_PAGE_NOT_PUBLISHED" ? withdrawIntent() : publishIntent(), {
      save() {}, clear() { cleared++ },
      async send() { calls++; throw new ApiError("冲突", 409, { code: 409, msg: "冲突", errorCode: code }) },
    }, { recovering }), /冲突/)
    assert.equal(calls, 1)
    assert.equal(cleared, 1)
  }
  assert.deepEqual(h5WriteFailure(new ApiError("拒绝", 403), false), { kind: "denied", clear: true })
  assert.deepEqual(h5WriteFailure(new ApiError("拒绝", 403), true), { kind: "denied", clear: false })
  assert.deepEqual(h5WriteFailure(new ApiError("暂不可用", 503), false), { kind: "unknown", clear: false })
  assert.deepEqual(h5WriteFailure(new ApiError("回执不可用", 409, { errorCode: "H5_RESULT_UNAVAILABLE" }), true), { kind: "unknown", clear: false })
  assert.match(h5WriteMessage(new ApiError("离线", 0)), /同号恢复/)
})

test("错回执或不可解析恢复记录保持待核对，不生成替代发布", async () => {
  const intent = publishIntent()
  let saved: string | null = null
  await assert.rejects(submitH5Write(intent, {
    save(value) { saved = JSON.stringify(value) }, clear() { saved = null },
    async send() { return parseH5MutationResult(publishResult({ version: 2 }), intent) },
  }), /版本身份/)
  assert.deepEqual(parseH5WriteIntent(saved, identity), intent)
  assert.throws(() => parseH5WriteIntent(saved, { ...identity, storeSlug: "branch-b" }), /已阻止新写入/)
  assert.throws(() => parseH5WriteIntent("broken", identity), /已阻止新写入/)
  assert.equal(parseH5WriteIntent(null, identity), null)
})

test("存储失败时不发送，撤回必须保留原原因且版权确认只能显式为 true", async () => {
  let sent = false
  await assert.rejects(submitH5Write(publishIntent(), {
    save() { throw new Error("存储不可用") }, clear() {},
    async send() { sent = true; return publishResult() as H5MutationResult },
  }), /存储不可用/)
  assert.equal(sent, false)
  assert.throws(() => createH5WriteIntent(identity, "INTRODUCTION", "withdraw", 1, "fixed", "  "), /撤回原因/)
  const withdrawn = withdrawIntent()
  assert.equal(withdrawn.operation, "withdraw")
  assert.equal(withdrawn.payload.reason, "内容需要更新")
  const published = publishIntent()
  assert.equal(published.operation, "publish")
  assert.equal(published.payload.copyright_confirmed, true)
  assert.throws(() => parseH5WriteIntent(JSON.stringify({
    ...published, payload: { ...published.payload, copyright_confirmed: false },
  }), identity), /已阻止新写入/)
})
