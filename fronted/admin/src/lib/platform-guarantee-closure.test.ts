/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import {
  createClosureExpiryIntent,
  createClosureStartIntent,
  createContractIntent,
  createGuaranteeEventIntent,
  parseClosureExpiryResult,
  parseClosureStartResult,
  parseClosureSummary,
  parseContractResult,
  parseGuaranteeEventResult,
  parseGuaranteeSummary,
  parsePlatformWriteIntent,
  platformGuaranteeClosureAccess,
  platformSnapshotMode,
  platformWriteFailure,
  platformWriteMessage,
  recoverPlatformWrite,
  submitPlatformWrite,
  type ClosureSummary,
  type GuaranteeEvent,
  type GuaranteeSummary,
  type PlatformWriteIntent,
  type PlatformWriteResult,
} from "./platform-guarantee-closure.ts"
import { PlatformNetworkError, PlatformRequestError, type PlatformProfile } from "./platform-session.ts"

const context = { storeId: 2, storeSlug: "shenglang", actorId: "actor-1", sessionId: "session-1" }
const contractId = "123e4567-e89b-42d3-a456-426614174000"
const receiptId = "223e4567-e89b-42d3-a456-426614174000"
const refundId = "323e4567-e89b-42d3-a456-426614174000"
const noticeId = "423e4567-e89b-42d3-a456-426614174000"
const expiryId = "523e4567-e89b-42d3-a456-426614174000"
const actor = { id: "actor-1", username: "platform-finance", nickname: "平台财务" }
const sha = "a".repeat(64), otherSha = "b".repeat(64)
const startedAt = "2026-11-01T04:00:00+00:00"
const earliestAt = "2026-12-01T04:00:00+00:00"

