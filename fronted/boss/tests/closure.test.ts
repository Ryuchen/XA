import assert from "node:assert/strict"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { StoreClosureCardView } from "../src/business/closure-card.tsx"
import { storeGuaranteeScope } from "../src/business/guarantee.ts"
import {
  beginStoreClosureLoad,
  completeStoreClosureLoad,
  emptyStoreClosureState,
  failStoreClosureLoad,
  formatStoreClosureTime,
  parseStoreClosure,
  requestPublicStoreClosure,
  storeClosureScope,
  type StoreClosureState,
} from "../src/business/closure.ts"

const scope = "shenglang:1:1"
const notStarted = { status: "NOT_STARTED", announcement_visible: false }
const activeText = "本店正在进行闭店公示。既有订单、售后与权益查询仍按原入口办理。"
const pendingText = "本店闭店公示期已届满，待平台完成后续结清核对；本状态不代表余额已返还。"
const unknownText = "本店闭店公示状态暂时待核，请以平台后续公告为准。"
const active = {
  status: "NOTICE_ACTIVE",
  announcement_visible: true,
  clock_status: "VERIFIED",
  announcement: activeText,
  started_at: "2026-11-01T04:00:00+00:00",
  earliest_settlement_at: "2026-12-01T04:00:00+00:00",
  notice_days: 30,
}
const payload = (overrides: Record<string, unknown> = {}) => ({ ...active, ...overrides })

function ready(raw = active, at = scope): StoreClosureState {
  const initial = beginStoreClosureLoad(emptyStoreClosureState(at), at)
  const result = completeStoreClosureLoad(initial, at, raw)
  assert.equal(result.error, null)
  assert.ok(result.snapshot)
  return result
}

function html(state: StoreClosureState, storeName = "声浪电竞") {
  return renderToStaticMarkup(createElement(StoreClosureCardView, {
    state,
    storeName,
    onRefresh: () => {},
  }))
}

test("NOT_STARTED is exact, concise and neutral without alarms or fake actions", () => {
  const parsed = parseStoreClosure(notStarted)
  assert.equal(parsed.status, "NOT_STARTED")
  assert.equal(parsed.announcementVisible, false)
  const markup = html(ready(notStarted))
  assert.match(markup, /未公示/)
  assert.match(markup, /当前未发布闭店公示/)
  assert.doesNotMatch(markup, /role="alert"/)
  assert.doesNotMatch(markup, /倒计时|取消闭店|确认结清|申请退款|余额返还入口/)
  assert.doesNotMatch(markup, /<a\s/)
})

test("NOTICE_ACTIVE uses snake_case public fields and the actual earliest settlement time", () => {
  assert.equal("notice_policy_version" in active, false)
  const parsed = parseStoreClosure(active)
  assert.equal(parsed.earliestSettlementAt, active.earliest_settlement_at)
  const markup = html(ready(active))
  assert.match(markup, /闭店公示中/)
  assert.match(markup, /最早结清时间/)
  assert.match(markup, /2026\/12\/01/)
  assert.match(markup, /此时点直接来自后端公示记录/)
  assert.match(markup, /不计算本地倒计时/)
  assert.doesNotMatch(markup, /政策版本|自动 DISABLED|剩余\s*\d+\s*天/)
})

test("SETTLEMENT_PENDING explicitly says refunds and final disablement are incomplete", () => {
  const raw = payload({ status: "SETTLEMENT_PENDING", announcement: pendingText })
  const markup = html(ready(raw))
  assert.match(markup, /待结清，尚未完成余额返还或最终停用/)
  assert.match(markup, /原公示最早结清时间/)
  assert.match(markup, /不代表余额已返还或门店已最终停用/)
  assert.doesNotMatch(markup, /自动 DISABLED|余额返还已经完成|门店已经停用/)
})

