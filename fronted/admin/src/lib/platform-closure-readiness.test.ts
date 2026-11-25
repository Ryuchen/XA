/// <reference types="node" />
import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"
import {
  beginClosureReadinessLoad,
  closureReadinessMode,
  closureReadinessPath,
  closureReadinessReasonLabel,
  closureReadinessScopeKey,
  completeClosureReadinessLoad,
  emptyClosureReadinessState,
  failClosureReadinessLoad,
  formatClosureReadinessValue,
  parseClosureReadiness,
  requestClosureReadiness,
  type ClosureReadinessScope,
} from "./platform-closure-readiness.ts"

const sha = "a".repeat(64)
const fundsSha = "b".repeat(64)
const obligationsSha = "c".repeat(64)
const observedAt = "2026-11-25T08:00:00+00:00"
const totals = {
  wallet_units: 100,
  principal_units: 100,
  gift_units: 0,
  compensation_units: 0,
  points_hundredths: 321,
  frozen_units: 0,
  refund_fen: 100,
  paid_fen: 12500,
}
const feeBlocker = { code: "FEE_RULES_PENDING", resource: "closure_policy", reference: "PC04" }
const barrierBlocker = {
  code: "FINAL_WRITE_BARRIER_UNAVAILABLE",
  resource: "closure_policy",
  reference: "shared-write-barrier",
}
const fundsBlocker = {
  code: "WALLET_BALANCE_REMAINING",
  resource: "wallet",
  reference: "account:807101",
}
const scope: ClosureReadinessScope = {
  storeId: 2,
  storeSlug: "shenglang",
  actorId: "actor-1",
  sessionId: "session-1",
  scopeSignature: '[{"role":"SUPER_ADMIN","scope_slug":"shenglang"}]',
}

function blockedPayload(overrides: Record<string, unknown> = {}) {
  return {
    status: "BLOCKED",
    complete: true,
    can_finalize: false,
    observed_at: observedAt,
    snapshot_sha256: sha,
    funds: {
      complete: true,
      status: "BLOCKED",
      account_count: 2,
      completed_count: 1,
      zero_balance_count: 0,
      totals: { ...totals },
      blockers: [fundsBlocker],
      facts_sha256: fundsSha,
    },
    obligations: {
      complete: true,
      status: "CLEAR",
      metrics: [
        { code: "OPEN_ORDERS", count: 0 },
        { code: "OPEN_AFTERSALE", count: 0 },
      ],
      blockers: [],
      facts_sha256: obligationsSha,
    },
    policy: {
      complete: true,
      fee_rules: "PENDING",
      final_write_barrier: "UNAVAILABLE",
      blockers: [feeBlocker, barrierBlocker],
    },
    blocking_reasons: [
      "FUNDS_REVIEW_REQUIRED",
      "FEE_RULES_PENDING",
      "FINAL_WRITE_BARRIER_UNAVAILABLE",
    ],
    ...overrides,
  }
}

function unknownPayload(overrides: Record<string, unknown> = {}) {
  return blockedPayload({
    status: "UNKNOWN",
    complete: false,
    funds: {
      complete: false,
      status: "UNKNOWN",
      account_count: 0,
      completed_count: 0,
      zero_balance_count: 0,
      totals: null,
      blockers: [],
      facts_sha256: fundsSha,
    },
    obligations: {
      complete: false,
      status: "UNKNOWN",
      metrics: [{ code: "OPEN_ORDERS", count: null }],
      blockers: [],
      facts_sha256: obligationsSha,
    },
    blocking_reasons: [
      "FUNDS_REVIEW_REQUIRED",
      "BUSINESS_OBLIGATIONS",
      "FEE_RULES_PENDING",
      "FINAL_WRITE_BARRIER_UNAVAILABLE",
      "READ_INCOMPLETE",
    ],
    ...overrides,
  })
}

function invalid(value: unknown) {
  assert.throws(() => parseClosureReadiness(value), /预检|待核|阻断/)
}

