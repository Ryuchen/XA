/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { normalizeSettlementBreakdown, normalizeStaffSettlementBreakdown, settlementAmount, settlementMoney } from "./order-settlement-breakdown.ts"

const mainLine = {
  line_id: "main:32",
  kind: "MAIN",
  segment_id: 32,
  provider_assignment_id: 41,
  slot: 1,
  started_at: "2026-11-25T10:00:00+08:00",
  ended_at: "2026-11-25T11:00:00+08:00",
  payment_id: null,
  addon_name: null,
  net_income_raw: 1250,
  main_net_income_raw: 1250,
  addon_net_income_raw: null,
  pending_income_raw: 250,
  frozen_income_raw: 750,
  posted_income_raw: 500,
  settlement_status: "PARTIAL",
  amount_basis: "SEGMENT_SETTLEMENT_MANIFEST",
  freeze_until: "2026-11-26T10:00:00+08:00",
  transactions: [{ id: 90, transaction_no: "TX-90", tx_type: "INCOME", amount_raw: 500, status: "SUCCESS", created_at: "2026-11-25T12:00:00+08:00", scope: "PERSON_MAIN" }],
  unknown_items: [],
}

const addonLine = {
  line_id: "addon:8:32",
  kind: "ADDON",
  segment_id: 32,
  provider_assignment_id: null,
  slot: 1,
  started_at: "2026-11-25T10:00:00+08:00",
  ended_at: "2026-11-25T11:00:00+08:00",
  payment_id: 8,
  addon_name: "加时",
  net_income_raw: 500,
  main_net_income_raw: null,
  addon_net_income_raw: 500,
  pending_income_raw: 0,
  frozen_income_raw: 0,
  posted_income_raw: 500,
  settlement_status: "POSTED",
  amount_basis: "SEGMENT_SETTLEMENT_MANIFEST",
  freeze_until: null,
  transactions: [{ id: 91, transaction_no: "TX-91", tx_type: "INCOME", amount_raw: 500, status: "SUCCESS", created_at: "2026-11-25T12:01:00+08:00", scope: "ADDON" }],
  unknown_items: [],
}

const payload = (): any => ({
  order_id: 3,
  order_no: "SL-3",
  store_slug: "shenglang",
  current_provider: { account_id: 20, account_no: "P20", display_name: "甲" },
  scope: "PROVIDER#20",
  generated_at: "2026-11-25T10:00:00Z",
  status: "PARTIAL",
  main_segments: [mainLine],
  addon_earnings: [addonLine],
  person_settlement: {
    account_id: 20,
    segment_ids: [32],
    main_net_income_raw: 1250,
    addon_net_income_raw: 500,
    net_income_raw: 1750,
    pending_income_raw: 250,
    frozen_income_raw: 750,
    posted_income_raw: 1000,
    settlement_status: "PARTIAL",
    freeze_until: "2026-11-26T10:00:00+08:00",
    income_transaction: mainLine.transactions[0],
    transactions: [...mainLine.transactions, ...addonLine.transactions],
    unknown_items: [],
  },
  totals: {
    main_net_income_raw: 1250,
    addon_net_income_raw: 500,
    net_income_raw: 1750,
    total_income_raw: 1750,
    pending_income_raw: 250,
    frozen_income_raw: 750,
    posted_income_raw: 1000,
    unknown: false,
  },
  transaction_refs: [...mainLine.transactions, ...addonLine.transactions],
  unknown_items: [],
})

