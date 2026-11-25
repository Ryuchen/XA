/// <reference types="node" />
import assert from "node:assert/strict"
import { test } from "node:test"
import {
  PLATFORM_ORDER_MAX_PAGE,
  PLATFORM_ORDER_PAGE_SIZE,
  PlatformOrderContractError,
  platformOrderCanGoNext,
  platformOrderListAvailable,
  buildPlatformOrderQuery,
  canReadPlatformOrderStore,
  hasPlatformOrderReadAccess,
  initialKeyedRead,
  keyedReadReducer,
  orderIdentity,
  parsePlatformOrderDetail,
  parsePlatformOrderPage,
  parsePlatformOrderSearch,
  parsePlatformStoreOptions,
  platformOrderSearchWithFilters,
  type PlatformOrderAccess,
  type PlatformOrder,
  type PlatformOrderFilters,
  type PlatformOrderPage,
} from "./platform-orders.ts"

function order(): PlatformOrder {
  return {
    id: "12",
    store_slug: "shenglang",
    store_name: "声浪电竞",
    order_no: "XA202611250001",
    service_name: "双排陪玩",
    customer_name: "老板甲",
    provider_names: ["陪玩甲", "陪玩乙"],
    currency_name: "浪花",
    version: "2026-11-25T10:30:00+00:00",
    allowed_actions: [],
    status: "IN_SERVICE",
    payment_status: "PAID",
    payment_display: null,
    dispatch_route: "STAFF",
    order_source: "STAFF_MANUAL",
    requires_boss_selection: false,
    pending_deadline_expired: false,
    aftersale_status: null,
    settlement_status: "WAITING",
    created_at: "2026-11-25T10:00:00+00:00",
    grabbed_at: "2026-11-25T10:05:00+00:00",
    started_at: null,
    in_service_at: "2026-11-25T10:10:00+00:00",
    completed_at: null,
    cancelled_at: null,
    updated_at: "2026-11-25T10:30:00+00:00",
  }
}

function page(): PlatformOrderPage {
  return {
    results: [order()],
    total: 1,
    complete: true,
    generated_at: "2026-11-25T10:31:00+00:00",
    missing_stores: [],
  }
}

function access(role: string, capability = true): PlatformOrderAccess {
  return {
    grants: [{ role, scope_slug: "shenglang" }],
    capabilities: capability ? ["PLATFORM_ORDERS_V1"] : [],
  }
}

test("安全订单投影和完整列表信封通过严格校验", () => {
  const parsed = parsePlatformOrderPage(page(), {
    pageSize: PLATFORM_ORDER_PAGE_SIZE,
  })
  assert.equal(parsed.results.length, 1)
  assert.equal(parsed.results[0].allowed_actions.length, 0)
  assert.equal(orderIdentity(parsed.results[0]), "shenglang/12")
  assert.equal(parsed.total, 1)
  assert.equal(parsed.complete, true)
})

test("partial 列表保留已读取总数并明确缺失门店", () => {
  const value = {
    ...page(),
    total: 9,
    complete: false,
    missing_stores: ["xinan"],
  }
  const parsed = parsePlatformOrderPage(value, { pageSize: 20 })
  assert.equal(parsed.total, 9)
  assert.equal(parsed.complete, false)
  assert.deepEqual(parsed.missing_stores, ["xinan"])
})

test("部分缺失的空页保持未读取，完整的空页才显示正常空态", () => {
  assert.equal(platformOrderListAvailable(null), false)
  assert.equal(platformOrderListAvailable(page()), true)
  assert.equal(
    platformOrderListAvailable({ ...page(), results: [], total: 0 }),
    true
  )
  assert.equal(
    platformOrderListAvailable({
      ...page(),
      results: [],
      total: 0,
      complete: false,
      missing_stores: ["xinan"],
    }),
    false
  )
  assert.equal(
    platformOrderListAvailable({
      ...page(),
      complete: false,
      missing_stores: ["xinan"],
    }),
    true
  )
})