test("严格解析 BLOCKED 契约并转换为 camelCase，保留八项金额和 false 终态门禁", () => {
  const result = parseClosureReadiness(blockedPayload())
  assert.equal(result.status, "BLOCKED")
  assert.equal(result.complete, true)
  assert.equal(result.canFinalize, false)
  assert.equal(result.observedAt, observedAt)
  assert.equal(result.snapshotSha256, sha)
  assert.equal(result.funds.accountCount, 2)
  assert.equal(result.funds.completedCount, 1)
  assert.equal(result.funds.zeroBalanceCount, 0)
  assert.deepEqual(result.funds.totals, {
    walletUnits: 100,
    principalUnits: 100,
    giftUnits: 0,
    compensationUnits: 0,
    pointsHundredths: 321,
    frozenUnits: 0,
    refundFen: 100,
    paidFen: 12500,
  })
  assert.equal(result.funds.factsSha256, fundsSha)
  assert.equal(result.obligations.metrics[0].code, "OPEN_ORDERS")
  assert.equal(result.policy.feeRules, "PENDING")
  assert.equal(result.policy.finalWriteBarrier, "UNAVAILABLE")
  assert.equal(Object.hasOwn(result, "can_finalize"), false)
  assert.equal(Object.hasOwn(result.funds.totals, "wallet_units"), false)
})

test("UNKNOWN 保持整体金额 null 和义务指标 null，不把未知补成零", () => {
  const result = parseClosureReadiness(unknownPayload())
  assert.equal(result.complete, false)
  assert.equal(result.status, "UNKNOWN")
  assert.equal(result.funds.totals, null)
  assert.equal(result.obligations.metrics[0].count, null)
  assert.equal(result.canFinalize, false)
  assert.ok(result.blockingReasons.includes("READ_INCOMPLETE"))
  assert.equal(formatClosureReadinessValue(null), "—")
  assert.equal(formatClosureReadinessValue(0), "0")
  assert.equal(formatClosureReadinessValue(12345), "12,345")
})

test("顶级只接受 BLOCKED／UNKNOWN，complete 与分域完整性一致，且永远拒绝可结清标记", () => {
  for (const value of [
    blockedPayload({ can_finalize: true }),
    blockedPayload({ status: "READY_TO_FINALIZE" }),
    blockedPayload({ complete: false }),
    unknownPayload({ status: "BLOCKED" }),
    blockedPayload({ snapshot_sha256: "not-a-digest" }),
    blockedPayload({ observed_at: "2026-11-25 08:00:00" }),
    blockedPayload({ unexpected: true }),
    { ...blockedPayload(), snapshot_sha256: undefined },
  ]) invalid(value)
})

test("funds 严格校验计数、状态、块项、64 位事实摘要及 totals 完整结构", () => {
  const missingAmount = blockedPayload()
  delete (missingAmount.funds.totals as Record<string, unknown>).paid_fen
  const nullWhenComplete = blockedPayload()
  nullWhenComplete.funds.totals = null as unknown as typeof totals
  const totalsWhenIncomplete = unknownPayload()
  ;(totalsWhenIncomplete.funds as Record<string, unknown>).totals = { ...totals }
  for (const value of [
    blockedPayload({ funds: { ...blockedPayload().funds, account_count: -1 } }),
    blockedPayload({ funds: { ...blockedPayload().funds, completed_count: 1.5 } }),
    blockedPayload({ funds: { ...blockedPayload().funds, status: "UNKNOWN" } }),
    blockedPayload({ funds: { ...blockedPayload().funds, status: "CLEAR", blockers: [fundsBlocker] } }),
    blockedPayload({ funds: { ...blockedPayload().funds, status: "CLEAR", account_count: 0, completed_count: 0, zero_balance_count: 0, blockers: [] } }),
    blockedPayload({ funds: { ...blockedPayload().funds, facts_sha256: "abc" } }),
    missingAmount,
    nullWhenComplete,
    totalsWhenIncomplete,
  ]) invalid(value)
  const nullableAmount = blockedPayload()
  nullableAmount.funds.totals.refund_fen = null as unknown as number
  assert.equal(parseClosureReadiness(nullableAmount).funds.totals?.refundFen, null)
})

test("obligations 每项严格为 code／count，拒绝重复指标、非整数和缺字段", () => {
  const base = blockedPayload().obligations
  for (const metrics of [
    [{ code: "OPEN_ORDERS" }],
    [{ code: "OPEN_ORDERS", count: 0, detail: "内部备注" }],
    [{ code: "OPEN_ORDERS", count: -1 }],
    [{ code: "OPEN_ORDERS", count: 1.2 }],
    [{ code: "OPEN_ORDERS", count: "0" }],
    [{ code: "OPEN_ORDERS", count: 0 }, { code: "OPEN_ORDERS", count: 1 }],
  ]) invalid(blockedPayload({ obligations: { ...base, metrics } }))
  invalid(blockedPayload({ obligations: { ...base, complete: false } }))
  invalid(unknownPayload({ obligations: { ...unknownPayload().obligations, status: "CLEAR" } }))
})