test("按陪玩本人实际契约读取主单、逐笔加购、待入账、冻结、已入账和流水", () => {
  const view = normalizeSettlementBreakdown(payload(), 3)
  assert.equal(view.state, "ready")
  assert.equal(view.orderId, 3)
  assert.equal(view.orderNo, "SL-3")
  assert.equal(view.storeSlug, "shenglang")
  assert.equal(view.providerAccountId, "20")
  assert.equal(view.scope, "甲")
  assert.equal(view.status, "PARTIAL")
  assert.equal(view.freezeUntil, "2026-11-26T10:00:00+08:00")
  assert.equal(view.lines.length, 2)
  assert.equal(view.lines[0].kind, "main")
  assert.equal(view.lines[0].lineId, "main:32")
  assert.match(view.lines[0].title, /#32/)
  assert.equal(view.lines[0].pending, 250)
  assert.equal(view.lines[0].amountBasis, "SEGMENT_SETTLEMENT_MANIFEST")
  assert.equal(view.lines[0].transactions[0].key, "TX-90")
  assert.equal(view.lines[0].transactions[0].amountRaw, 500)
  assert.equal(view.lines[1].kind, "addon")
  assert.match(view.lines[1].title, /#8.*加时/)
  assert.equal(view.lines[1].frozen, 0)
  assert.deepEqual(view.totals, {
    mainNet: 1250,
    addonNet: 500,
    net: 1750,
    total: 1750,
    pending: 250,
    frozen: 750,
    posted: 1000,
    unknown: false,
  })
  assert.deepEqual(view.transactionRefs.map(item => item.key), ["TX-90", "TX-91"])
  assert.deepEqual(view.unknownItems, [])
})

test("内部单位只在展示边界换算，不把未知或畸形金额变成零", () => {
  for (const value of [undefined, null, "", "1.5", "1e3", "-1", -1, Number.NaN, Number.POSITIVE_INFINITY, {}, true]) {
    assert.equal(settlementAmount(value), null)
  }
  assert.equal(settlementAmount(0), 0)
  assert.equal(settlementAmount("1250"), 1250)
  assert.equal(settlementMoney(null), "—")
  assert.equal(settlementMoney(1250), "125")
  assert.equal(settlementMoney(1255), "125.5")

  const changed = payload()
  changed.main_segments = [{ ...mainLine, net_income_raw: null, pending_income_raw: 250, frozen_income_raw: undefined, posted_income_raw: 500 }]
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.lines[0].net, null)
  assert.equal(view.lines[0].pending, 250)
  assert.equal(view.lines[0].frozen, null)
  assert.ok(view.unknownItems.some(item => item.message.includes("主单净收益待核对")))
  assert.ok(view.unknownItems.some(item => item.message.includes("冻结收益待核对")))
})

test("明确空清单与不支持响应分开，空清单只保留服务端合计", () => {
  const changed = payload()
  changed.main_segments = []
  changed.addon_earnings = []
  changed.totals = { main_net_income_raw: 0, addon_net_income_raw: 0, net_income_raw: 0, total_income_raw: 0, pending_income_raw: 0, frozen_income_raw: 0, posted_income_raw: 0, unknown: false }
  changed.status = "PENDING"
  const empty = normalizeSettlementBreakdown(changed)
  assert.equal(empty.state, "empty")
  assert.deepEqual(empty.lines, [])
  assert.equal(empty.totals.total, 0)

  for (const value of [
    null,
    [],
    {},
    { ...payload(), main_segments: undefined },
    { ...payload(), addon_earnings: {} },
    { ...payload(), totals: null },
    { ...payload(), current_provider: undefined },
    { ...payload(), person_settlement: undefined },
    { order_id: 3, segments: [], addon_earnings: [] },
  ]) {
    const view = normalizeSettlementBreakdown(value)
    assert.equal(view.state, "unsupported")
    assert.equal(view.totals.total, null)
    assert.match(view.unknownItems[0].message, /待核对/)
  }
})

test("错单与错误陪玩范围分别停止展示，不把员工权限当成陪玩本人权限", () => {
  const mismatch = normalizeSettlementBreakdown(payload(), 4)
  assert.equal(mismatch.state, "mismatch")
  assert.equal(mismatch.orderId, 3)
  assert.deepEqual(mismatch.lines, [])
  assert.equal(mismatch.totals.total, null)
  assert.match(mismatch.unknownItems[0].message, /其他订单/)

  for (const changed of [
    { ...payload(), scope: "STAFF#20" },
    { ...payload(), current_provider: { account_id: 21, account_no: "P21", display_name: "乙" } },
    { ...payload(), person_settlement: { ...payload().person_settlement, account_id: 21 } },
  ]) {
    const view = normalizeSettlementBreakdown(changed, 3)
    assert.equal(view.state, "unsupported")
    assert.deepEqual(view.lines, [])
    assert.equal(view.totals.total, null)
  }
})

test("服务端未知项保留代码、原因和付款或履约段范围并去重", () => {
  const changed = payload()
  changed.status = "REVIEW"
  changed.totals.unknown = true
  changed.unknown_items = [{ code: "ORDINARY_BREAKDOWN_UNSUPPORTED", message: "普通订单含退款、加购、补贴或多段履约，逐段净收益待核对", segment_id: null, payment_id: null }]
  changed.main_segments = [{ ...mainLine, settlement_status: "REVIEW", unknown_items: [{ code: "SEGMENT_POSTED_UNKNOWN", message: "本人多段共用一笔主单入账流水，逐段到账金额待核对", segment_id: 32, payment_id: null }] }]
  changed.unknown_items.push(changed.main_segments[0].unknown_items[0])
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.status, "REVIEW")
  assert.equal(view.totals.unknown, true)
  assert.deepEqual(view.unknownItems.map(item => item.code), ["ORDINARY_BREAKDOWN_UNSUPPORTED", "SEGMENT_POSTED_UNKNOWN"])
  assert.equal(view.unknownItems[1].segmentId, "32")
  assert.equal(view.lines[0].unknownItems[0].code, "SEGMENT_POSTED_UNKNOWN")
})

