/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import { ApiError } from "@xa/shared"
import {
  createManualPushIntent,
  findManualPushRecovery,
  isManualPushRequestConflict,
  manualPushCampaignMatchesIntent,
  manualPushReadMode,
  manualPushSelector,
  manualPushWriteFailure,
  manualPushWriteMessage,
  parseManualPushCampaignPage,
  parseManualPushIntent,
  parseManualPushResult,
  parseManualPushRetryResult,
  parseManualPushStatusPage,
  submitManualPushIntent,
  updateManualRetrySelection,
  type ManualPushAudience,
  type ManualPushCampaignFilter,
  type ManualPushIntent,
  type ManualPushRecipientFilter,
} from "./manual-push.ts"

const context = { storeSlug: "shenglang", creatorId: 42 }
const createdAt = "2026-11-25T10:00:00+08:00"
const nextAttemptAt = "2026-11-25T10:05:00+08:00"

function intent(audience: ManualPushAudience = "CUSTOM"): ManualPushIntent {
  return createManualPushIntent(context, {
    audience,
    recipientAccountIds: audience === "CUSTOM" ? [9, 3] : [999],
    type: "SYSTEM",
    title: "  恢复核对  ",
    preview: "  摘要  ",
    detail: "完整正文\n保留换行",
  }, "manual-push-fixed")
}
function campaign(overrides: Record<string, unknown> = {}) {
  const value = intent()
  return {
    campaign_id: 77,
    client_request_id: value.payload.client_request_id,
    creator_id: context.creatorId,
    created_at: createdAt,
    title: value.payload.title,
    message_type: value.payload.type,
    selector: manualPushSelector(value.payload),
    total: 5,
    count: 2,
    pending_count: 2,
    failed_count: 1,
    skipped_count: 1,
    ...overrides,
  }
}
function campaignPage(overrides: Record<string, unknown> = {}) {
  return { list: [campaign()], total: 1, page: 1, page_size: 10, ...overrides }
}
const recipients = [
  { id: 1, account_id: 11, account_no: "XA00000000011", status: "CREATED", attempts: 1, last_error: "", message_id: 101, next_attempt_at: null },
  { id: 2, account_id: 12, account_no: "XA00000000012", status: "CREATED", attempts: 1, last_error: "", message_id: 102, next_attempt_at: null },
  { id: 3, account_id: 13, account_no: "XA00000000013", status: "PENDING", attempts: 1, last_error: "DatabaseError", message_id: null, next_attempt_at: nextAttemptAt },
  { id: 4, account_id: 14, account_no: "XA00000000014", status: "PENDING", attempts: 0, last_error: "", message_id: null, next_attempt_at: nextAttemptAt },
  { id: 5, account_id: 15, account_no: "XA00000000015", status: "SKIPPED", attempts: 0, last_error: "账号停用或门店身份已变化", message_id: null, next_attempt_at: null },
]
function statusPage(status: "" | ManualPushRecipientFilter = "", overrides: Record<string, unknown> = {}) {
  const list = status === "FAILED" ? recipients.filter(row => row.status === "PENDING" && row.last_error)
    : status ? recipients.filter(row => row.status === status) : recipients
  return {
    list,
    total: list.length,
    page: 1,
    page_size: 10,
    campaign: { ...campaign(), preview: intent().payload.preview, detail: intent().payload.detail },
    ...overrides,
  }
}
function pageContext(status: "" | ManualPushCampaignFilter = "") {
  return { page: 1, pageSize: 10, status, creatorId: context.creatorId, clientRequestId: intent().payload.client_request_id }
}

test("首次发送前保存完整请求，五种接收范围与后端 selector 一致", async () => {
  const value = intent()
  assert.equal(value.payload.title, "恢复核对")
  assert.equal(value.payload.preview, "摘要")
  assert.equal(value.payload.detail, "完整正文\n保留换行")
  assert.deepEqual(value.payload.recipient_account_ids, [3, 9])
  assert.deepEqual(manualPushSelector(value.payload), { accounts: [3, 9], users: [] })
  assert.deepEqual(manualPushSelector(intent("BOSS").payload), { audience: "BOSS" })
  assert.deepEqual(manualPushSelector(intent("PROVIDER").payload), { audience: "PROVIDER" })
  assert.deepEqual(manualPushSelector(intent("STAFF").payload), { audience: "STAFF" })
  assert.deepEqual(manualPushSelector(intent("ALL").payload), { broadcast: true })
  assert.equal(intent("STAFF").payload.broadcast, false)
  assert.equal(intent("ALL").payload.broadcast, true)
  assert.deepEqual(intent("BOSS").payload.recipient_account_ids, [])

  const events: string[] = []
  let sent = false
  await submitManualPushIntent(value, {
    save(saved) {
      events.push("save")
      assert.deepEqual(parseManualPushIntent(JSON.stringify(saved), context), value)
    },
    async send(saved) {
      events.push("send")
      sent = true
      return parseManualPushResult({ ...campaign(), request_replayed: false }, saved)
    },
    clear() { events.push("clear") },
  })
  assert.equal(sent, true)
  assert.deepEqual(events, ["save", "send", "clear"])
})

