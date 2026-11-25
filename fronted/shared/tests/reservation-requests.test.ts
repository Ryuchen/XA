import test from "node:test"
import assert from "node:assert/strict"
import {
  createReservationRequest,
  executeReservationRequest,
  readReservationRequest,
  reservationRequestFailure,
  reservationRequestNotice,
  reservationRequestStorageKey,
  type ReservationRequestIntent,
  type ReservationRequestStorage,
} from "../src/reservation-requests.ts"

function memoryStorage() {
  const values = new Map<string, string>()
  const storage: ReservationRequestStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value) },
    removeItem: (key) => { values.delete(key) },
  }
  return { storage, values }
}

function apiFailure(message: string, status: number, errorCode = "") {
  return Object.assign(new Error(message), {
    status,
    code: status,
    errorCode,
    body: { code: status, msg: message, ...(errorCode ? { errorCode } : {}) },
  })
}

function fixture() {
  const { storage, values } = memoryStorage()
  const store = "store-a"
  const key = reservationRequestStorageKey("provider", 9, 7, store)
  const request = createReservationRequest({
    role: "provider",
    accountId: 9,
    action: "confirm",
    reservationId: 7,
    payload: { reason: "" },
    decisionKey: "confirm-original",
    store,
  })
  return { storage, values, store, key, request }
}

const accepted = (result: { id: number }) => result.id === 7

test("writes the original decision key before POST and same content adopts it after a lost reply", async () => {
  const { storage, key, request } = fixture()
  const attempts: Record<string, unknown>[] = []
  await assert.rejects(executeReservationRequest({
    storage,
    key,
    request,
    post: async (path, payload) => {
      assert.equal(path, "/orders/reservations/7/confirm/")
      assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")
      attempts.push(payload)
      throw apiFailure("回包丢失", 0)
    },
    acceptsResult: accepted,
  }))
  assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")

  const result = await executeReservationRequest({
    storage,
    key,
    request: { ...request, decisionKey: "a-new-key-must-not-replace-the-original" },
    post: async (_path, payload) => {
      attempts.push(payload)
      return { id: 7, status: "CONFIRMED" }
    },
    acceptsResult: accepted,
  })
  assert.equal(result.status, "CONFIRMED")
  assert.deepEqual(attempts, [
    { reason: "", decision_key: "confirm-original", client_request_id: "confirm-original" },
    { reason: "", decision_key: "confirm-original", client_request_id: "confirm-original" },
  ])
  assert.deepEqual(readReservationRequest(storage, key), { intent: null, error: "" })
})

test("a saved key cannot be reused for another action, reservation, reason, seat, or target provider", async () => {
  const { storage, store } = memoryStorage()
  const key = reservationRequestStorageKey("boss", 3, 7, store)
  const request = createReservationRequest({
    role: "boss",
    accountId: 3,
    action: "replace_provider",
    reservationId: 7,
    seatNumber: 1,
    targetProviderAccountId: 11,
    payload: { seat_number: 1, provider_account_id: 11 },
    decisionKey: "replace-original",
    store,
  })
  let posts = 0
  await assert.rejects(executeReservationRequest({
    storage,
    key,
    request,
    post: async () => { posts++; throw apiFailure("网络中断", 0) },
  }))
  const variants: ReservationRequestIntent[] = [
    createReservationRequest({
      role: "boss", accountId: 3, action: "cancel", reservationId: 7,
      payload: { reason: "改变动作" }, decisionKey: "replace-original", store,
    }),
    createReservationRequest({
      role: "boss", accountId: 3, action: "replace_provider", reservationId: 8,
      seatNumber: 1, targetProviderAccountId: 11,
      payload: { seat_number: 1, provider_account_id: 11 }, decisionKey: "replace-original", store,
    }),
    createReservationRequest({
      role: "boss", accountId: 3, action: "replace_provider", reservationId: 7,
      seatNumber: 2, targetProviderAccountId: 11,
      payload: { seat_number: 2, provider_account_id: 11 }, decisionKey: "replace-original", store,
    }),
    createReservationRequest({
      role: "boss", accountId: 3, action: "replace_provider", reservationId: 7,
      seatNumber: 1, targetProviderAccountId: 12,
      payload: { seat_number: 1, provider_account_id: 12 }, decisionKey: "replace-original", store,
    }),
    { ...request, payload: { seat_number: 1, provider_account_id: 11, reason: "异内容" } },
  ]
  for (const variant of variants) {
    await assert.rejects(
      executeReservationRequest({ storage, key, request: variant, post: async () => { posts++ } }),
      (error: { errorCode?: string }) => error.errorCode === "RESERVATION_REQUEST_CONFLICT",
    )
  }
  assert.equal(posts, 1)
  const saved = readReservationRequest(storage, key).intent
  assert.equal(saved?.decisionKey, "replace-original")
  assert.deepEqual(saved?.payload, request.payload)
})