test("未知元数据缺失或非数组时停止展示金额", () => {
  const person = payload().person_settlement
  for (const value of [
    { ...payload(), unknown_items: {} },
    { ...payload(), unknown_items: undefined },
    { ...payload(), main_segments: [{ ...mainLine, unknown_items: {} }] },
    { ...payload(), addon_earnings: [{ ...addonLine, unknown_items: {} }] },
    { ...payload(), person_settlement: { ...person, unknown_items: {} } },
  ]) {
    const view = normalizeSettlementBreakdown(value, 3)
    assert.equal(view.state, "unsupported")
    assert.deepEqual(view.lines, [])
    assert.equal(view.totals.total, null)
    assert.match(view.unknownItems[0].message, /未知|待核对/)
  }

  const provider = payload()
  const staff = normalizeStaffSettlementBreakdown({
    order_id: 3,
    order_no: "SL-3",
    store_slug: "shenglang",
    scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z",
    providers: [provider],
    unknown_items: {},
  }, 3)
  assert.equal(staff.state, "unsupported")
  assert.deepEqual(staff.providers, [])
  assert.match(staff.unknownItems[0].message, /未知|待核对/)
})

test("合计未知标记只接受布尔值，字符串 true 不降级为 false", () => {
  for (const value of ["true", "false", 1, 0, null, undefined]) {
    const changed = payload()
    changed.totals.unknown = value
    const view = normalizeSettlementBreakdown(changed, 3)
    assert.equal(view.state, "unsupported")
    assert.deepEqual(view.lines, [])
    assert.equal(view.totals.total, null)
    assert.match(view.unknownItems[0].message, /未知|待核对/)
  }
})

test("多段共用一笔入账时保留本人合计，但逐段到账和冻结继续未知", () => {
  const changed = payload()
  changed.status = "REVIEW"
  changed.addon_earnings = []
  changed.main_segments = [
    { ...mainLine, line_id: "main:32", posted_income_raw: null, frozen_income_raw: null, pending_income_raw: null, settlement_status: "REVIEW", transactions: [], unknown_items: [{ code: "SEGMENT_POSTED_UNKNOWN", message: "逐段到账金额待核对", segment_id: 32, payment_id: null }] },
    { ...mainLine, line_id: "main:33", segment_id: 33, posted_income_raw: null, frozen_income_raw: null, pending_income_raw: null, settlement_status: "REVIEW", transactions: [], unknown_items: [{ code: "SEGMENT_FROZEN_UNKNOWN", message: "逐段冻结金额待核对", segment_id: 33, payment_id: null }] },
  ]
  changed.totals.posted_income_raw = 6400
  changed.person_settlement.posted_income_raw = 6400
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.lines.length, 2)
  assert.ok(view.lines.every(item => item.posted === null && item.frozen === null))
  assert.equal(view.totals.posted, 6400)
  assert.deepEqual(view.unknownItems.map(item => item.code), ["SEGMENT_POSTED_UNKNOWN", "SEGMENT_FROZEN_UNKNOWN"])
})