test("请求恢复记录严格绑定门店、操作人和规范字段，存储失败时不发送", async () => {
  const value = intent()
  const raw = JSON.stringify(value)
  assert.deepEqual(parseManualPushIntent(raw, context), value)
  assert.equal(parseManualPushIntent(null, context), null)
  assert.throws(() => parseManualPushIntent(raw, { ...context, storeSlug: "branch-b" }), /已阻止新发送/)
  assert.throws(() => parseManualPushIntent(raw, { ...context, creatorId: 43 }), /已阻止新发送/)
  assert.throws(() => parseManualPushIntent(JSON.stringify({ ...value, payload: { ...value.payload, extra: true } }), context), /已阻止新发送/)
  assert.throws(() => parseManualPushIntent(JSON.stringify({ ...value, payload: { ...value.payload, recipient_account_ids: [9, 3] } }), context), /已阻止新发送/)
  assert.throws(() => parseManualPushIntent("broken", context), /已阻止新发送/)

  let sent = false
  await assert.rejects(submitManualPushIntent(value, {
    save() { throw new Error("存储不可用") },
    async send() { sent = true; return parseManualPushResult({ ...campaign(), request_replayed: false }, value) },
    clear() {},
  }), /存储不可用/)
  assert.equal(sent, false)
})

test("批次统计、筛选和分页严格解析，失败是 pending 子集且不能重复累加", () => {
  const parsed = parseManualPushCampaignPage(campaignPage(), pageContext("FAILED"))
  assert.deepEqual([parsed.list[0].total, parsed.list[0].count, parsed.list[0].pending_count, parsed.list[0].failed_count, parsed.list[0].skipped_count], [5, 2, 2, 1, 1])
  assert.equal(parsed.list[0].count + parsed.list[0].pending_count + parsed.list[0].skipped_count, 5)
  assert.equal(parsed.list[0].failed_count <= parsed.list[0].pending_count, true)
  for (const changed of [
    campaignPage({ list: [{ ...campaign(), total: 6 }] }),
    campaignPage({ list: [{ ...campaign(), failed_count: 3 }] }),
    campaignPage({ list: [{ ...campaign(), count: undefined }] }),
    campaignPage({ list: [{ ...campaign(), pendingCount: 2 }] }),
    campaignPage({ page: 2 }),
    campaignPage({ total: 20 }),
    campaignPage({ list: [{ ...campaign(), creator_id: 43 }] }),
    campaignPage({ list: [{ ...campaign(), client_request_id: "other" }] }),
  ]) assert.throws(() => parseManualPushCampaignPage(changed, pageContext()), /群发/)

  const completed = campaign({ total: 3, count: 2, pending_count: 0, failed_count: 0, skipped_count: 1 })
  assert.equal(parseManualPushCampaignPage(campaignPage({ list: [completed] }), pageContext("COMPLETED")).list[0].pending_count, 0)
  assert.throws(() => parseManualPushCampaignPage(campaignPage(), pageContext("COMPLETED")), /待处理/)
})

test("成功空批次保留真实零统计，缺失统计仍视为未知而非补零", () => {
  const empty = parseManualPushCampaignPage({ list: [], total: 0, page: 1, page_size: 10 }, pageContext())
  assert.deepEqual(empty, { list: [], total: 0, page: 1, page_size: 10 })
  const emptyCampaign = campaign({ campaign_id: 78, total: 0, count: 0, pending_count: 0, failed_count: 0, skipped_count: 0 })
  const page = parseManualPushStatusPage(statusPage("", {
    list: [], total: 0,
    campaign: { ...emptyCampaign, preview: "摘要", detail: "完整正文\n保留换行" },
  }), { campaignId: 78, page: 1, pageSize: 10, status: "" })
  assert.equal(page.campaign.total, 0)
  assert.deepEqual(page.list, [])
  assert.throws(() => parseManualPushCampaignPage(campaignPage({ list: [{ ...campaign(), skipped_count: undefined }] }), pageContext()), /统计/)
})

