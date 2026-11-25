import assert from "node:assert/strict"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { StoreGuaranteeCardView } from "../src/business/guarantee-card.tsx"
import {
  beginStoreGuaranteeLoad,
  completeStoreGuaranteeLoad,
  emptyStoreGuaranteeState,
  failStoreGuaranteeLoad,
  formatStoreGuaranteeAmount,
  parseStoreGuarantee,
  requestPublicStoreGuarantee,
  storeGuaranteePresentation,
  storeGuaranteeScope,
  type StoreGuaranteeState,
} from "../src/business/guarantee.ts"

const scope = "shenglang:1:1"
const covered = {
  status: "COVERED",
  label: "保证金已按合同登记足额",
  currency: "CNY",
  amount_due_fen: 10000,
  net_received_fen: 10000,
  amount_remaining_fen: 0,
  record_basis: "OFFLINE_EVIDENCE",
}
const payload = (overrides: Record<string, unknown> = {}) => ({ ...covered, ...overrides })

function ready(raw = covered, at = scope): StoreGuaranteeState {
  const initial = beginStoreGuaranteeLoad(emptyStoreGuaranteeState(at), at)
  const result = completeStoreGuaranteeLoad(initial, at, raw)
  assert.equal(result.error, null)
  assert.ok(result.snapshot)
  return result
}

function html(state: StoreGuaranteeState, storeName = "声浪电竞") {
  return renderToStaticMarkup(createElement(StoreGuaranteeCardView, {
    state,
    storeName,
    onRefresh: () => {},
  }))
}

test("accepts exactly the four public states and formats known values as RMB fen", () => {
  const rows = [
    payload({
      status: "PENDING_REVIEW", label: "保障信息待核",
      amount_due_fen: null, net_received_fen: null, amount_remaining_fen: null,
    }),
    payload({
      status: "NOT_COVERED", label: "未形成保证金保障",
      amount_due_fen: 10000, net_received_fen: 0, amount_remaining_fen: 10000,
    }),
    payload({
      status: "PARTIAL", label: "保证金部分登记",
      amount_due_fen: 10000, net_received_fen: 4000, amount_remaining_fen: 6000,
    }),
    covered,
  ]
  assert.deepEqual(rows.map(row => parseStoreGuarantee(row).status), [
    "PENDING_REVIEW", "NOT_COVERED", "PARTIAL", "COVERED",
  ])
  assert.equal(formatStoreGuaranteeAmount(10000), "¥ 100.00")
  assert.equal(formatStoreGuaranteeAmount(4000), "¥ 40.00")
  assert.equal(formatStoreGuaranteeAmount(0), "¥ 0.00")
  assert.equal(formatStoreGuaranteeAmount(123456), "¥ 1234.56")
})

test("pending and partially unknown amounts remain pending and are never rendered as zero", () => {
  const pending = parseStoreGuarantee(payload({
    status: "PENDING_REVIEW", label: "保障信息待核",
    amount_due_fen: null, net_received_fen: null, amount_remaining_fen: null,
  }))
  assert.equal(storeGuaranteePresentation(pending).label, "保障信息待核")
  assert.equal(formatStoreGuaranteeAmount(null), "待核")
  assert.equal(formatStoreGuaranteeAmount(Number.MAX_SAFE_INTEGER + 1), "待核")
  const markup = html(ready(payload({
    status: "PENDING_REVIEW", label: "保障信息待核",
    amount_due_fen: null, net_received_fen: null, amount_remaining_fen: null,
  })))
  assert.match(markup, /保障信息待核/)
  assert.match(markup, /金额按未知处理/)
  assert.doesNotMatch(markup, /¥ 0\.00/)
  assert.match(markup, /未知金额不按 0 计算/)
})

test("an explicitly configured zero requirement displays zero without turning other nulls into zero", () => {
  const raw = payload({
    status: "NOT_COVERED", label: "无保证金缴纳要求",
    amount_due_fen: 0, net_received_fen: 0, amount_remaining_fen: 0,
  })
  assert.equal(parseStoreGuarantee(raw).amountDueFen, 0)
  const markup = html(ready(raw))
  assert.match(markup, /无保证金缴纳要求/)
  assert.match(markup, /¥ 0\.00/)
  assert.doesNotMatch(markup, /未知金额不按 0 计算/)
})