test("UNKNOWN evidence, unavailable and review responses stay pending and never become NOT_STARTED", () => {
  const evidence = parseStoreClosure({
    status: "UNKNOWN", announcement_visible: false, clock_status: "EVIDENCE_REVIEW",
  })
  assert.equal(evidence.clockStatus, "EVIDENCE_REVIEW")
  assert.match(html(ready({
    status: "UNKNOWN", announcement_visible: false, clock_status: "EVIDENCE_REVIEW",
  })), /不能按未公示处理/)
  for (const clockStatus of ["UNAVAILABLE", "REVIEW"]) {
    const raw = payload({ status: "UNKNOWN", clock_status: clockStatus, announcement: unknownText })
    const markup = html(ready(raw))
    assert.match(markup, /状态待核/)
    assert.match(markup, /不能按未公示处理/)
    assert.doesNotMatch(markup, /当前未发布闭店公示/)
  }
})

test("strict conditional schemas reject missing fields, camelCase aliases and unknown statuses", () => {
  assert.throws(() => parseStoreClosure({ status: "NOT_STARTED" }))
  assert.throws(() => parseStoreClosure({ status: "NOT_STARTED", announcement_visible: false, announcement: null }))
  assert.throws(() => parseStoreClosure({ ...notStarted, announcementVisible: false }))
  assert.throws(() => parseStoreClosure({ status: "CLOSED", announcement_visible: true }))
  for (const field of ["clock_status", "announcement", "started_at", "earliest_settlement_at", "notice_days"]) {
    const raw = payload() as Record<string, unknown>
    delete raw[field]
    assert.throws(() => parseStoreClosure(raw))
  }
})

test("internal reasons, actors, request ids, clock identities and amounts are rejected without entering snapshots", () => {
  const secrets = [
    ["reason", "内部秘密闭店原因"],
    ["reason_category", "OPERATOR_REQUEST"],
    ["applicant", "internal-applicant"],
    ["client_request_id", "closure-secret-request"],
    ["clock_identity", "mysql-internal-identity"],
    ["clock_source", "CONTROL_DATABASE"],
    ["amount_fen", 10000],
    ["balance_fen", 10000],
  ] as const
  for (const [field, secret] of secrets) {
    assert.throws(() => parseStoreClosure(payload({ [field]: secret })))
    const failed = completeStoreClosureLoad(ready(), scope, payload({ [field]: secret }))
    assert.equal(failed.stale, true)
    assert.equal(failed.snapshot?.data.status, "NOTICE_ACTIVE")
    assert.doesNotMatch(JSON.stringify(failed), new RegExp(String(secret)))
  }
})

test("rejects invalid visibility, clock states, announcements, timestamps and notice days", () => {
  for (const raw of [
    payload({ announcement_visible: false }),
    payload({ clock_status: "UNAVAILABLE" }),
    payload({ announcement: "平台已完成退款并停用" }),
    payload({ started_at: "2026-11-01 04:00:00" }),
    payload({ earliest_settlement_at: "not-a-date" }),
    payload({ earliest_settlement_at: active.started_at }),
    payload({ notice_days: 29 }),
    payload({ notice_days: 30.5 }),
    payload({ status: "UNKNOWN", clock_status: "VERIFIED", announcement: unknownText }),
    { status: "UNKNOWN", announcement_visible: false, clock_status: "UNAVAILABLE" },
  ]) {
    assert.throws(() => parseStoreClosure(raw))
  }
})

test("frontend never infers a new status from browser time or the thirty-day boundary", () => {
  const pastActive = payload({
    started_at: "2020-01-01T00:00:00+00:00",
    earliest_settlement_at: "2020-01-31T00:00:00+00:00",
  })
  assert.equal(parseStoreClosure(pastActive).status, "NOTICE_ACTIVE")
  assert.match(html(ready(pastActive)), /闭店公示中/)
  const futurePending = payload({
    status: "SETTLEMENT_PENDING",
    announcement: pendingText,
    started_at: "2099-01-01T00:00:00+00:00",
    earliest_settlement_at: "2099-01-31T00:00:00+00:00",
  })
  assert.equal(parseStoreClosure(futurePending).status, "SETTLEMENT_PENDING")
  assert.match(html(ready(futurePending)), /待结清，尚未完成余额返还或最终停用/)
  assert.equal(formatStoreClosureTime(null), "待核")
})