test("policy 固定 complete=true、费用 PENDING、最终写入屏障 UNAVAILABLE", () => {
  const base = blockedPayload().policy
  for (const policy of [
    { ...base, complete: false },
    { ...base, fee_rules: "CLEAR" },
    { ...base, final_write_barrier: "AVAILABLE" },
    { ...base, blockers: [] },
    { ...base, facts_sha256: sha },
  ]) invalid(blockedPayload({ policy }))
})

test("统一 blocker 严格脱敏，拒绝额外原文、完整银行资料、URL 和不安全引用", () => {
  const hyphenatedResource = blockedPayload({
    funds: {
      ...blockedPayload().funds,
      blockers: [{ ...fundsBlocker, resource: "order-report" }],
    },
  })
  assert.equal(parseClosureReadiness(hyphenatedResource).funds.blockers[0].resource, "order-report")
  for (const blocker of [
    { ...fundsBlocker, detail: "老板内部备注" },
    { ...fundsBlocker, reference: "6222020200112233445" },
    { ...fundsBlocker, reference: "https://example.invalid/private" },
    { ...fundsBlocker, reference: "" },
    { ...fundsBlocker, resource: "老板 807101" },
    { code: "missing-resource", reference: "case:1" },
  ]) {
    invalid(blockedPayload({ funds: { ...blockedPayload().funds, blockers: [blocker] } }))
  }
})

test("blocking_reasons 必须区分费用、写入屏障、资金、义务和读取不完整", () => {
  for (const reasons of [
    ["FUNDS_REVIEW_REQUIRED"],
    ["FEE_RULES_PENDING", "FINAL_WRITE_BARRIER_UNAVAILABLE"],
    ["FUNDS_REVIEW_REQUIRED", "FUNDS_REVIEW_REQUIRED", "FEE_RULES_PENDING", "FINAL_WRITE_BARRIER_UNAVAILABLE"],
  ]) invalid(blockedPayload({ blocking_reasons: reasons }))
  const unknown = unknownPayload()
  invalid({ ...unknown, blocking_reasons: unknown.blocking_reasons.filter(code => code !== "READ_INCOMPLETE") })
  const obligationsBlocked = blockedPayload({
    obligations: {
      ...blockedPayload().obligations,
      status: "BLOCKED",
      blockers: [{ code: "OPEN_ORDERS", resource: "order", reference: "order:123" }],
    },
  })
  invalid(obligationsBlocked)
  assert.equal(closureReadinessReasonLabel("FUNDS_REVIEW_REQUIRED"), "老板资金仍需复核或返还")
  assert.equal(closureReadinessReasonLabel("SOME_FUTURE_CODE"), "其他阻断")
})

test("请求路径只绑定门店 ID；请求支持取消且不发送 query／header 切店参数", async () => {
  assert.equal(
    closureReadinessPath(2),
    "store-management/2/closure/settlement-readiness",
  )
  for (const id of [0, -1, 1.2, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => closureReadinessPath(id), /门店/)
  }
  const controller = new AbortController()
  let requested = false
  const pending = requestClosureReadiness(2, {
    signal: controller.signal,
    request(path, signal) {
      requested = true
      assert.equal(path, "store-management/2/closure/settlement-readiness")
      assert.equal(signal, controller.signal)
      return new Promise(() => {})
    },
  })
  const rejection = assert.rejects(pending, /取消|中止/)
  assert.equal(requested, true)
  controller.abort()
  await rejection

  const alreadyAborted = new AbortController()
  alreadyAborted.abort()
  requested = false
  await assert.rejects(requestClosureReadiness(2, {
    signal: alreadyAborted.signal,
    async request() { requested = true; return blockedPayload() },
  }), /取消|中止/)
  assert.equal(requested, false)
})

test("作用域 key 绑定门店 ID／slug、平台账号、会话和 scope", () => {
  const key = closureReadinessScopeKey(scope)
  assert.equal(closureReadinessScopeKey(scope), key)
  for (const changed of [
    { ...scope, storeId: 3 },
    { ...scope, storeSlug: "xinan" },
    { ...scope, actorId: "actor-2" },
    { ...scope, sessionId: "session-2" },
    { ...scope, scopeSignature: "[]" },
  ]) assert.notEqual(closureReadinessScopeKey(changed), key)
})