test("分页遵守服务端最大可读页，最大页禁用下一页", () => {
  assert.equal(platformOrderCanGoNext(0, 1), false)
  assert.equal(platformOrderCanGoNext(41, 1), true)
  assert.equal(platformOrderCanGoNext(41, 3), false)
  assert.equal(platformOrderCanGoNext(1000040, 5000), true)
  assert.equal(platformOrderCanGoNext(1000040, 5001), false)
  assert.equal(platformOrderCanGoNext(1000040, 5002), false)
  assert.equal(PLATFORM_ORDER_MAX_PAGE, 5001)
})

test("列表拒绝缺失字段、额外敏感字段、非空动作和未知枚举", () => {
  const invalid: unknown[] = [
    { ...page(), results: {} },
    { ...page(), total: "1" },
    { ...page(), complete: false },
    { ...page(), missing_stores: ["bad_slug"] },
    { ...page(), results: [{ ...order(), amount: 100 }] },
    { ...page(), results: [{ ...order(), customer_phone: "13800000000" }] },
    { ...page(), results: [{ ...order(), internal_remark: "内部备注" }] },
    { ...page(), results: [{ ...order(), allowed_actions: ["ASSIGN"] }] },
    { ...page(), results: [{ ...order(), status: "UNKNOWN" }] },
    { ...page(), results: [{ ...order(), order_source: "UNKNOWN" }] },
    { ...page(), results: [{ ...order(), created_at: "yesterday" }] },
    { ...page(), results: [{ ...order(), provider_names: [1] }] },
    { ...page(), results: [{ ...order(), payment_display: { kind: "LIVE" } }] },
  ]
  const missing = order() as Record<string, unknown>
  delete missing.service_name
  invalid.push({ ...page(), results: [missing] })
  for (const value of invalid) {
    assert.throws(
      () => parsePlatformOrderPage(value, { pageSize: 20 }),
      PlatformOrderContractError
    )
  }
})

test("指定门店的列表不能混入其他门店订单", () => {
  assert.throws(
    () => parsePlatformOrderPage(page(), { store: "xinan", pageSize: 20 }),
    PlatformOrderContractError
  )
})

test("详情只接受安全 events，并拒绝原因、联系方式等额外字段", () => {
  const detail = {
    ...order(),
    events: [
      {
        id: "3",
        at: "2026-11-25T10:05:00+00:00",
        title: "陪玩接单",
        actor: "陪玩甲",
      },
    ],
  }
  const parsed = parsePlatformOrderDetail(detail)
  assert.deepEqual(parsed.events, detail.events)
  for (const event of [
    { ...detail.events[0], reason: "内部原因" },
    { ...detail.events[0], actor: { phone: "13800000000" } },
    { ...detail.events[0], at: "not-a-date" },
  ]) {
    assert.throws(
      () => parsePlatformOrderDetail({ ...detail, events: [event] }),
      PlatformOrderContractError
    )
  }
  assert.throws(
    () => parsePlatformOrderDetail(order()),
    PlatformOrderContractError
  )
})

test("筛选提交重置页码，只发送白名单字段并编码关键词", () => {
  const draft: PlatformOrderFilters = {
    store: "shenglang",
    source: "BOSS_H5",
    status: "PENDING",
    payment: "UNPAID",
    keyword: "  老板 A&B  ",
  }
  assert.equal(
    platformOrderSearchWithFilters("page=4&store=shenglang", draft),
    "page=1&page_size=20&store=shenglang&source=BOSS_H5&status=PENDING&payment=UNPAID&keyword=%E8%80%81%E6%9D%BF+A%26B"
  )
  assert.equal(
    buildPlatformOrderQuery(
      { store: "", source: "", status: "", payment: "", keyword: "" },
      2
    ),
    "page=2&page_size=20"
  )
})