test("逐人状态、全局批次统计、失败筛选和版本身份一致", () => {
  const parsed = parseManualPushStatusPage(statusPage(), { campaignId: 77, page: 1, pageSize: 10, status: "" })
  assert.equal(parsed.campaign.total, 5)
  assert.equal(parsed.total, 5)
  assert.equal(parsed.list[2].status, "PENDING")
  assert.equal(parsed.list[2].last_error, "DatabaseError")
  const failed = parseManualPushStatusPage(statusPage("FAILED"), { campaignId: 77, page: 1, pageSize: 10, status: "FAILED" })
  assert.deepEqual(failed.list.map(row => row.id), [3])
  assert.equal(failed.campaign.pending_count, 2)
  assert.equal(failed.total, 1)
  for (const [value, filter] of [
    [statusPage("", { campaign: { ...statusPage().campaign, campaign_id: 78 } }), ""],
    [statusPage("", { list: [{ ...recipients[2], message_id: 999 }, recipients[0], recipients[1], recipients[3], recipients[4]] }), ""],
    [statusPage("", { list: [{ ...recipients[0], next_attempt_at: nextAttemptAt }, ...recipients.slice(1)] }), ""],
    [statusPage("FAILED", { list: [recipients[3]], total: 1 }), "FAILED"],
    [statusPage("", { page_size: 20 }), ""],
  ] as const) assert.throws(() => parseManualPushStatusPage(value, { campaignId: 77, page: 1, pageSize: 10, status: filter }), /逐人|状态|分页|失败/)
})

test("发送回执必须匹配原请求号、操作人、标题、类型和完整 selector", () => {
  const value = intent()
  const result = parseManualPushResult({ ...campaign(), request_replayed: true }, value)
  assert.equal(result.request_replayed, true)
  assert.equal(manualPushCampaignMatchesIntent(result, value), true)
  for (const changed of [
    { ...campaign(), client_request_id: "other", request_replayed: false },
    { ...campaign(), creator_id: 43, request_replayed: false },
    { ...campaign(), title: "其他标题", request_replayed: false },
    { ...campaign(), message_type: "SUPPORT", request_replayed: false },
    { ...campaign(), selector: { accounts: [3], users: [] }, request_replayed: false },
    { ...campaign(), request_replayed: "false" },
  ]) assert.throws(() => parseManualPushResult(changed, value), /回执/)
})

test("网络与 5xx 保留原请求；首次 403 解除本地锁，恢复中 403 不解锁", async () => {
  for (const failure of [new ApiError("网络中断", 0), new ApiError("Worker unavailable", 503)]) {
    let saved: string | null = null
    await assert.rejects(submitManualPushIntent(intent(), {
      save(value) { saved = JSON.stringify(value) },
      async send() { throw failure },
      clear() { saved = null },
    }))
    assert.deepEqual(parseManualPushIntent(saved, context), intent())
  }
  assert.deepEqual(manualPushWriteFailure(new ApiError("denied", 403), false), { kind: "denied", clear: true })
  assert.deepEqual(manualPushWriteFailure(new ApiError("denied", 403), true), { kind: "denied", clear: false })
  let cleared = false
  await assert.rejects(submitManualPushIntent(intent(), {
    save() {}, clear() { cleared = true },
    async send() { throw new ApiError("denied", 403) },
  }))
  assert.equal(cleared, true)
  let recoveredSaved: string | null = null
  await assert.rejects(submitManualPushIntent(intent(), {
    save(value) { recoveredSaved = JSON.stringify(value) }, clear() { recoveredSaved = null },
    async send() { throw new ApiError("denied", 403) },
  }, { recovering: true }))
  assert.deepEqual(parseManualPushIntent(recoveredSaved, context), intent())
})

test("未知结果先按原号查批次，找到完整匹配后不再次 POST", async () => {
  const sent: ManualPushIntent[] = []
  let saved: string | null = null
  const storage = { save(value: ManualPushIntent) { saved = JSON.stringify(value) }, clear() { saved = null } }
  await assert.rejects(submitManualPushIntent(intent(), {
    ...storage,
    async send(value) { sent.push(value); throw new ApiError("网络中断", 0) },
  }))
  const recovered = parseManualPushIntent(saved, context)!
  const found = await findManualPushRecovery(recovered, {
    async lookup(value) {
      assert.deepEqual(value, intent())
      return parseManualPushCampaignPage(campaignPage(), pageContext())
    },
    async detail(campaignId) {
      assert.equal(campaignId, 77)
      return parseManualPushStatusPage(statusPage(), { campaignId: 77, page: 1, pageSize: 10, status: "" })
    },
  })
  assert.equal(found.status, "FOUND")
  if (found.status === "FOUND") assert.equal(manualPushCampaignMatchesIntent(found.campaign, recovered, true), true)
  assert.equal(sent.length, 1)
  assert.deepEqual(parseManualPushIntent(saved, context), intent())
})