function contract(overrides: Record<string, unknown> = {}) {
  return {
    id: contractId, store_slug: "shenglang", source_license_contract_id: null,
    amount_due_fen: 10000, version: 2, amount_received_fen: 6000, amount_refunded_fen: 1000,
    net_received_fen: 5000, amount_remaining_fen: 5000, status: "PARTIAL",
    updated_at: "2026-11-21T04:00:00+00:00", updated_by: actor, ...overrides,
  }
}
function eventIntent(amountFen = 6000) {
  return createGuaranteeEventIntent(context, {
    businessKey: "DEP-2026-0001", direction: "RECEIPT", amountFen,
    occurredAt: "2026-11-20T10:00:00+08:00", evidenceSha256: "A".repeat(64),
    evidenceRef: "offline:2026/11/receipt-0001", compensatesEventId: null,
  }, "event-request-1") as Extract<PlatformWriteIntent, { operation: "guarantee-event" }>
}
function event(overrides: Record<string, unknown> = {}): GuaranteeEvent {
  return {
    id: receiptId, business_key: "DEP-2026-0001", client_request_id: "event-request-1",
    contract_version: 1, direction: "RECEIPT", amount_fen: 6000,
    occurred_at: "2026-11-20T02:00:00+00:00", evidence_sha256: sha,
    evidence_ref: "offline:2026/11/receipt-0001", compensates_event_id: null,
    created_by: actor, created_at: "2026-11-20T03:00:00+00:00", channel_status: "NOT_REQUESTED",
    ...overrides,
  } as GuaranteeEvent
}
function refund(overrides: Record<string, unknown> = {}) {
  return event({
    id: refundId, business_key: "REF-2026-0001", client_request_id: "refund-request-1",
    contract_version: 2, direction: "REFUND", amount_fen: 1000, evidence_sha256: otherSha,
    evidence_ref: "offline:2026/11/refund-0001", occurred_at: "2026-11-21T02:00:00+00:00",
    created_at: "2026-11-21T03:00:00+00:00", ...overrides,
  })
}
function publicStatus(overrides: Record<string, unknown> = {}) {
  return { status: "PARTIAL", label: "保证金部分登记", currency: "CNY", amount_due_fen: 10000, net_received_fen: 5000, amount_remaining_fen: 5000, record_basis: "OFFLINE_EVIDENCE", ...overrides }
}
function guaranteeSummary(overrides: Record<string, unknown> = {}): GuaranteeSummary {
  return parseGuaranteeSummary({ contract: contract(), events: [event(), refund()], public_status: publicStatus(), ...overrides }, { storeSlug: "shenglang" })
}
function pendingSummary() {
  return parseGuaranteeSummary({
    contract: null, events: [],
    public_status: { status: "PENDING_REVIEW", label: "保障信息待核", currency: "CNY", amount_due_fen: null, net_received_fen: null, amount_remaining_fen: null, record_basis: "OFFLINE_EVIDENCE" },
  }, { storeSlug: "shenglang" })
}
function notice(overrides: Record<string, unknown> = {}) {
  return {
    id: noticeId, store_slug: "shenglang", status: "NOTICE_ACTIVE", reason_category: "OPERATOR_REQUEST",
    reason: "经营主体申请停止营业", applicant: actor, started_at: startedAt,
    notice_policy_version: "SA02_NOTICE_30D_V1", notice_days: 30, earliest_settlement_at: earliestAt,
    client_request_id: "closure-start-1", clock_source: "MYSQL_CONTROL", clock_identity: "mysql:default",
    created_at: startedAt, ...overrides,
  }
}
function expiry(overrides: Record<string, unknown> = {}) {
  return {
    id: expiryId, kind: "NOTICE_EXPIRED", reason: "控制库时钟确认公示已满 30 天", actor,
    occurred_at: earliestAt, client_request_id: "closure-expiry-1", clock_source: "MYSQL_CONTROL",
    clock_identity: "mysql:default", created_at: earliestAt, ...overrides,
  }
}
function closureSummary(status: "NOT_STARTED" | "NOTICE_ACTIVE" | "SETTLEMENT_PENDING" = "NOTICE_ACTIVE"): ClosureSummary {
  if (status === "NOT_STARTED") return parseClosureSummary({ status, notice: null, expiry_event: null, new_activity_allowed: true, store_status: "ACTIVE" }, { storeSlug: "shenglang" })
  const hasExpiry = status === "SETTLEMENT_PENDING"
  return parseClosureSummary({
    status, notice: notice({ status }), expiry_event: hasExpiry ? expiry() : null,
    new_activity_allowed: false, store_status: "ACTIVE",
  }, { storeSlug: "shenglang" })
}
function contractIntent() {
  return createContractIntent(context, { amountDueFen: 10000, expectedVersion: 1, reason: "平台合同额度核对", sourceLicenseContractId: null }, "contract-request-1") as Extract<PlatformWriteIntent, { operation: "contract" }>
}
function startIntent() {
  return createClosureStartIntent(context, { reasonCategory: "OPERATOR_REQUEST", reason: "经营主体申请停止营业" }, "closure-start-1") as Extract<PlatformWriteIntent, { operation: "closure-start" }>
}
function expiryIntent() {
  return createClosureExpiryIntent(context, "控制库时钟确认公示已满 30 天", "closure-expiry-1") as Extract<PlatformWriteIntent, { operation: "closure-expiry" }>
}
function profile(grants: PlatformProfile["grants"]): PlatformProfile {
  return { id: "actor-1", username: "platform", identity_type: "platform", grants, capabilities: ["STORE_MANAGEMENT_V1"] }
}
const unusedClosure = async () => { throw new Error("不应读取闭店") }
const unusedGuarantee = async () => { throw new Error("不应读取保证金") }

test("平台角色和门店 scope 分别控制查看、合同配置、收退登记和闭店管理", () => {
  const globalSuper = platformGuaranteeClosureAccess(profile([{ role: "SUPER_ADMIN", scope_slug: "*" }]), "shenglang")
  assert.deepEqual([globalSuper.guaranteeView, globalSuper.guaranteeConfigure, globalSuper.guaranteeRecord, globalSuper.closureView, globalSuper.closureManage], [true, true, true, true, true])
  const finance = platformGuaranteeClosureAccess(profile([{ role: "FINANCE", scope_slug: "shenglang" }]), "shenglang")
  assert.deepEqual([finance.guaranteeView, finance.guaranteeConfigure, finance.guaranteeRecord, finance.closureView, finance.closureManage], [true, false, true, true, false])
  const wrongStore = platformGuaranteeClosureAccess(profile([{ role: "FINANCE", scope_slug: "xinan" }]), "shenglang")
  assert.equal(wrongStore.guaranteeView, false)
  const viewer = platformGuaranteeClosureAccess(profile([{ role: "ORDER_VIEWER", scope_slug: "*" }]), "shenglang")
  assert.equal(viewer.closureView, false)
  const scopedSuper = platformGuaranteeClosureAccess(profile([{ role: "SUPER_ADMIN", scope_slug: "shenglang" }]), "shenglang")
  assert.equal(scopedSuper.closureManage, true)
  assert.notEqual(finance.scopeSignature, wrongStore.scopeSignature)
})