test("加载、有数据、异常、空指标和刷新过期快照均有独立状态", () => {
  const key = closureReadinessScopeKey(scope)
  const initial = emptyClosureReadinessState(key)
  assert.equal(closureReadinessMode(initial), "loading")
  const ready = completeClosureReadinessLoad(initial, key, parseClosureReadiness(blockedPayload()))
  assert.equal(closureReadinessMode(ready), "ready")
  assert.equal(ready.stale, false)
  const refreshing = beginClosureReadinessLoad(ready, key)
  assert.equal(refreshing.snapshot?.data.complete, true)
  assert.equal(refreshing.loading, true)
  const stale = failClosureReadinessLoad(refreshing, key, new Error("原始后端响应不得泄露"))
  assert.equal(stale.stale, true)
  assert.equal(stale.snapshot?.data.status, "BLOCKED")
  assert.equal(stale.error?.message.includes("原始后端响应"), false)
  assert.equal(stale.snapshot?.data.canFinalize, false)
  const failed = failClosureReadinessLoad(emptyClosureReadinessState(key), key, new Error("offline"))
  assert.equal(closureReadinessMode(failed), "error")

  const empty = blockedPayload({
    funds: {
      ...blockedPayload().funds,
      account_count: 0,
      completed_count: 0,
      zero_balance_count: 0,
    },
    obligations: { ...blockedPayload().obligations, metrics: [] },
  })
  const emptyState = completeClosureReadinessLoad(initial, key, parseClosureReadiness(empty))
  assert.equal(closureReadinessMode(emptyState), "empty")
})

test("门店／会话／scope 变化立即清旧快照，迟到完成和失败均不能改写新状态", () => {
  const firstKey = closureReadinessScopeKey(scope)
  const secondKey = closureReadinessScopeKey({ ...scope, storeId: 3, storeSlug: "xinan" })
  const first = completeClosureReadinessLoad(
    emptyClosureReadinessState(firstKey), firstKey, parseClosureReadiness(blockedPayload()),
  )
  const switched = beginClosureReadinessLoad(first, secondKey)
  assert.equal(switched.snapshot, null)
  assert.equal(switched.scopeKey, secondKey)
  assert.equal(completeClosureReadinessLoad(switched, firstKey, parseClosureReadiness(blockedPayload())), switched)
  assert.equal(failClosureReadinessLoad(switched, firstKey, new Error("late")), switched)
})

test("权限拒绝清除旧快照，安全错误文案不透传原始响应", () => {
  const key = closureReadinessScopeKey(scope)
  const ready = completeClosureReadinessLoad(emptyClosureReadinessState(key), key, parseClosureReadiness(blockedPayload()))
  const denied = failClosureReadinessLoad(ready, key, Object.assign(new Error("内部权限细节"), { status: 403 }))
  assert.equal(denied.snapshot, null)
  assert.equal(denied.stale, false)
  assert.match(denied.error?.message || "", /无权/)
  assert.equal(denied.error?.message.includes("内部权限细节"), false)
  const invalidResponse = failClosureReadinessLoad(ready, key, new SyntaxError("secret response"))
  assert.equal(invalidResponse.error?.message.includes("secret response"), false)
})

test("只读面板固定安全文案，不包含最终确认、提交或 confirm-final 接线", async () => {
  const source = await readFile(new URL("../components/PlatformClosureReadiness.tsx", import.meta.url), "utf8")
  assert.match(source, /预检不是结清/)
  assert.match(source, /当前不可最终停用/)
  assert.equal(source.includes("confirm-final"), false)
  assert.equal(source.includes("onFinalize"), false)
  assert.equal(source.includes('type="submit"'), false)
})

test("面板覆盖后端十二类义务，读取不完整时账户计数保持未知", async () => {
  const source = await readFile(new URL("../components/PlatformClosureReadiness.tsx", import.meta.url), "utf8")
  for (const code of [
    "ORDERS", "RESERVATIONS", "AFTERSALES", "REPORTS", "INCOME_HOLDS", "WITHDRAWALS",
    "DEPOSIT_DEFICITS", "RECOVERY_PERMITS", "GIFT_APPROVALS", "REWARD_CLAIMS",
    "PAYROLL", "OTHER_RIGHTS",
  ]) assert.match(source, new RegExp(`\\b${code}:`))
  for (const field of ["accountCount", "completedCount", "zeroBalanceCount"]) {
    assert.match(source, new RegExp(`data\\.funds\\.complete \\? data\\.funds\\.${field} : null`))
  }
})