test("查不到原批次后才允许同号同内容重试，不能换号或改变正文", async () => {
  const value = intent()
  const missing = await findManualPushRecovery(value, {
    async lookup() { return parseManualPushCampaignPage({ list: [], total: 0, page: 1, page_size: 10 }, pageContext()) },
    async detail() { throw new Error("不应读取详情") },
  })
  assert.equal(missing.status, "NOT_FOUND")
  const sent: ManualPushIntent[] = []
  let saved: string | null = JSON.stringify(value)
  await submitManualPushIntent(parseManualPushIntent(saved, context)!, {
    save(current) { saved = JSON.stringify(current) },
    clear() { saved = null },
    async send(current) {
      sent.push(current)
      assert.deepEqual(current, value)
      return parseManualPushResult({ ...campaign(), request_replayed: true }, current)
    },
  }, { recovering: true })
  assert.equal(saved, null)
  assert.deepEqual(sent[0], value)
  assert.equal(sent[0].payload.client_request_id, "manual-push-fixed")
})

test("409 同号异内容明确拒绝覆盖，且不自动生成替代请求", async () => {
  const conflict = new ApiError("请求编号已用于其他消息，请使用新的请求编号", 409, {
    code: 409,
    msg: "请求编号已用于其他消息，请使用新的请求编号",
    errors: { detail: "请求编号已用于其他消息，请使用新的请求编号" },
  })
  assert.equal(isManualPushRequestConflict(conflict), true)
  assert.deepEqual(manualPushWriteFailure(conflict), { kind: "conflict", clear: true })
  assert.match(manualPushWriteMessage(conflict), /未覆盖原批次/)
  let calls = 0
  let cleared = false
  await assert.rejects(submitManualPushIntent(intent(), {
    save() {}, clear() { cleared = true },
    async send() { calls++; throw conflict },
  }))
  assert.equal(calls, 1)
  assert.equal(cleared, true)

  await assert.rejects(findManualPushRecovery(intent(), {
    async lookup() { return parseManualPushCampaignPage(campaignPage(), pageContext()) },
    async detail() { return parseManualPushStatusPage(statusPage("", { campaign: { ...statusPage().campaign, detail: "其他正文" } }), { campaignId: 77, page: 1, pageSize: 10, status: "" }) },
  }), /内容或接收范围不同/)
})

test("失败重试沿用原 campaign，选择最多 200 条并验证回执身份", () => {
  const first = Array.from({ length: 100 }, (_, index) => index + 1)
  const second = Array.from({ length: 100 }, (_, index) => index + 101)
  const selected = updateManualRetrySelection(updateManualRetrySelection([], first, true), second, true)
  assert.equal(selected.length, 200)
  assert.throws(() => updateManualRetrySelection(selected, [201], true), /最多选择 200/)
  assert.deepEqual(updateManualRetrySelection(selected, [1, 2], false).length, 198)
  assert.throws(() => updateManualRetrySelection([1, 1], [2], true), /无效或重复/)
  const parsed = parseManualPushRetryResult(campaign(), { campaignId: 77, clientRequestId: "manual-push-fixed" })
  assert.equal(parsed.campaign_id, 77)
  assert.throws(() => parseManualPushRetryResult(campaign({ campaign_id: 78 }), { campaignId: 77, clientRequestId: "manual-push-fixed" }), /原群发批次/)
})

test("页面读取状态覆盖加载、首次失败、成功空、刷新过期和有数据", () => {
  assert.equal(manualPushReadMode({ hasSnapshot: false, loading: true, error: "" }), "loading")
  assert.equal(manualPushReadMode({ hasSnapshot: false, loading: false, error: "读取失败" }), "error")
  assert.equal(manualPushReadMode({ hasSnapshot: true, loading: false, error: "", itemCount: 0 }), "empty")
  assert.equal(manualPushReadMode({ hasSnapshot: true, loading: false, error: "刷新失败", itemCount: 2 }), "stale")
  assert.equal(manualPushReadMode({ hasSnapshot: true, loading: false, error: "", itemCount: 2 }), "ready")
})