test("保证金摘要验证门店、合同版本、净额守恒和逐笔事件合计", () => {
  const summary = guaranteeSummary()
  assert.equal(summary.contract?.net_received_fen, 5000)
  assert.equal(summary.events[0].channel_status, "NOT_REQUESTED")
  assert.equal(summary.public_status.amount_remaining_fen, 5000)
  for (const value of [
    { contract: contract({ store_slug: "xinan" }), events: [event(), refund()], public_status: publicStatus() },
    { contract: contract({ amount_received_fen: 5000 }), events: [event(), refund()], public_status: publicStatus() },
    { contract: contract({ net_received_fen: 4000 }), events: [event(), refund()], public_status: publicStatus() },
    { contract: contract(), events: [event()], public_status: publicStatus() },
    { contract: contract(), events: [event(), event({ id: refundId })], public_status: publicStatus() },
  ]) assert.throws(() => parseGuaranteeSummary(value, { storeSlug: "shenglang" }), /保证金|合同/)
})

test("无合同与异常证据保持待核，null 金额不补零，NOT_REQUESTED 不可冒充渠道成功", () => {
  const pending = pendingSummary()
  assert.equal(pending.contract, null)
  assert.equal(pending.public_status.amount_due_fen, null)
  assert.notEqual(pending.public_status.amount_due_fen, 0)
  for (const value of [
    { contract: null, events: [], public_status: publicStatus({ status: "PENDING_REVIEW", amount_due_fen: 0 }) },
    { contract: contract(), events: [event({ evidence_sha256: "" }), refund()], public_status: publicStatus() },
    { contract: contract(), events: [event({ evidence_ref: "6222020200112233445" }), refund()], public_status: publicStatus() },
    { contract: contract(), events: [event({ channel_status: "PAID" }), refund()], public_status: publicStatus() },
    { contract: contract(), events: [event(), refund()], public_status: publicStatus({ status: "COVERED", net_received_fen: 5000 }) },
  ]) assert.throws(() => parseGuaranteeSummary(value, { storeSlug: "shenglang" }), /待核|证据|金额|状态/)
})

test("补偿事件必须指向先存在的原事件，方向相反且金额相同", () => {
  const original = event({ amount_fen: 5000 })
  const compensation = refund({ amount_fen: 5000, compensates_event_id: original.id })
  const summary = parseGuaranteeSummary({
    contract: contract({ amount_received_fen: 5000, amount_refunded_fen: 5000, net_received_fen: 0, amount_remaining_fen: 10000, status: "UNPAID" }),
    events: [original, compensation], public_status: publicStatus({ status: "NOT_COVERED", net_received_fen: 0, amount_remaining_fen: 10000 }),
  }, { storeSlug: "shenglang" })
  assert.equal(summary.events[1].compensates_event_id, original.id)
  assert.throws(() => parseGuaranteeSummary({
    contract: contract({ amount_received_fen: 5000, amount_refunded_fen: 5000, net_received_fen: 0, amount_remaining_fen: 10000, status: "UNPAID" }),
    events: [original, { ...compensation, direction: "RECEIPT" }], public_status: publicStatus({ status: "NOT_COVERED", net_received_fen: 0, amount_remaining_fen: 10000 }),
  }, { storeSlug: "shenglang" }), /补偿事件/)
})