test("只使用服务端合计，不在前端从分段相加补出总额或掩盖矛盾", () => {
  const changed = payload()
  changed.totals.main_net_income_raw = 1249
  changed.totals.net_income_raw = 1749
  changed.totals.total_income_raw = 1748
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.totals.mainNet, 1249)
  assert.equal(view.totals.net, 1749)
  assert.equal(view.totals.total, 1748)
  assert.equal(view.totals.addonNet, 500)

  const missing = payload()
  missing.totals.total_income_raw = null
  const unknown = normalizeSettlementBreakdown(missing)
  assert.equal(unknown.totals.total, null)
  assert.ok(unknown.unknownItems.some(item => item.message.includes("收益总额待核对")))
})

test("坏行、未知状态和缺失流水编号不会被静默降级成可核对金额", () => {
  const malformed = payload()
  malformed.main_segments = [null]
  const stopped = normalizeSettlementBreakdown(malformed)
  assert.equal(stopped.state, "unsupported")
  assert.deepEqual(stopped.lines, [])
  assert.equal(stopped.totals.total, null)

  const changed = payload()
  changed.status = "SETTLED"
  changed.main_segments = [{ ...mainLine, settlement_status: "SETTLED", amount_basis: "LEGACY", transactions: [{ tx_type: "INCOME" }] }]
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.status, null)
  assert.equal(view.lines[0].status, null)
  assert.equal(view.lines[0].amountBasis, null)
  assert.deepEqual(view.lines[0].transactions, [])
  assert.ok(view.unknownItems.some(item => item.message.includes("结算状态待核对")))
  assert.ok(view.unknownItems.some(item => item.message.includes("金额依据待核对")))
  assert.ok(view.unknownItems.some(item => item.message.includes("流水引用待核对")))
})

test("普通订单使用已核验结算依据，未知原因不为空时不额外伪造零或原因", () => {
  const changed = payload()
  changed.addon_earnings = []
  changed.main_segments = [{ ...mainLine, line_id: "main:assignment:41", segment_id: null, amount_basis: "VERIFIED_ORDINARY_SETTLEMENT", pending_income_raw: 0, frozen_income_raw: 0, posted_income_raw: 1250, settlement_status: "POSTED" }]
  changed.totals.addon_net_income_raw = 0
  changed.totals.net_income_raw = 1250
  changed.totals.total_income_raw = 1250
  changed.totals.pending_income_raw = 0
  changed.totals.frozen_income_raw = 0
  changed.totals.posted_income_raw = 1250
  const view = normalizeSettlementBreakdown(changed)
  assert.equal(view.lines[0].segmentId, null)
  assert.equal(view.lines[0].amountBasis, "VERIFIED_ORDINARY_SETTLEMENT")
  assert.equal(view.totals.addonNet, 0)
  assert.deepEqual(view.unknownItems, [])
})

test("员工订单范围逐陪玩保留本人合计，不在前端合并跨人收益", () => {
  const first = payload()
  const second = payload()
  second.current_provider = { account_id: 21, account_no: "P21", display_name: "乙" }
  second.scope = "PROVIDER#21"
  second.person_settlement.account_id = 21
  const staff = normalizeStaffSettlementBreakdown({
    order_id: 3,
    order_no: "SL-3",
    store_slug: "shenglang",
    scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z",
    providers: [first, second],
    unknown_items: [{ code: "ORDER_REVIEW", message: "整单仍有待核对项" }],
  }, 3)
  assert.equal(staff.state, "ready")
  assert.deepEqual(staff.providers.map(item => item.providerAccountId), ["20", "21"])
  assert.deepEqual(staff.providers.map(item => item.scope), ["甲", "乙"])
  assert.deepEqual(staff.providers.map(item => item.totals.total), [1750, 1750])
  assert.equal(staff.unknownItems[0].code, "ORDER_REVIEW")
  assert.ok(staff.providers.every(item => !("providers" in item)))
})

test("员工范围拒绝错单、错误 scope、重复陪玩和坏子项，不展示部分金额", () => {
  const provider = payload()
  const envelope = (providers: unknown[] = [provider]): any => ({
    order_id: 3,
    order_no: "SL-3",
    store_slug: "shenglang",
    scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z",
    providers,
    unknown_items: [],
  })
  assert.equal(normalizeStaffSettlementBreakdown(envelope(), 4).state, "mismatch")
  for (const value of [
    null,
    [],
    {},
    { ...envelope(), scope: "PROVIDER#20" },
    { ...envelope(), providers: {} },
    { ...envelope(), order_id: 0 },
    envelope([provider, provider]),
    envelope([{ ...provider, order_id: 4 }]),
    envelope([{ ...provider, main_segments: null }]),
    envelope([{ ...provider, scope: "STAFF#20" }]),
  ]) {
    const view = normalizeStaffSettlementBreakdown(value, 3)
    assert.equal(view.state, "unsupported")
    assert.deepEqual(view.providers, [])
    assert.equal(view.unknownItems.length, 1)
  }
})