test("strict public schema rejects missing, unknown and sensitive internal fields without storing secrets", () => {
  const secrets = [
    ["internal_note", "保密合同编号"],
    ["updated_by", "internal-operator"],
    ["business_key", "DEP-2026-0001"],
    ["evidence_sha256", "a".repeat(64)],
    ["evidence_ref", "offline:secret/reference"],
  ] as const
  for (const [field, secret] of secrets) {
    assert.throws(() => parseStoreGuarantee(payload({ [field]: secret })), /非公开字段/)
    const previous = ready()
    const failed = completeStoreGuaranteeLoad(previous, scope, payload({ [field]: secret }))
    assert.equal(failed.stale, true)
    assert.equal(failed.snapshot?.data.amountDueFen, 10000)
    assert.doesNotMatch(JSON.stringify(failed), new RegExp(secret))
  }
  const missing = payload() as Record<string, unknown>
  delete missing.net_received_fen
  assert.throws(() => parseStoreGuarantee(missing), /缺失或非公开字段/)
})

test("rejects unsupported statuses, source labels, currencies and evidence bases", () => {
  for (const raw of [
    payload({ status: "UNKNOWN" }),
    payload({ status: "toString" }),
    payload({ label: "平台担保，已赔付" }),
    payload({ currency: "CNY " }),
    payload({ currency: "USD" }),
    payload({ record_basis: "BANK_CONFIRMED" }),
    payload({ record_basis: null }),
  ]) {
    assert.throws(() => parseStoreGuarantee(raw))
  }
})

test("rejects unsafe amounts, inconsistent net conservation and impossible status claims", () => {
  for (const value of [true, "10000", -1, 1.5, Number.MAX_SAFE_INTEGER + 1, undefined]) {
    assert.throws(() => parseStoreGuarantee(payload({ amount_due_fen: value })))
  }
  for (const raw of [
    payload({ amount_remaining_fen: 1 }),
    payload({ amount_due_fen: 100, net_received_fen: 101, amount_remaining_fen: -1 }),
    payload({ amount_due_fen: 100, net_received_fen: 101, amount_remaining_fen: null }),
    payload({ amount_due_fen: 100, net_received_fen: null, amount_remaining_fen: 101 }),
    payload({ status: "NOT_COVERED", label: "未形成保证金保障" }),
    payload({ status: "PARTIAL", label: "保证金部分登记" }),
    payload({ status: "PENDING_REVIEW", label: "保障信息待核" }),
    payload({ status: "NOT_COVERED", label: "无保证金缴纳要求" }),
    payload({ amount_due_fen: 0, net_received_fen: 0, amount_remaining_fen: 0 }),
  ]) {
    assert.throws(() => parseStoreGuarantee(raw))
  }
})

test("non-pending responses with missing amounts are downgraded to review instead of claiming coverage", () => {
  const raw = payload({ net_received_fen: null, amount_remaining_fen: null })
  const parsed = parseStoreGuarantee(raw)
  const presentation = storeGuaranteePresentation(parsed)
  assert.equal(presentation.label, "保障信息待核")
  assert.equal(presentation.amountsUnknown, true)
  const markup = html(ready(raw))
  assert.match(markup, /保障信息待核/)
  assert.match(markup, /已约定保证金/)
  assert.match(markup, /¥ 100\.00/)
  assert.match(markup, /已收净额（凭证登记）/)
  assert.doesNotMatch(markup, /保证金已按合同登记足额/)
  assert.doesNotMatch(markup, /已收净额（凭证登记）[\s\S]*?¥ 0\.00[\s\S]*?待补登记金额/)
})

test("store and session scope switches clear old snapshots and ignore late responses", () => {
  const first = ready()
  const nextScope = storeGuaranteeScope({ id: 2, slug: "xinan" }, 2)
  const switched = beginStoreGuaranteeLoad(first, nextScope)
  assert.equal(switched.snapshot, null)
  assert.equal(switched.scope, nextScope)
  assert.equal(completeStoreGuaranteeLoad(switched, scope, covered), switched)
  assert.equal(failStoreGuaranteeLoad(switched, scope, new Error("late")), switched)
  assert.notEqual(storeGuaranteeScope({ id: 1, slug: "shenglang" }, 1),
    storeGuaranteeScope({ id: 1, slug: "shenglang" }, 2))
})

