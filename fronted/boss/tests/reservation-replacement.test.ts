/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { register } from "node:module"
import { createReservationRequest, persistSession, type ReservationRequestSnapshot } from "../../shared/src/index.ts"
import {
  canOpenReservationReplacement,
  parseReservationReplacementCandidateSnapshot,
  readReservationReplacementCandidates,
  reservationReplacementCandidateForTarget,
  reservationReplacementCandidatePath,
  reservationReplacementContextChanged,
  reservationReplacementOriginalName,
  reservationReplacementScopeIsCurrent,
  reservationSeatMatchesReplacement,
  type ReservationLike,
} from "../src/business/reservation-replacement-model.ts"

register("./alias-hooks.mjs", import.meta.url)
const {
  ReservationReplacementCandidateResults,
  ReservationReplacementOriginalProvider,
  ReservationReplacementPanel,
} = await import("../src/business/reservation-replacement.tsx")

const values = new Map<string, string>()
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => { values.set(key, value) },
  removeItem: (key: string) => { values.delete(key) },
} })
Object.defineProperty(globalThis, "location", { configurable: true, value: {
  hostname: "sl.esports.club", host: "sl.esports.club", protocol: "http:", search: "", hash: "",
} })

function reservation(overrides: Record<string, unknown> = {}): ReservationLike {
  return {
    id: 7,
    status: "PENDING",
    required_provider_count: 2,
    allowed_actions: ["cancel", "replace_provider"],
    service: { id: 5, name: "双陪测试" },
    start_time: "2026-11-26T12:00:00.000Z",
    end_time: "2026-11-26T13:00:00.000Z",
    updated_at: "2026-11-25T12:00:00.000Z",
    seats: [
      { number: 1, status: "CONFIRMED", account_id: 11, name: "SL-保留陪玩" },
      { number: 2, status: "VACANT", account_id: null, name: "" },
    ],
    ...overrides,
  }
}

function candidate(id = 13, overrides: Record<string, unknown> = {}) {
  return {
    id,
    account_id: id,
    nickname: `SL-候选${id}`,
    display_name: `SL-候选${id}`,
    avatar: "",
    avatar_url: "",
    gender: "UNKNOWN",
    level_name: "王者",
    rank: "",
    rating: 0,
    rating_count: 0,
    favorable_rate: 0,
    order_count: 0,
    win_rate: 0,
    price_per_hour: 0,
    qualifications: [],
    has_reference_hourly_price: false,
    bio: "",
    city: "",
    status: "AVAILABLE",
    can_order: true,
    is_verified: true,
    voice_card_url: "",
    game_category_ids: [],
    game_category_names: [],
    service_item_ids: [5],
    ...overrides,
  }
}

function candidateResponse(overrides: Record<string, unknown> = {}) {
  const current = reservation()
  return {
    seat_number: 2,
    service_id: 5,
    start_time: current.start_time,
    end_time: current.end_time,
    original_provider: { account_id: 10, display_name: "SL-原陪玩公开名" },
    list: [candidate()],
    ...overrides,
  }
}

function replacementIntent(review = false, blocked = false) {
  return {
    ...createReservationRequest({
      role: "boss",
      accountId: 3,
      action: "replace_provider",
      reservationId: 7,
      seatNumber: 2,
      targetProviderAccountId: 13,
      payload: { seat_number: 2, provider_account_id: 13 },
      decisionKey: "replace-provider-original",
      store: "shenglang",
    }),
    ...(review ? { review: true } : {}),
    ...(blocked ? { blocked: true } : {}),
  }
}

function panelMarkup(
  current: ReservationLike,
  snapshot: ReservationRequestSnapshot = { intent: null, error: "" },
) {
  persistSession("boss", { token: "test", user: { id: 3, role: "customer" } })
  return renderToStaticMarkup(createElement(ReservationReplacementPanel, {
    reservation: current,
    requestKey: "xa:store:shenglang:xa:boss:web:reservation-request:3:7",
    owner: 3,
    snapshot,
    busy: false,
    operationPending: !!snapshot.intent || !!snapshot.error,
    storePrefix: "SL",
    run: async action => action(),
    onSnapshot: () => {},
    onNotice: () => {},
    onRefresh: async () => current,
  }))
}

function apiFailure(message: string, status: number, code = status) {
  return Object.assign(new Error(message), { status, code, body: { code, msg: message } })
}

