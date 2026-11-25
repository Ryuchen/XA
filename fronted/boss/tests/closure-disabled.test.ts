import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { StoreClosureCardView } from "../src/business/closure-card.tsx"
import {
  beginStoreClosureLoad,
  completeStoreClosureLoad,
  emptyStoreClosureState,
  failStoreClosureLoad,
  parseStoreClosure,
  storeClosureScope,
  type StoreClosureState,
} from "../src/business/closure.ts"

const scope = "shenglang:1:1"
const disabledText = "本店已停用，不再接受新增业务。既有订单、售后与余额返还情况需另行核对；停用不代表已完成退款或结清。"
const disabled = {
  status: "DISABLED",
  announcement_visible: true,
  clock_status: "NOT_REQUIRED",
  announcement: disabledText,
}
const active = {
  status: "NOTICE_ACTIVE",
  announcement_visible: true,
  clock_status: "VERIFIED",
  announcement: "本店正在进行闭店公示。既有订单、售后与权益查询仍按原入口办理。",
  started_at: "2026-11-01T04:00:00+00:00",
  earliest_settlement_at: "2026-12-01T04:00:00+00:00",
  notice_days: 30,
}

function ready(raw: unknown = active, at = scope): StoreClosureState {
  const initial = beginStoreClosureLoad(emptyStoreClosureState(at), at)
  const result = completeStoreClosureLoad(initial, at, raw)
  assert.equal(result.error, null)
  assert.ok(result.snapshot)
  return result
}

function html(state: StoreClosureState) {
  return renderToStaticMarkup(createElement(StoreClosureCardView, {
    state,
    storeName: "声浪电竞",
    onRefresh: () => {},
  }))
}

test("DISABLED accepts only the four-field public contract and fixed announcement", () => {
  const parsed = parseStoreClosure(disabled)
  assert.deepEqual(parsed, {
    status: "DISABLED",
    announcementVisible: true,
    clockStatus: "NOT_REQUIRED",
    announcement: disabledText,
    startedAt: null,
    earliestSettlementAt: null,
    noticeDays: null,
  })
  const markup = html(ready(disabled))
  assert.match(markup, /data-status="DISABLED"/)
  assert.match(markup, /门店已停用/)
  assert.match(markup, /role="alert"/)
  assert.match(markup, /不再接受新增业务/)
  assert.match(markup, /停用不代表已完成退款或结清/)
  assert.doesNotMatch(markup, /最早结清时间|当前未发布闭店公示|可继续下单|余额已返还|已完成结清/)

  for (const field of Object.keys(disabled)) {
    const raw = { ...disabled } as Record<string, unknown>
    delete raw[field]
    assert.throws(() => parseStoreClosure(raw), undefined, `missing ${field}`)
  }
  for (const raw of [
    { ...disabled, announcement_visible: false },
    { ...disabled, clock_status: "VERIFIED" },
    { ...disabled, announcement: "本店已停用并完成退款" },
    { ...disabled, started_at: active.started_at },
    { ...disabled, earliest_settlement_at: active.earliest_settlement_at },
    { ...disabled, notice_days: 30 },
    { ...disabled, reason: "内部原因" },
    { ...disabled, amount_fen: 100 },
  ]) assert.throws(() => parseStoreClosure(raw))
})

test("a successful refresh replaces an active notice with DISABLED", () => {
  const refreshed = completeStoreClosureLoad(ready(), scope, disabled)
  assert.equal(refreshed.error, null)
  assert.equal(refreshed.stale, false)
  assert.equal(refreshed.snapshot?.data.status, "DISABLED")
  assert.equal(refreshed.snapshot?.data.earliestSettlementAt, null)
  const markup = html(refreshed)
  assert.match(markup, /门店已停用/)
  assert.doesNotMatch(markup, /闭店公示中|最早结清时间|2026\/12\/01/)
})

test("a late cross-store DISABLED response cannot replace the new store loading state", () => {
  const switched = beginStoreClosureLoad(ready(), storeClosureScope({ id: 2, slug: "xinan" }, 2))
  assert.equal(switched.snapshot, null)
  assert.equal(completeStoreClosureLoad(switched, scope, disabled), switched)
  assert.equal(switched.snapshot, null)
})

test("UNKNOWN and HTTP 403/404/503 never infer DISABLED or NOT_STARTED", () => {
  const initial = beginStoreClosureLoad(emptyStoreClosureState(scope), scope)
  const unknown = ready({
    status: "UNKNOWN",
    announcement_visible: false,
    clock_status: "EVIDENCE_REVIEW",
  })
  for (const state of [
    unknown,
    ...[403, 404, 503].map(status => failStoreClosureLoad(
      initial,
      scope,
      Object.assign(new Error("fixture transport failure"), { status }),
    )),
  ]) {
    const markup = html(state)
    assert.doesNotMatch(markup, /data-status="DISABLED"|门店已停用|data-status="NOT_STARTED"|当前未发布闭店公示/)
    assert.match(markup, /状态待核/)
  }
})

test("the boss shared shell owns one closure request outside account and order business states", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8")
  const account = readFileSync(new URL("../src/business/account.tsx", import.meta.url), "utf8")
  const orders = readFileSync(new URL("../src/business/orders.tsx", import.meta.url), "utf8")
  const mounts = app.match(/<StoreClosureCard\s*\/>/g) ?? []
  assert.equal(mounts.length, 1, "the shared shell must mount the announcement exactly once")
  assert.ok(app.indexOf("<StoreClosureCard />") < app.indexOf("<Routes>"), "the announcement must survive routed business API failures")
  assert.doesNotMatch(account, /StoreClosureCard/, "the account Promise.all State must not own the announcement")
  assert.doesNotMatch(orders, /选择服务并下单后/, "an empty order list must not invite a new order")
})