test("员工逐段收益要求外层订单身份并逐陪玩严格匹配门店和订单号", () => {
  const provider = payload()
  const envelope = (providers: unknown[] = [provider]): any => ({
    order_id: 3,
    order_no: "SL-3",
    store_slug: "shenglang",
    scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z",
    providers,
    unknown_items: [],
  })

  for (const value of [
    { ...envelope(), order_no: undefined },
    { ...envelope(), store_slug: undefined },
    envelope([{ ...provider, store_slug: "xingan" }]),
    envelope([{ ...provider, order_no: "SL-4" }]),
    envelope([{ ...provider, store_slug: undefined }]),
    envelope([{ ...provider, order_no: undefined }]),
    envelope([{ ...provider, store_slug: " shenglang " }]),
    envelope([{ ...provider, order_no: " SL-3 " }]),
  ]) {
    const view = normalizeStaffSettlementBreakdown(value, 3)
    assert.equal(view.state, "unsupported")
    assert.deepEqual(view.providers, [])
    assert.match(view.unknownItems[0].message, /身份|门店|订单号|结构/)
  }
})

test("合法空未知清单和 false 标记保持可展示", () => {
  const view = normalizeSettlementBreakdown(payload(), 3)
  assert.equal(view.state, "ready")
  assert.equal(view.totals.unknown, false)
  assert.deepEqual(view.unknownItems, [])
  assert.ok(view.lines.every(item => item.unknownItems.length === 0))

  const staff = normalizeStaffSettlementBreakdown({
    order_id: 3,
    order_no: "SL-3",
    store_slug: "shenglang",
    scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z",
    providers: [payload()],
    unknown_items: [],
  }, 3)
  assert.equal(staff.state, "ready")
  assert.deepEqual(staff.unknownItems, [])
})

test("员工空清单和陪玩未知项不会补出零收益或隐藏原因", () => {
  const empty = payload()
  empty.main_segments = []
  empty.addon_earnings = []
  empty.totals = { main_net_income_raw: 0, addon_net_income_raw: 0, net_income_raw: 0, total_income_raw: 0, pending_income_raw: 0, frozen_income_raw: 0, posted_income_raw: 0, unknown: false }
  const staff = normalizeStaffSettlementBreakdown({
    order_id: 3, order_no: "SL-3", store_slug: "shenglang", scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z", providers: [empty], unknown_items: [],
  }, 3)
  assert.equal(staff.state, "ready")
  assert.equal(staff.providers[0].state, "empty")
  assert.equal(staff.providers[0].totals.total, 0)

  const unknown = payload()
  unknown.totals.total_income_raw = null
  unknown.unknown_items = [{ code: "REVIEW_REQUIRED", message: "服务端收益待核对", segment_id: null, payment_id: null }]
  const changed = normalizeStaffSettlementBreakdown({
    order_id: 3, order_no: "SL-3", store_slug: "shenglang", scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z", providers: [unknown], unknown_items: [],
  }, 3)
  assert.equal(changed.providers[0].totals.total, null)
  assert.ok(changed.unknownItems.some(item => item.code === "REVIEW_REQUIRED"))

  const none = normalizeStaffSettlementBreakdown({
    order_id: 3, order_no: "SL-3", store_slug: "shenglang", scope: "STAFF_ORDER",
    generated_at: "2026-11-25T10:00:00Z", providers: [],
    unknown_items: [{ code: "PROVIDER_ACCOUNT_UNKNOWN", message: "历史陪玩待核对", segment_id: null, payment_id: null }],
  }, 3)
  assert.equal(none.state, "empty")
  assert.deepEqual(none.providers, [])
  assert.equal(none.unknownItems[0].code, "PROVIDER_ACCOUNT_UNKNOWN")
})