test("refresh failure preserves only the same-store snapshot as stale and a later success clears staleness", () => {
  const first = ready()
  const refreshing = beginStoreGuaranteeLoad(first, scope)
  assert.equal(refreshing.snapshot?.data.amountDueFen, 10000)
  assert.equal(refreshing.loading, true)
  const failed = failStoreGuaranteeLoad(refreshing, scope, Object.assign(new Error("offline"), { status: 503 }))
  assert.equal(failed.stale, true)
  assert.equal(failed.snapshot?.data.amountDueFen, 10000)
  const recovered = completeStoreGuaranteeLoad(failed, scope, payload({ amount_due_fen: 20000, net_received_fen: 20000 }))
  assert.equal(recovered.stale, false)
  assert.equal(recovered.error, null)
  assert.equal(recovered.snapshot?.data.amountDueFen, 20000)
})

test("public transport checks tenant identity and never requires an authorization session", async () => {
  const controller = new AbortController()
  const calls: unknown[] = []
  const result = await requestPublicStoreGuarantee(async (...args) => {
    calls.push(args)
    return covered
  }, { store: { id: 1, slug: "shenglang" }, resolvedSlug: "shenglang", signal: controller.signal })
  assert.deepEqual(result, covered)
  assert.deepEqual(calls, [["/stores/guarantee/", undefined, { public: true, signal: controller.signal }]])
  for (const [store, resolvedSlug] of [
    [{ id: 0, slug: "shenglang" }, "shenglang"],
    [{ id: 1, slug: "bad slug" }, "bad slug"],
    [{ id: 1, slug: "shenglang" }, "xinan"],
  ] as const) {
    await assert.rejects(requestPublicStoreGuarantee(async () => {
      calls.push("must not request")
    }, { store, resolvedSlug, signal: controller.signal }), /门店身份与当前域名不一致/)
  }
  assert.equal(calls.length, 1)
})

test("initial loading, empty, 401, 403, 404 and network failures have explicit component states", () => {
  const initial = beginStoreGuaranteeLoad(emptyStoreGuaranteeState(scope), scope)
  const loading = html(initial)
  assert.match(loading, /正在读取当前门店保障/)
  assert.match(loading, /disabled=""/)
  assert.doesNotMatch(loading, /¥/)

  const empty = completeStoreGuaranteeLoad(initial, scope, null)
  assert.match(html(empty), /暂未配置或暂无可核对信息/)
  for (const [status, text] of [
    [401, /访问身份待核（401）/],
    [403, /当前入口未能读取门店保障（403）/],
    [404, /当前门店不存在或保障公示不可用/],
    [503, /门店保障暂时无法读取/],
    [0, /状态与金额尚未确认/],
  ] as const) {
    const failed = failStoreGuaranteeLoad(initial, scope, Object.assign(new Error("request failed"), { status }))
    assert.match(html(failed), text)
    assert.match(html(failed), /已约定、已收净额及待补登记金额：待核/)
  }
})

test("covered and partial cards use source-qualified amounts and expose no fake action or promise", () => {
  for (const raw of [covered, payload({
    status: "PARTIAL", label: "保证金部分登记",
    amount_due_fen: 10000, net_received_fen: 4000, amount_remaining_fen: 6000,
  })]) {
    const markup = html(ready(raw))
    assert.match(markup, /已约定保证金/)
    assert.match(markup, /已收净额（凭证登记）/)
    assert.match(markup, /¥ 100\.00/)
    assert.match(markup, /合同与线下收退凭证登记口径/)
    assert.match(markup, /不代表银行到账或支付渠道确认/)
    assert.doesNotMatch(markup, /平台担保|已赔付|银行已到账|闭店|余额返还/)
    assert.doesNotMatch(markup, /<a\s/)
  }
})

test("refresh failure renders the old snapshot as expired without hiding its age or the refresh error", () => {
  const failed = failStoreGuaranteeLoad(ready(), scope, Object.assign(new Error("offline"), { status: 503 }))
  const markup = html(failed)
  assert.match(markup, /data-stale="true"/)
  assert.match(markup, /上次快照已过期/)
  assert.match(markup, /不能视为当前保障状态/)
  assert.match(markup, /门店保障暂时无法读取/)
  assert.match(markup, /¥ 100\.00/)
})

test("refresh in progress keeps the prior snapshot identifiable and disables duplicate refreshes", () => {
  const markup = html(beginStoreGuaranteeLoad(ready(), scope))
  assert.match(markup, /正在刷新/)
  assert.match(markup, /上次成功读取的快照/)
  assert.match(markup, /disabled=""/)
  assert.match(markup, /¥ 100\.00/)
})