test("补位入口同时要求真实 allowed_actions、双陪 PENDING 和指定空席", () => {
  assert.equal(canOpenReservationReplacement(reservation(), 2), true)
  assert.equal(canOpenReservationReplacement(reservation(), 1), false)
  assert.equal(canOpenReservationReplacement(reservation({ allowed_actions: ["cancel"] }), 2), false)
  assert.equal(canOpenReservationReplacement(reservation({ status: "CONFIRMED" }), 2), false)
  assert.equal(canOpenReservationReplacement(reservation({ required_provider_count: 1 }), 2), false)
})

test("候选适配器只调用新补位候选路由并返回原预约只读快照", async () => {
  assert.equal(
    reservationReplacementCandidatePath(7, 2),
    "/orders/reservations/7/replacement-candidates/?seat_number=2",
  )
  const paths: string[] = []
  const result = await readReservationReplacementCandidates(async path => {
    paths.push(path)
    return candidateResponse()
  }, reservation(), 2)
  assert.deepEqual(paths, ["/orders/reservations/7/replacement-candidates/?seat_number=2"])
  assert.equal(paths[0].includes("/users/escorts/"), false)
  assert.equal(result.seatNumber, 2)
  assert.equal(result.serviceId, 5)
  assert.equal(result.startTime, reservation().start_time)
  assert.equal(result.endTime, reservation().end_time)
  assert.deepEqual(result.originalProvider, { account_id: 10, display_name: "SL-原陪玩公开名" })
  assert.deepEqual(result.list.map(item => item.account_id), [13])
  assert.equal("token" in result, false)
  await assert.rejects(readReservationReplacementCandidates(async () => {
    throw new Error("不应读取无权限候选")
  }, reservation({ allowed_actions: [] }), 2), /已不允许补位/)
})

test("跨店、账号或登录 revision 切换都会使旧补位作用域失效", () => {
  const scope = {
    reservationId: 7,
    owner: 3,
    store: "shenglang",
    sessionOwner: 3,
    sessionRevision: 9,
    currentStore: "shenglang",
    currentOwner: 3,
    currentRevision: 9,
  }
  assert.equal(reservationReplacementScopeIsCurrent(scope), true)
  assert.equal(reservationReplacementScopeIsCurrent({ ...scope, currentStore: "xinan" }), false)
  assert.equal(reservationReplacementScopeIsCurrent({ ...scope, currentOwner: 4 }), false)
  assert.equal(reservationReplacementScopeIsCurrent({ ...scope, currentRevision: 10 }), false)
  assert.equal(reservationReplacementScopeIsCurrent({ ...scope, reservationId: 0 }), false)
})

test("候选严格使用 snake_case account_id，拒绝错账号、重复、失效和另一席", () => {
  for (const list of [
    [candidate(13, { account_id: 14 })],
    [{ ...candidate(), accountId: 13, account_id: undefined }],
    [candidate(13, { can_order: false })],
    [candidate(), candidate()],
    [candidate(11)],
    [candidate(13, { displayName: " camelCase 不能替代 ", display_name: undefined })],
  ]) {
    assert.throws(() => parseReservationReplacementCandidateSnapshot(
      candidateResponse({ list }), reservation(), 2,
    ), /候选资料无效|快照/)
  }
  assert.throws(() => parseReservationReplacementCandidateSnapshot(
    candidateResponse({ list: {} }), reservation(), 2,
  ), /不能按空列表处理/)
})

test("旧席位历史字段只在尚未取得新快照时作为有限回退", () => {
  assert.equal(reservationReplacementOriginalName({ number: 2, status: "VACANT", name: "" }), "")
  assert.equal(reservationReplacementOriginalName({
    number: 2,
    status: "VACANT",
    previous_provider: { display_name: "SL-原陪玩" },
  }), "SL-原陪玩")
})

test("服务、预约时段或另一席变化会要求重新核对，单纯 refreshed_at 变化不会", () => {
  const before = reservation()
  assert.equal(reservationReplacementContextChanged(before, reservation({ updated_at: "later" }), 2), false)
  assert.equal(reservationReplacementContextChanged(before, reservation({ start_time: "changed" }), 2), true)
  assert.equal(reservationReplacementContextChanged(before, reservation({ service: { id: 6 } }), 2), true)
  const otherChanged = reservation()
  otherChanged.seats = [
    { number: 1, status: "PENDING", account_id: 15, name: "新另一席" },
    { number: 2, status: "VACANT", account_id: null, name: "" },
  ]
  assert.equal(reservationReplacementContextChanged(before, otherChanged, 2), true)
})