test("未知、重复和损坏的筛选参数 fail closed", () => {
  for (const search of [
    "page=0",
    "page=1&page=2",
    "source=UNKNOWN",
    "store=bad_slug",
    "keyword=%00",
    "dispatch=quick",
  ]) {
    assert.throws(
      () => parsePlatformOrderSearch(search),
      PlatformOrderContractError
    )
  }
  assert.deepEqual(parsePlatformOrderSearch("page=3&source=GIFT"), {
    page: 3,
    filters: {
      store: "",
      source: "GIFT",
      status: "",
      payment: "",
      keyword: "",
    },
  })
})

test("门店下拉目录拒绝损坏、重复和非法门店值", () => {
  assert.deepEqual(
    parsePlatformStoreOptions([
      { slug: "shenglang", name: "声浪电竞", status: "ACTIVE" },
    ]),
    [{ slug: "shenglang", name: "声浪电竞" }]
  )
  for (const value of [
    {},
    [{ slug: "bad_slug", name: "门店" }],
    [{ slug: "shenglang", name: "" }],
    [
      { slug: "shenglang", name: "甲" },
      { slug: "shenglang", name: "乙" },
    ],
  ])
    assert.throws(
      () => parsePlatformStoreOptions(value),
      PlatformOrderContractError
    )
})

test("能力与三类只读角色共同门禁，财务角色不能读取订单", () => {
  for (const role of ["SUPER_ADMIN", "ORDER_VIEWER", "ORDER_OPERATOR"]) {
    assert.equal(hasPlatformOrderReadAccess(access(role)), true)
    assert.equal(canReadPlatformOrderStore(access(role), "shenglang"), true)
    assert.equal(canReadPlatformOrderStore(access(role), "xinan"), false)
  }
  assert.equal(hasPlatformOrderReadAccess(access("FINANCE")), false)
  assert.equal(hasPlatformOrderReadAccess(access("ORDER_VIEWER", false)), false)
  const global: PlatformOrderAccess = {
    grants: [{ role: "SUPER_ADMIN", scope_slug: "*" }],
    capabilities: ["PLATFORM_ORDERS_V1"],
  }
  assert.equal(canReadPlatformOrderStore(global, "xinan"), true)
})

test("读取状态机覆盖加载、成功、失败和重试，同条件保留快照而换条件清空", () => {
  let state = initialKeyedRead<string>("shenglang:1")
  state = keyedReadReducer(state, { type: "start", key: "shenglang:1" })
  assert.equal(state.phase, "loading")
  state = keyedReadReducer(state, {
    type: "success",
    key: "shenglang:1",
    data: "snapshot",
  })
  assert.equal(state.phase, "ready")
  assert.equal(state.data, "snapshot")
  state = keyedReadReducer(state, { type: "start", key: "shenglang:1" })
  assert.equal(state.phase, "loading")
  assert.equal(state.data, "snapshot")
  state = keyedReadReducer(state, {
    type: "failure",
    key: "shenglang:1",
    error: "offline",
  })
  assert.equal(state.phase, "error")
  assert.equal(state.data, "snapshot")
  assert.equal(state.error, "offline")
  state = keyedReadReducer(state, { type: "start", key: "xinan:1" })
  assert.equal(state.phase, "loading")
  assert.equal(state.data, undefined)
  state = keyedReadReducer(state, {
    type: "failure",
    key: "xinan:1",
    error: "offline",
  })
  assert.equal(state.phase, "error")
  assert.equal(state.data, undefined)
  state = keyedReadReducer(state, { type: "start", key: "xinan:1" })
  assert.equal(state.error, undefined)
})

test("旧门店或旧请求的迟到响应不能覆盖当前状态", () => {
  const ready = { key: "xinan:1", phase: "ready" as const, data: "current" }
  assert.equal(
    keyedReadReducer(ready, {
      type: "success",
      key: "shenglang:1",
      data: "stale",
    }),
    ready
  )
  assert.equal(
    keyedReadReducer(ready, {
      type: "failure",
      key: "shenglang:1",
      error: "stale",
    }),
    ready
  )
})