test("合同请求写前保存本地请求号与完整版本参数，但不向 PUT 添加未知字段", () => {
  const value = contractIntent()
  assert.equal(value.client_request_id, "contract-request-1")
  assert.equal(Object.hasOwn(value.payload, "client_request_id"), false)
  assert.deepEqual(value.payload, { amount_due_fen: 10000, expected_version: 1, reason: "平台合同额度核对", source_license_contract_id: null })
  assert.deepEqual(parsePlatformWriteIntent(JSON.stringify(value), context), value)
  const receipt = parseContractResult(contract(), value)
  assert.equal(receipt.version, 2)
  assert.throws(() => parseContractResult(contract({ version: 3 }), value), /回执/)
  assert.throws(() => parseContractResult(contract({ store_slug: "xinan" }), value), /门店/)
})

test("收退请求规范化时间和摘要，回执严格匹配同号同内容与凭证", () => {
  const value = eventIntent()
  assert.equal(value.payload.occurred_at, "2026-11-20T02:00:00.000Z")
  assert.equal(value.payload.evidence_sha256, sha)
  const result = parseGuaranteeEventResult({ ...event(), replayed: true }, value)
  assert.equal(result.replayed, true)
  assert.equal(result.channel_status, "NOT_REQUESTED")
  for (const changed of [
    { ...event(), amount_fen: 5999, replayed: false },
    { ...event(), evidence_ref: "offline:other", replayed: false },
    { ...event(), client_request_id: "other", replayed: false },
    { ...event(), occurred_at: "2026-11-20T03:00:00+00:00", replayed: false },
  ]) assert.throws(() => parseGuaranteeEventResult(changed, value), /回执/)
  const changedIntent = eventIntent(5999)
  assert.equal(changedIntent.client_request_id, value.client_request_id)
  assert.notEqual(changedIntent.payload.amount_fen, value.payload.amount_fen)
})

test("闭店摘要严格校验 30 天政策、门店 slug、到期事件和门店实际状态", () => {
  assert.equal(closureSummary("NOT_STARTED").new_activity_allowed, true)
  assert.equal(closureSummary("NOTICE_ACTIVE").expiry_event, null)
  const pending = closureSummary("SETTLEMENT_PENDING")
  assert.equal(pending.store_status, "ACTIVE")
  assert.equal(pending.status, "SETTLEMENT_PENDING")
  assert.notEqual(pending.status, "DISABLED")
  for (const value of [
    { status: "NOTICE_ACTIVE", notice: notice({ store_slug: "xinan" }), expiry_event: null, new_activity_allowed: false, store_status: "ACTIVE" },
    { status: "NOTICE_ACTIVE", notice: notice({ notice_days: 29 }), expiry_event: null, new_activity_allowed: false, store_status: "ACTIVE" },
    { status: "NOTICE_ACTIVE", notice: notice({ earliest_settlement_at: startedAt }), expiry_event: null, new_activity_allowed: false, store_status: "ACTIVE" },
    { status: "NOTICE_ACTIVE", notice: notice(), expiry_event: expiry(), new_activity_allowed: false, store_status: "ACTIVE" },
    { status: "SETTLEMENT_PENDING", notice: notice({ status: "SETTLEMENT_PENDING" }), expiry_event: expiry({ occurred_at: startedAt }), new_activity_allowed: false, store_status: "ACTIVE" },
  ]) assert.throws(() => parseClosureSummary(value, { storeSlug: "shenglang" }), /闭店|公示|到期/)
})

test("闭店发起与到期回执不推造停用、退款或重复事件", () => {
  const start = parseClosureStartResult({ closure_id: noticeId, started_at: startedAt, notice_policy_version: "SA02_NOTICE_30D_V1", notice_days: 30, earliest_settlement_at: earliestAt, outcome: "CREATED", replayed: false, already_started: false })
  assert.equal(start.outcome, "CREATED")
  assert.throws(() => parseClosureStartResult({ closure_id: noticeId, started_at: startedAt, notice_policy_version: "SA02_NOTICE_30D_V1", notice_days: 30, earliest_settlement_at: earliestAt, outcome: "EXISTING", replayed: true, already_started: false }), /标记/)
  const result = parseClosureExpiryResult({ ...expiry(), status: "SETTLEMENT_PENDING", store_status: "ACTIVE", outcome: "CREATED", replayed: false, already_confirmed: false }, expiryIntent())
  assert.equal(result.store_status, "ACTIVE")
  assert.equal(result.status, "SETTLEMENT_PENDING")
  assert.throws(() => parseClosureExpiryResult({ ...expiry(), reason: "其他原因", status: "SETTLEMENT_PENDING", store_status: "ACTIVE", outcome: "CREATED", replayed: false, already_confirmed: false }, expiryIntent()), /原请求/)
  const existing = parseClosureExpiryResult({ ...expiry({ client_request_id: "other-request", reason: "其他已存在原因" }), status: "SETTLEMENT_PENDING", store_status: "ACTIVE", outcome: "EXISTING", replayed: false, already_confirmed: true }, expiryIntent())
  assert.equal(existing.already_confirmed, true)
})