test("补位成功响应必须回到同预约、同席位、同目标的 PENDING 或 CONFIRMED", () => {
  const invited = reservation({
    seats: [
      { number: 1, status: "CONFIRMED", account_id: 11, name: "SL-保留陪玩" },
      { number: 2, status: "PENDING", account_id: 13, name: "SL-新陪玩" },
    ],
  })
  assert.equal(reservationSeatMatchesReplacement(invited, 7, 2, 13), true)
  assert.equal(reservationSeatMatchesReplacement(invited, 8, 2, 13), false)
  assert.equal(reservationSeatMatchesReplacement(invited, 7, 1, 13), false)
  assert.equal(reservationSeatMatchesReplacement(invited, 7, 2, 14), false)
  assert.equal(reservationSeatMatchesReplacement(reservation(), 7, 2, 13), false)
})

test("组件仅在允许补位时显示入口，并明确空席、原身份未知和保留另一席", () => {
  const html = panelMarkup(reservation())
  assert.match(html, /重选陪玩/)
  assert.match(html, /当前空席/)
  assert.match(html, /原身份未随空席返回/)
  assert.match(html, /保留陪玩/)
  assert.match(html, /该席保留，不在本次替换范围内/)
  assert.doesNotMatch(html, /目标陪玩账号.*input/)
})

test("组件在无权限或无空席时不提供重选入口", () => {
  assert.doesNotMatch(panelMarkup(reservation({ allowed_actions: ["cancel"] })), /重选陪玩/)
  assert.doesNotMatch(panelMarkup(reservation({
    seats: [
      { number: 1, status: "CONFIRMED", account_id: 11, name: "甲" },
      { number: 2, status: "PENDING", account_id: 12, name: "乙" },
    ],
  })), /重选陪玩/)
})

test("候选组件覆盖加载、读取失败和真实空候选状态", () => {
  const base = {
    candidates: [], selectedId: null, disabled: false, storePrefix: "SL",
    onSelect: () => {}, reload: () => {},
  }
  assert.match(renderToStaticMarkup(createElement(ReservationReplacementCandidateResults, { ...base, loading: true })), /正在读取本店候选陪玩/)
  const failed = renderToStaticMarkup(createElement(ReservationReplacementCandidateResults, { ...base, loading: false, error: "读取失败" }))
  assert.match(failed, /候选陪玩读取失败/)
  assert.match(failed, /重新读取候选/)
  const empty = renderToStaticMarkup(createElement(ReservationReplacementCandidateResults, { ...base, loading: false }))
  assert.match(empty, /暂无可用补位候选/)
  assert.match(empty, /完整预约时段/)
  assert.match(empty, /空列表是真实结果/)
})

test("候选组件复用真实选人卡并以稳定 account_id 选择", () => {
  const html = renderToStaticMarkup(createElement(ReservationReplacementCandidateResults, {
    loading: false,
    candidates: [candidate()],
    selectedId: 13,
    disabled: false,
    storePrefix: "SL",
    onSelect: () => {},
    reload: () => {},
  }))
  assert.match(html, /boss-picker-card selected/)
  assert.match(html, /aria-pressed="true"/)
  assert.match(html, /取消选择 SL-候选13/)
  assert.match(html, /王者/)
})