test("store and session scope switches remove old notices and ignore late responses", () => {
  const first = ready()
  const nextScope = storeClosureScope({ id: 2, slug: "xinan" }, 2)
  const switched = beginStoreClosureLoad(first, nextScope)
  assert.equal(switched.snapshot, null)
  assert.equal(completeStoreClosureLoad(switched, scope, active), switched)
  assert.equal(failStoreClosureLoad(switched, scope, new Error("late")), switched)
  assert.equal(nextScope, storeGuaranteeScope({ id: 2, slug: "xinan" }, 2))
  assert.notEqual(storeClosureScope({ id: 1, slug: "shenglang" }, 1),
    storeClosureScope({ id: 1, slug: "shenglang" }, 2))
})

test("refresh failure retains the same-store notice only as an expired snapshot", () => {
  const failed = failStoreClosureLoad(ready(), scope, Object.assign(new Error("offline"), { status: 503 }))
  const markup = html(failed)
  assert.equal(failed.stale, true)
  assert.match(markup, /data-stale="true"/)
  assert.match(markup, /上次快照已过期/)
  assert.match(markup, /不能视为当前状态/)
  assert.match(markup, /闭店公示中/)
  assert.match(markup, /2026\/12\/01/)
  const recovered = completeStoreClosureLoad(failed, scope, notStarted)
  assert.equal(recovered.stale, false)
  assert.equal(recovered.snapshot?.data.status, "NOT_STARTED")
})

test("public closure transport checks store identity and sends no authentication requirement", async () => {
  const controller = new AbortController()
  const calls: unknown[] = []
  const result = await requestPublicStoreClosure(async (...args) => {
    calls.push(args)
    return active
  }, { store: { id: 1, slug: "shenglang" }, resolvedSlug: "shenglang", signal: controller.signal })
  assert.deepEqual(result, active)
  assert.deepEqual(calls, [["/stores/closure/", undefined, { public: true, signal: controller.signal }]])
  for (const [store, resolvedSlug] of [
    [{ id: 0, slug: "shenglang" }, "shenglang"],
    [{ id: 1, slug: "shenglang" }, "xinan"],
  ] as const) {
    await assert.rejects(requestPublicStoreClosure(async () => {
      calls.push("must not request")
    }, { store, resolvedSlug, signal: controller.signal }), /门店身份与当前域名不一致/)
  }
  assert.equal(calls.length, 1)
})

test("loading, empty, first failures and 401/403/404 are explicit rather than NOT_STARTED", () => {
  const initial = beginStoreClosureLoad(emptyStoreClosureState(scope), scope)
  const loading = html(initial)
  assert.match(loading, /正在读取当前门店闭店公告/)
  assert.match(loading, /disabled=""/)
  const empty = completeStoreClosureLoad(initial, scope, null)
  assert.match(html(empty), /闭店公告暂未提供可核对信息/)
  assert.doesNotMatch(html(empty), /当前未发布闭店公示/)
  for (const [status, text] of [
    [401, /访问身份待核（401）/],
    [403, /当前入口未能读取闭店公告（403）/],
    [404, /当前门店不存在或闭店公告不可用/],
    [503, /闭店公告暂时无法读取/],
    [0, /当前状态待核/],
  ] as const) {
    const failed = failStoreClosureLoad(initial, scope, Object.assign(new Error("failed"), { status }))
    assert.match(html(failed), text)
    assert.match(html(failed), /不能将读取失败解释为未公示/)
  }
})

test("refresh in progress retains the old notice, marks it as a snapshot and blocks duplicate refreshes", () => {
  const markup = html(beginStoreClosureLoad(ready(), scope))
  assert.match(markup, /正在刷新/)
  assert.match(markup, /上次成功读取的闭店公告快照/)
  assert.match(markup, /disabled=""/)
  assert.match(markup, /闭店公示中/)
})