test("四类恢复记录绑定门店 ID/slug、平台账号和会话，损坏记录阻止新写入", () => {
  for (const value of [contractIntent(), eventIntent(), startIntent(), expiryIntent()]) {
    const raw = JSON.stringify(value)
    assert.deepEqual(parsePlatformWriteIntent(raw, context), value)
    assert.throws(() => parsePlatformWriteIntent(raw, { ...context, storeId: 3 }), /已阻止新写入/)
    assert.throws(() => parsePlatformWriteIntent(raw, { ...context, storeSlug: "xinan" }), /已阻止新写入/)
    assert.throws(() => parsePlatformWriteIntent(raw, { ...context, actorId: "actor-2" }), /已阻止新写入/)
    assert.throws(() => parsePlatformWriteIntent(raw, { ...context, sessionId: "session-2" }), /已阻止新写入/)
  }
  assert.throws(() => parsePlatformWriteIntent("broken", context), /已阻止新写入/)
  assert.equal(parsePlatformWriteIntent(null, context), null)
})

test("写前持久化失败不发送，网络／5xx 保留原请求，首次与恢复中 403 分别处理", async () => {
  let sent = false
  await assert.rejects(submitPlatformWrite(eventIntent(), {
    save() { throw new Error("存储不可用") }, clear() {},
    async send() { sent = true; return {} as PlatformWriteResult },
  }), /存储不可用/)
  assert.equal(sent, false)
  for (const failure of [new PlatformNetworkError("offline"), new PlatformRequestError("unavailable", 503)]) {
    let saved: string | null = null
    await assert.rejects(submitPlatformWrite(eventIntent(), {
      save(value) { saved = JSON.stringify(value) }, clear() { saved = null }, async send() { throw failure },
    }))
    assert.deepEqual(parsePlatformWriteIntent(saved, context), eventIntent())
  }
  assert.deepEqual(platformWriteFailure(new PlatformRequestError("denied", 403), false), { kind: "denied", clear: true })
  assert.deepEqual(platformWriteFailure(new PlatformRequestError("denied", 403), true), { kind: "denied", clear: false })
  let saved: string | null = null
  await assert.rejects(submitPlatformWrite(eventIntent(), {
    save(value) { saved = JSON.stringify(value) }, clear() { saved = null },
    async send() { throw new PlatformRequestError("denied", 403) },
  }, { recovering: true }))
  assert.deepEqual(parsePlatformWriteIntent(saved, context), eventIntent())
})

test("收退未知结果先只读恢复原号，找到后不再次 POST，异内容则冲突", async () => {
  const sent: PlatformWriteIntent[] = []
  let saved: string | null = null
  await assert.rejects(submitPlatformWrite(eventIntent(), {
    save(value) { saved = JSON.stringify(value) }, clear() { saved = null },
    async send(value) { sent.push(value); throw new PlatformNetworkError("offline") },
  }))
  const found = await recoverPlatformWrite(eventIntent(), { guarantee: async () => guaranteeSummary(), closure: unusedClosure })
  assert.equal(found.status, "FOUND")
  assert.equal(sent.length, 1)
  assert.deepEqual(parsePlatformWriteIntent(saved, context), eventIntent())
  const conflict = await recoverPlatformWrite(eventIntent(5999), { guarantee: async () => guaranteeSummary(), closure: unusedClosure })
  assert.equal(conflict.status, "CONFLICT")
})