test("network errors, 503, 408 and rate limits remain unknown and retryable with the original key", async () => {
  for (const error of [
    apiFailure("网络中断", 0),
    apiFailure("原预约决策回执仍待取上界", 503),
    apiFailure("请求超时", 408),
    apiFailure("请求较多", 429),
  ]) {
    const { storage, key, request } = fixture()
    await assert.rejects(executeReservationRequest({
      storage, key, request,
      post: async () => { throw error },
      acceptsResult: accepted,
    }))
    const saved = readReservationRequest(storage, key).intent
    assert.equal(saved?.decisionKey, "confirm-original")
    assert.equal(saved?.review, undefined)
    assert.equal(saved?.blocked, undefined)
    const notice = reservationRequestNotice(error)
    assert.equal(notice.kind, "unknown")
    assert.equal(notice.title, "预约结果未知")
    assert.equal(notice.retryable, true)
  }
})

test("409 REVIEW preserves and locks the original key instead of allowing a changed retry", async () => {
  for (const message of [
    "预约提交跨过截止或证据无效，已登记 REVIEW",
    "预约截止时间或时钟证据需核对",
    "原 decision_key 对应的预约决策内容不同",
  ]) {
    const { storage, key, request } = fixture()
    let posts = 0
    await assert.rejects(executeReservationRequest({
      storage,
      key,
      request,
      post: async () => { posts++; throw apiFailure(message, 409) },
      acceptsResult: accepted,
    }))
    const saved = readReservationRequest(storage, key).intent
    assert.equal(saved?.decisionKey, "confirm-original")
    assert.equal(saved?.review, true)
    assert.equal(reservationRequestFailure(apiFailure(message, 409)).kind, "review")
    await assert.rejects(
      executeReservationRequest({
        storage,
        key,
        request: { ...request, decisionKey: "must-not-replace-review" },
        post: async () => { posts++; return { id: 7 } },
        acceptsResult: accepted,
      }),
      (error: { errorCode?: string }) => error.errorCode === "RESERVATION_CLOCK_REVIEW",
    )
    assert.equal(posts, 1)
    assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")
  }
})

test("clock AFTER is shown as expired, clears the completed decision and never replays it", async () => {
  const { storage, key, request } = fixture()
  const keys: string[] = []
  await assert.rejects(executeReservationRequest({
    storage,
    key,
    request,
    post: async (_path, payload) => {
      keys.push(String(payload.decision_key))
      throw apiFailure("预约已到达截止时间", 409, "reservation_clock_after")
    },
    acceptsResult: accepted,
  }))
  assert.deepEqual(readReservationRequest(storage, key), { intent: null, error: "" })
  const notice = reservationRequestNotice(apiFailure("预约已到达截止时间", 409))
  assert.equal(notice.kind, "expired")
  assert.equal(notice.title, "预约已到期")
  assert.equal(notice.retryable, false)

  await executeReservationRequest({
    storage,
    key,
    request: { ...request, decisionKey: "later-new-decision" },
    post: async (_path, payload) => { keys.push(String(payload.decision_key)); return { id: 7 } },
    acceptsResult: accepted,
  })
  assert.deepEqual(keys, ["confirm-original", "later-new-decision"])
})

test("a first definite rejection or ordinary 409 may be corrected with a new key", async () => {
  for (const error of [
    apiFailure("原因格式无效", 400),
    apiFailure("该时间段大神已有预约，请另选时间", 409),
  ]) {
    const { storage, key, request } = fixture()
    await assert.rejects(executeReservationRequest({
      storage, key, request,
      post: async () => { throw error },
      acceptsResult: accepted,
    }))
    assert.deepEqual(readReservationRequest(storage, key), { intent: null, error: "" })
    const corrected = { ...request, decisionKey: "corrected-new-key", payload: { reason: "修正后" } }
    await executeReservationRequest({
      storage, key, request: corrected,
      post: async (_path, payload) => {
        assert.equal(payload.decision_key, "corrected-new-key")
        return { id: 7 }
      },
      acceptsResult: accepted,
    })
  }
})

test("a definite rejection during recovery blocks the original credential instead of clearing it", async () => {
  const { storage, key, request } = fixture()
  let posts = 0
  await assert.rejects(executeReservationRequest({
    storage, key, request,
    post: async () => { posts++; throw apiFailure("回包丢失", 0) },
  }))
  await assert.rejects(executeReservationRequest({
    storage, key, request,
    post: async () => { posts++; throw apiFailure("当前账号已无权操作", 400) },
  }))
  const saved = readReservationRequest(storage, key).intent
  assert.equal(saved?.decisionKey, "confirm-original")
  assert.equal(saved?.blocked, true)
  await assert.rejects(executeReservationRequest({
    storage, key, request,
    post: async () => { posts++ },
  }))
  assert.equal(posts, 2)
  assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")
})