test("未知结果恢复卡固定预约、席位、目标和 decision_key", () => {
  const html = panelMarkup(reservation(), { intent: replacementIntent(), error: "" })
  assert.match(html, /补位结果未知/)
  assert.match(html, /预约 #7 · 席位 2 · 目标陪玩账号 #13/)
  assert.match(html, /replace-provider-original/)
  assert.match(html, /同号重试原请求/)
  assert.match(html, /disabled="">重选陪玩/)
})

test("REVIEW 和 blocked 均锁定原凭证，不能换号重做", () => {
  for (const intent of [replacementIntent(true), replacementIntent(false, true)]) {
    const html = panelMarkup(reservation(), { intent, error: "" })
    assert.match(html, /补位结果待核对/)
    assert.match(html, /原请求已锁定/)
    assert.doesNotMatch(html, /同号重试原请求/)
    assert.match(html, /replace-provider-original/)
  }
})

test("未来档期快照严格匹配原预约席位、服务和完整起止时间，不接受 camelCase", () => {
  for (const response of [
    candidateResponse({ seat_number: 1 }),
    candidateResponse({ service_id: 6 }),
    candidateResponse({ start_time: "2026-11-26T12:00:00.001Z" }),
    candidateResponse({ end_time: "2026-11-26T14:00:00.000Z" }),
    { ...candidateResponse(), start_time: undefined, startTime: reservation().start_time },
    { ...candidateResponse(), service_id: undefined, serviceId: 5 },
  ]) {
    assert.throws(() => parseReservationReplacementCandidateSnapshot(
      response, reservation(), 2,
    ), /席位或服务|完整起止时间|原预约/)
  }
  const sameInstant = candidateResponse({
    start_time: "2026-11-26T20:00:00+08:00",
    end_time: "2026-11-26T21:00:00+08:00",
  })
  assert.equal(parseReservationReplacementCandidateSnapshot(sameInstant, reservation(), 2).list.length, 1)
})

test("original_provider 有证据展示公开名，显式 null 保持未知且不回退猜测", () => {
  const seat = { number: 2, status: "VACANT", account_id: null, previous_provider_name: "不能覆盖显式未知" }
  const known = parseReservationReplacementCandidateSnapshot(candidateResponse(), reservation(), 2)
  const knownHtml = renderToStaticMarkup(createElement(ReservationReplacementOriginalProvider, {
    seat, snapshot: known, storePrefix: "SL",
  }))
  assert.match(knownHtml, /原陪玩公开名/)
  assert.doesNotMatch(knownHtml, /原身份未随空席返回/)

  const unknown = parseReservationReplacementCandidateSnapshot(candidateResponse({ original_provider: null }), reservation(), 2)
  const unknownHtml = renderToStaticMarkup(createElement(ReservationReplacementOriginalProvider, {
    seat, snapshot: unknown, storePrefix: "SL",
  }))
  assert.match(unknownHtml, /原身份未随空席返回/)
  assert.doesNotMatch(unknownHtml, /不能覆盖显式未知/)
  assert.throws(() => parseReservationReplacementCandidateSnapshot(
    candidateResponse({ original_provider: { account_id: 10, displayName: "错误别名" } }),
    reservation(), 2,
  ), /原陪玩公开资料格式无效/)
})

test("真实空列表与候选消失冲突分开，提交前仅接受仍在同席快照中的目标", () => {
  const empty = parseReservationReplacementCandidateSnapshot(candidateResponse({ list: [] }), reservation(), 2)
  assert.deepEqual(empty.list, [])
  assert.equal(reservationReplacementCandidateForTarget(empty, 13, 2), null)
  const available = parseReservationReplacementCandidateSnapshot(candidateResponse(), reservation(), 2)
  assert.equal(reservationReplacementCandidateForTarget(available, 13, 2)?.account_id, 13)
  assert.equal(reservationReplacementCandidateForTarget(available, 14, 2), null)
  assert.equal(reservationReplacementCandidateForTarget(available, 13, 1), null)
})

test("候选读取 403、404、409 和网络失败分别展示，不混成真实空列表", async () => {
  const cases = [
    { error: apiFailure("仅预约老板本人可读", 403), code: "（403）", title: "无权读取补位候选" },
    { error: apiFailure("预约不存在", 404), code: "（404）", title: "预约或候选不可见" },
    { error: apiFailure("只能读取空出席位", 409), code: "（409）", title: "候选快照与预约状态冲突" },
    { error: apiFailure("网络连接失败", 0), code: "网络读取失败", title: "候选快照暂未读取成功" },
  ]
  for (const item of cases) {
    let message = ""
    await assert.rejects(readReservationReplacementCandidates(async () => {
      throw item.error
    }, reservation(), 2), error => {
      message = (error as Error).message
      return message.includes(item.code)
    })
    const html = renderToStaticMarkup(createElement(ReservationReplacementCandidateResults, {
      loading: false,
      error: message,
      candidates: [],
      selectedId: null,
      disabled: false,
      storePrefix: "SL",
      onSelect: () => {},
      reload: () => {},
    }))
    assert.match(html, new RegExp(item.title))
    assert.doesNotMatch(html, /暂无可用补位候选/)
  }
})