test("查不到收退事件后只能同号同内容重试，409 和版本冲突有明确状态", async () => {
  const value = eventIntent(), sent: PlatformWriteIntent[] = []
  const empty: GuaranteeSummary = { ...guaranteeSummary(), events: [] }
  const missing = await recoverPlatformWrite(value, { guarantee: async () => empty, closure: unusedClosure })
  assert.equal(missing.status, "NOT_FOUND")
  let saved: string | null = JSON.stringify(value)
  await submitPlatformWrite(value, {
    save(current) { saved = JSON.stringify(current) }, clear() { saved = null },
    async send(current) {
      sent.push(current)
      if (current.operation !== "guarantee-event") throw new Error("操作类型漂移")
      return parseGuaranteeEventResult({ ...event(), replayed: true }, current)
    },
  }, { recovering: true })
  assert.equal(saved, null)
  assert.deepEqual(sent[0], value)
  const conflict = new PlatformRequestError("请求编号已用于其他内容", 409, "REQUEST_ID_CONFLICT")
  assert.deepEqual(platformWriteFailure(conflict), { kind: "conflict", clear: true })
  assert.match(platformWriteMessage(conflict), /未覆盖原记录/)
  const revision = new PlatformRequestError("保证金约定已变化", 409, "GUARANTEE_REVISION_CONFLICT")
  assert.match(platformWriteMessage(revision), /合同版本已变化/)
})

test("合同未知恢复区分未找到、版本候选待审计和字段冲突，不伪称按请求号恢复", async () => {
  const value = contractIntent()
  const audit = await recoverPlatformWrite(value, { guarantee: async () => guaranteeSummary(), closure: unusedClosure })
  assert.equal(audit.status, "NEEDS_AUDIT")
  assert.match(audit.message || "", /不返回请求编号/)
  const missing = await recoverPlatformWrite(value, { guarantee: async () => ({ ...guaranteeSummary(), contract: { ...guaranteeSummary().contract!, version: 1 } }), closure: unusedClosure })
  assert.equal(missing.status, "NOT_FOUND")
  const conflict = await recoverPlatformWrite(value, { guarantee: async () => ({ ...guaranteeSummary(), contract: { ...guaranteeSummary().contract!, version: 3 } }), closure: unusedClosure })
  assert.equal(conflict.status, "CONFLICT")
})

test("闭店恢复找回原申请／到期事件，已有其他请求不重置倒计时，到期前只允许只读核对", async () => {
  const started = await recoverPlatformWrite(startIntent(), { guarantee: unusedGuarantee, closure: async () => closureSummary("NOTICE_ACTIVE") })
  assert.equal(started.status, "FOUND")
  const existing = await recoverPlatformWrite(createClosureStartIntent(context, { reasonCategory: "OTHER", reason: "另一申请" }, "closure-other"), { guarantee: unusedGuarantee, closure: async () => closureSummary("NOTICE_ACTIVE") })
  assert.equal(existing.status, "ALREADY_EXISTS")
  const early = await recoverPlatformWrite(expiryIntent(), { guarantee: unusedGuarantee, closure: async () => closureSummary("NOTICE_ACTIVE") })
  assert.equal(early.status, "NOT_FOUND")
  assert.equal(closureSummary("NOTICE_ACTIVE").expiry_event, null)
  const confirmed = await recoverPlatformWrite(expiryIntent(), { guarantee: unusedGuarantee, closure: async () => closureSummary("SETTLEMENT_PENDING") })
  assert.equal(confirmed.status, "FOUND")
  const notStarted = await recoverPlatformWrite(expiryIntent(), { guarantee: unusedGuarantee, closure: async () => closureSummary("NOT_STARTED") })
  assert.equal(notStarted.status, "CONFLICT")
})

test("页面状态区分加载、首次失败、刷新旧快照过期和就绪", () => {
  assert.equal(platformSnapshotMode({ hasSnapshot: false, loading: true, error: "" }), "loading")
  assert.equal(platformSnapshotMode({ hasSnapshot: false, loading: false, error: "读取失败" }), "error")
  assert.equal(platformSnapshotMode({ hasSnapshot: true, loading: false, error: "刷新失败" }), "stale")
  assert.equal(platformSnapshotMode({ hasSnapshot: true, loading: false, error: "" }), "ready")
})