test("damaged or unavailable storage fails closed before any POST", async () => {
  const { storage, key, request } = fixture()
  storage.setItem(key, "not-json")
  let posts = 0
  await assert.rejects(executeReservationRequest({
    storage, key, request,
    post: async () => { posts++ },
  }))
  assert.equal(posts, 0)
  assert.notEqual(readReservationRequest(storage, key).error, "")

  const unavailable: ReservationRequestStorage = {
    getItem: () => null,
    setItem: () => { throw new Error("quota") },
    removeItem: () => {},
  }
  await assert.rejects(
    executeReservationRequest({ storage: unavailable, key, request, post: async () => { posts++ } }),
    (error: { errorCode?: string }) => error.errorCode === "RESERVATION_STORAGE_UNAVAILABLE",
  )
  assert.equal(posts, 0)
})

test("an unverifiable success response is unknown and retained until the same key returns the right reservation", async () => {
  const { storage, key, request } = fixture()
  const keys: unknown[] = []
  await assert.rejects(executeReservationRequest({
    storage,
    key,
    request,
    post: async (_path, payload) => { keys.push(payload.decision_key); return { id: 8 } },
    acceptsResult: accepted,
  }), /结果尚未确认/)
  assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")
  const result = await executeReservationRequest({
    storage,
    key,
    request,
    post: async (_path, payload) => { keys.push(payload.decision_key); return { id: 7 } },
    acceptsResult: accepted,
  })
  assert.equal(result.id, 7)
  assert.deepEqual(keys, ["confirm-original", "confirm-original"])
  assert.deepEqual(readReservationRequest(storage, key), { intent: null, error: "" })
})

test("a late successful response never removes a different request written by another tab", async () => {
  const { storage, key, request } = fixture()
  const replacement = {
    ...request,
    action: "reject" as const,
    path: "/orders/reservations/7/reject/",
    payload: { reason: "另一标签页的原决定" },
    decisionKey: "other-tab-original",
  }
  const result = await executeReservationRequest({
    storage,
    key,
    request,
    post: async () => {
      storage.setItem(key, JSON.stringify(replacement))
      return { id: 7 }
    },
    acceptsResult: accepted,
  })
  assert.equal(result.id, 7)
  assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "other-tab-original")
})

test("storage scope isolates stores, accounts, roles and reservations", () => {
  const base = reservationRequestStorageKey("provider", 9, 7, "store-a")
  assert.notEqual(base, reservationRequestStorageKey("provider", 9, 7, "store-b"))
  assert.notEqual(base, reservationRequestStorageKey("provider", 10, 7, "store-a"))
  assert.notEqual(base, reservationRequestStorageKey("boss", 9, 7, "store-a"))
  assert.notEqual(base, reservationRequestStorageKey("provider", 9, 8, "store-a"))
  const { storage, request } = fixture()
  storage.setItem(reservationRequestStorageKey("provider", 9, 8, "store-a"), JSON.stringify(request))
  assert.notEqual(
    readReservationRequest(storage, reservationRequestStorageKey("provider", 9, 8, "store-a")).error,
    "",
  )
})


test("legacy 400 request-id conflicts also preserve the original decision for review", async () => {
  for (const error of [
    apiFailure("请求编号已用于其他操作，请重新核对", 400),
    apiFailure("请求编号冲突", 400, "REQUEST_ID_CONFLICT"),
    apiFailure("原陪玩决定已回执，请勿重复提交", 400),
  ]) {
    const { storage, key, request } = fixture()
    let posts = 0
    await assert.rejects(executeReservationRequest({
      storage,
      key,
      request,
      post: async () => { posts++; throw error },
      acceptsResult: accepted,
    }))
    assert.equal(reservationRequestFailure(error).kind, "review")
    assert.equal(readReservationRequest(storage, key).intent?.decisionKey, "confirm-original")
    assert.equal(readReservationRequest(storage, key).intent?.review, true)
    await assert.rejects(executeReservationRequest({
      storage,
      key,
      request: { ...request, decisionKey: "not-a-fresh-start" },
      post: async () => { posts++; return { id: 7 } },
      acceptsResult: accepted,
    }))
    assert.equal(posts, 1)
  }
})


test("a stale page closure cannot post an old account or reservation intent into a new storage scope", async () => {
  const { storage, request, store } = fixture()
  const wrongReservationKey = reservationRequestStorageKey("provider", 9, 8, store)
  const wrongAccountKey = reservationRequestStorageKey("provider", 10, 7, store)
  let posts = 0
  for (const key of [wrongReservationKey, wrongAccountKey]) {
    await assert.rejects(
      executeReservationRequest({ storage, key, request, post: async () => { posts++ } }),
      (error: { errorCode?: string }) => error.errorCode === "RESERVATION_REQUEST_CONFLICT",
    )
    assert.deepEqual(readReservationRequest(storage, key), { intent: null, error: "" })
  }
  assert.equal(posts, 0)
})
