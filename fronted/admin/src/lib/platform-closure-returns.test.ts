/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import {
  clearStoredClosureReturnIntent,
  closureReturnAllowedActions,
  closureReturnPoints,
  closureReturnStatusPresentation,
  closureReturnsAccess,
  closureReturnsViewMode,
  closureReturnStorageKey,
  closureReturnUnits,
  closureReturnWriteFailure,
  closureReturnWriteMessage,
  createClosureReturnPaymentIntent,
  createClosureReturnPrepareIntent,
  createClosureReturnReleaseIntent,
  parseClosureReturnDetail,
  parseClosureReturnIntent,
  parseClosureReturnList,
  parseClosureReturnPaymentResult,
  parseClosureReturnPrepareResult,
  parseClosureReturnReleaseResult,
  parseClosureReturnRow,
  readStoredClosureReturnIntent,
  recoverClosureReturnWrite,
  sameClosureReturnCase,
  sameClosureReturnScope,
  storeClosureReturnIntent,
  submitClosureReturnWrite,
  type ClosureReturnContext,
  type ClosureReturnDetail,
  type ClosureReturnIntent,
  type ClosureReturnRow,
  type ClosureReturnStorage,
} from "./platform-closure-returns.ts"
import { PlatformNetworkError, PlatformRequestError, type PlatformProfile } from "./platform-session.ts"

const context: ClosureReturnContext = {
  storeId: 2, storeSlug: "shenglang", actorId: "actor-1", sessionId: "session-1",
  scopeSignature: '[{"role":"SUPER_ADMIN","scope_slug":"shenglang"}]',
}
const occurredAt = "2026-11-25T02:00:00.000Z"
const sha = "a".repeat(64)
const base = {
  case_id: null, account_id: 7, business_cycle: 3, account_label: "老板 7",
  status: "READY_TO_PREPARE", wallet_units: 12500, principal_units: 12500,
  gift_units: 0, compensation_units: 0, points_hundredths: 12345,
  frozen_units: 0, refund_fen: 12500, paid_fen: 0, version: 0,
  blocking_reasons: [], updated_at: "2026-11-25T01:00:00.000Z",
}
function row(status: ClosureReturnRow["status"] = "READY_TO_PREPARE", overrides: Record<string, unknown> = {}) {
  const state = status === "FROZEN" ? {
    case_id: "case-frozen-1", wallet_units: 0, frozen_units: 12500, version: 2,
  } : status === "PAID" ? {
    case_id: "case-frozen-1", wallet_units: 0, frozen_units: 0, paid_fen: 12500, version: 3,
  } : status === "RELEASED" ? {
    case_id: "case-frozen-1", wallet_units: 12500, frozen_units: 0, version: 4,
  } : status === "REVIEW_REQUIRED" ? {
    wallet_units: null, principal_units: null, gift_units: null, compensation_units: null,
    points_hundredths: null, frozen_units: null, refund_fen: null, paid_fen: null,
    blocking_reasons: ["RECHARGE_SOURCE_UNKNOWN"],
  } : {}
  return parseClosureReturnRow({ ...base, ...state, status, ...overrides })
}
const ready = row(), frozen = row("FROZEN"), paid = row("PAID"), review = row("REVIEW_REQUIRED"), releasedHistory = row("RELEASED")
function list(rows: ClosureReturnRow[], complete = true, overrides: Record<string, unknown> = {}) {
  return {
    list: rows, total: rows.length, page: 1, page_size: 20, has_more: false,
    summary: {
      complete,
      counts: { READY_TO_PREPARE: 1, REVIEW_REQUIRED: 1, FROZEN: 1, PAID: 1, RELEASED: 1 },
      totals: complete ? {
        wallet_units: 12500, principal_units: 12500, refund_fen: 12500, paid_fen: 12500,
      } : null,
    }, ...overrides,
  }
}
function detail(rowValue: ClosureReturnRow, events: Array<Record<string, unknown>>): ClosureReturnDetail {
  return parseClosureReturnDetail({ ...rowValue, events, channel_status: "NOT_REQUESTED" }, {
    caseId: rowValue.case_id!, accountId: rowValue.account_id, businessCycle: rowValue.business_cycle,
  })
}
function prepareIntent(id = "prepare-request-1", rowValue = ready) {
  return createClosureReturnPrepareIntent(context, rowValue, id) as Extract<ClosureReturnIntent, { operation: "prepare" }>
}
function paymentIntent(id = "payment-request-1", amountFen = 12500) {
  return createClosureReturnPaymentIntent(context, frozen, {
    amountFen, externalReference: "OFFLINE-RETURN-0001", proofSha256: "A".repeat(64), occurredAt,
  }, id) as Extract<ClosureReturnIntent, { operation: "record-payment" }>
}
function releaseIntent(id = "release-request-1") {
  return createClosureReturnReleaseIntent(context, frozen, "原冻结登记有误，核对后解除", id) as Extract<ClosureReturnIntent, { operation: "release" }>
}
function recoveryEvent(intent: ClosureReturnIntent, changes: Record<string, unknown> = {}) {
  const kind = intent.operation === "prepare" ? "PREPARED" : intent.operation === "record-payment" ? "PAYMENT_RECORDED" : "RELEASED"
  const detail = intent.operation === "prepare" ? {
    account_id: intent.accountId, business_cycle: intent.businessCycle,
    principal_units: intent.payload.expected_principal_units, refund_fen: intent.payload.expected_principal_units,
    wallet_before: intent.payload.expected_wallet_units,
  } : intent.operation === "record-payment" ? {
    amount_fen: intent.payload.amount_fen,
    external_reference_sha256: "db8731986ed2c1f4cd536eec6bf361bb8cf6da7afbba3288d430ff0bd79ec198",
    proof_sha256: intent.payload.proof_sha256, occurred_at: "2026-11-25T02:00:00+00:00",
  } : { reason: intent.payload.reason }
  return {
    event_id: "event-1", kind, client_request_id: intent.client_request_id,
    platform_actor_id: intent.actorId, from_status: "READY_TO_PREPARE", to_status: "FROZEN",
    detail: { ...detail, ...changes }, created_at: "2026-11-25T03:00:00+00:00",
  }
}
function prepareReceipt(replayed = false) {
  return { ...frozen, replayed, wallet_balance: 0, wallet_frozen_units: 12500 }
}
function paymentReceipt(replayed = false) {
  return { ...paid, replayed, wallet_balance: 0, wallet_frozen_units: 0, channel_status: "NOT_REQUESTED" }
}
function memoryStorage(): ClosureReturnStorage & { values: Map<string, string> } {
  const values = new Map<string, string>()
  return {
    values,
    getItem(key) { return values.get(key) ?? null },
    setItem(key, value) { values.set(key, value) },
    removeItem(key) { values.delete(key) },
  }
}
function profile(grants: PlatformProfile["grants"]): PlatformProfile {
  return { id: "actor-1", username: "platform", identity_type: "platform", grants, capabilities: ["STORE_MANAGEMENT_V1"] }
}
const unusedFind = async () => { throw new Error("不应按清单查找 prepare case") }
const unusedRead = async () => { throw new Error("不应读取其他 case") }

test("分页清单保留五态、账号周期和 summary.complete，null 金额永远不补零", () => {
  const result = parseClosureReturnList(list([
    ready, { ...review, account_id: 8 }, { ...frozen, account_id: 9 }, { ...paid, account_id: 10 },
    { ...releasedHistory, account_id: 11 },
  ], false), { page: 1, pageSize: 20 })
  assert.deepEqual(result.list.map(item => item.status), ["READY_TO_PREPARE", "REVIEW_REQUIRED", "FROZEN", "PAID", "RELEASED"])
  assert.equal(result.summary.complete, false)
  assert.equal(result.list[1].wallet_units, null)
  assert.equal(result.list[1].refund_fen, null)
  assert.equal(result.summary.totals, null)
  assert.equal(result.list[0].version, 0)
  for (const value of [review.wallet_units, review.principal_units, review.gift_units, review.compensation_units, review.frozen_units, review.refund_fen, review.paid_fen]) {
    assert.equal(closureReturnUnits(value), "待核")
    assert.notEqual(closureReturnUnits(value), "0")
  }
  assert.equal(closureReturnPoints(review.points_hundredths), "待核")
  assert.equal(closureReturnPoints(ready.points_hundredths), "12,345（百分位）")
  assert.throws(() => parseClosureReturnList(list([ready], true, { page: 2 }), { page: 1, pageSize: 20 }), /分页/)
  assert.throws(() => parseClosureReturnList(list([ready, { ...ready }])), /重复账号周期/)
})

test("五态只允许规定动作，部分退、非纯本金和 PAID／RELEASED 写入均无入口", () => {
  assert.deepEqual(closureReturnAllowedActions(ready, true), ["prepare"])
  assert.deepEqual(closureReturnAllowedActions(review, true), [])
  assert.deepEqual(closureReturnAllowedActions(frozen, true), ["record-payment", "release"])
  assert.deepEqual(closureReturnAllowedActions(paid, true), [])
  assert.deepEqual(closureReturnAllowedActions(releasedHistory, true), [])
  assert.deepEqual(closureReturnAllowedActions(frozen, true, true), [])
  assert.deepEqual(closureReturnAllowedActions(ready, false), [])
  const released = row("READY_TO_PREPARE", { case_id: "released-case", version: 2 })
  assert.deepEqual(closureReturnAllowedActions(released, true), ["prepare"])
  const again = createClosureReturnPrepareIntent(context, released, "prepare-after-release")
  assert.equal(again.caseId, null)
  assert.deepEqual(parseClosureReturnIntent(JSON.stringify(again), context), again)
  for (const changed of [
    row("READY_TO_PREPARE", { gift_units: 1 }),
    row("READY_TO_PREPARE", { compensation_units: 1 }),
    row("READY_TO_PREPARE", { principal_units: null }),
    row("READY_TO_PREPARE", { wallet_units: 12499 }),
  ]) assert.deepEqual(closureReturnAllowedActions(changed, true), [])
  assert.throws(() => paymentIntent("partial-payment", 12499), /必须等于原可退本金/)
  assert.throws(() => createClosureReturnReleaseIntent(context, paid, "试图解除已付", "paid-release"), /仅未实付冻结单/)
})

test("prepare 请求严格包含账号周期、预期钱包和本金及原请求号", () => {
  const intent = prepareIntent()
  assert.deepEqual(intent.payload, {
    account_id: 7, business_cycle: 3, expected_wallet_units: 12500,
    expected_principal_units: 12500, client_request_id: "prepare-request-1",
  })
  assert.deepEqual(Object.keys(intent.payload).sort(), ["account_id", "business_cycle", "client_request_id", "expected_principal_units", "expected_wallet_units"])
  assert.deepEqual(parseClosureReturnIntent(JSON.stringify(intent), context), intent)
  const result = parseClosureReturnPrepareResult(prepareReceipt(true), intent)
  assert.equal(result.row.status, "FROZEN")
  assert.equal(result.replayed, true)
  assert.equal(result.wallet_frozen_units, 12500)
  assert.throws(() => parseClosureReturnPrepareResult({ ...prepareReceipt(), account_id: 8 }, intent), /回执/)
})

test("实付请求只发全额、脱敏编号、SHA256、发生时间和请求号，NOT_REQUESTED 不是渠道成功", () => {
  const intent = paymentIntent()
  assert.deepEqual(intent.payload, {
    amount_fen: 12500, external_reference: "OFFLINE-RETURN-0001", proof_sha256: sha,
    occurred_at: occurredAt, client_request_id: "payment-request-1",
  })
  assert.deepEqual(parseClosureReturnIntent(JSON.stringify(intent), context), intent)
  const safeReference = createClosureReturnPaymentIntent(context, frozen, {
    amountFen: 12500, externalReference: "OFFLINE-RETURN-0002", proofSha256: sha, occurredAt,
  }, "safe-reference") as Extract<ClosureReturnIntent, { operation: "record-payment" }>
  assert.equal(safeReference.payload.external_reference, "OFFLINE-RETURN-0002")
  for (const reference of ["6222020200112233445", "https://example.invalid/proof", "   ", "****1234"]) {
    assert.throws(() => createClosureReturnPaymentIntent(context, frozen, {
      amountFen: 12500, externalReference: reference, proofSha256: sha, occurredAt,
    }, "unsafe-reference"), /外部编号/)
  }
  const result = parseClosureReturnPaymentResult(paymentReceipt(), intent)
  assert.equal(result.channel_status, "NOT_REQUESTED")
  assert.equal(result.row.status, "PAID")
  assert.throws(() => parseClosureReturnPaymentResult({ ...paymentReceipt(), channel_status: "SUCCESS" }, intent), /不得表示银行/)
})

test("解除请求严格为原因和请求号，回执不得保留冻结或变成已实付", () => {
  const intent = releaseIntent()
  assert.deepEqual(intent.payload, { reason: "原冻结登记有误，核对后解除", client_request_id: "release-request-1" })
  assert.deepEqual(parseClosureReturnIntent(JSON.stringify(intent), context), intent)
  const released = row("READY_TO_PREPARE", { case_id: frozen.case_id, version: 3 })
  const result = parseClosureReturnReleaseResult({
    ...released, replayed: false, wallet_balance: 12500, wallet_frozen_units: 0,
  }, intent)
  assert.equal(result.wallet_balance, 12500)
  assert.equal(result.wallet_frozen_units, 0)
  assert.throws(() => parseClosureReturnReleaseResult({ ...paid, replayed: false, wallet_balance: 0, wallet_frozen_units: 0 }, intent), /解除回执/)
})

test("所有写操作先保存完整请求；存储不可用、网络和 5xx 不发送或保留原号", async () => {
  for (const intent of [prepareIntent(), paymentIntent(), releaseIntent()]) {
    const storage = memoryStorage(), key = closureReturnStorageKey(context), calls: string[] = []
    await submitClosureReturnWrite(intent, {
      save(value) { calls.push("save"); storeClosureReturnIntent(storage, key, value) },
      async send() { calls.push("send"); throw new PlatformNetworkError("offline") },
      clear(value) { clearStoredClosureReturnIntent(storage, key, value) },
    }).catch(() => {})
    assert.deepEqual(calls, ["save", "send"])
    assert.deepEqual(readStoredClosureReturnIntent(storage, key, context), intent)
  }
  let sent = false
  await assert.rejects(submitClosureReturnWrite(prepareIntent(), {
    save() { throw Object.assign(new Error("存储不可用"), { localCode: "STORAGE_UNAVAILABLE" }) },
    async send() { sent = true; throw new Error("不应发送") }, clear() {},
  }), /存储不可用/)
  assert.equal(sent, false)
  const storage = memoryStorage(), key = closureReturnStorageKey(context)
  await assert.rejects(submitClosureReturnWrite(paymentIntent(), {
    save(value) { storeClosureReturnIntent(storage, key, value) },
    async send() { throw new PlatformRequestError("unavailable", 503) },
    clear(value) { clearStoredClosureReturnIntent(storage, key, value) },
  }))
  assert.equal(readStoredClosureReturnIntent(storage, key, context)?.client_request_id, "payment-request-1")
})

test("网络未知后只读按原号和完整内容恢复，不再次 POST；同号异内容明确冲突", async () => {
  let writes = 0
  const storage = memoryStorage(), key = closureReturnStorageKey(context), intent = paymentIntent()
  await assert.rejects(submitClosureReturnWrite(intent, {
    save(value) { storeClosureReturnIntent(storage, key, value) },
    async send() { writes += 1; throw new PlatformNetworkError("offline") },
    clear(value) { clearStoredClosureReturnIntent(storage, key, value) },
  }))
  const recovered = await recoverClosureReturnWrite(intent, {
    findCase: unusedFind,
    readCase: async () => detail(paid, [recoveryEvent(intent)]),
  })
  assert.equal(recovered.status, "FOUND")
  assert.equal(writes, 1)
  const changed = createClosureReturnPaymentIntent(context, frozen, {
    amountFen: 12500, externalReference: "OFFLINE-RETURN-9999", proofSha256: sha, occurredAt,
  }, intent.client_request_id)
  const conflict = await recoverClosureReturnWrite(changed, {
    findCase: unusedFind,
    readCase: async () => detail(paid, [recoveryEvent(intent)]),
  })
  assert.equal(conflict.status, "CONFLICT")
  assert.match(conflict.message || "", /内容不同/)
  const wrongKind = await recoverClosureReturnWrite(intent, {
    findCase: unusedFind,
    readCase: async () => detail(paid, [{ ...recoveryEvent(intent), kind: "RELEASED" }]),
  })
  assert.equal(wrongKind.status, "CONFLICT")
})

test("查不到时才允许原号原内容重试；缺少事件内容证据必须人工核对", async () => {
  const intent = releaseIntent()
  const missing = await recoverClosureReturnWrite(intent, {
    findCase: unusedFind, readCase: async () => detail(frozen, []),
  })
  assert.equal(missing.status, "NOT_FOUND")
  const audit = await recoverClosureReturnWrite(intent, {
    findCase: unusedFind, readCase: async () => detail(frozen, [{
      client_request_id: intent.client_request_id, kind: "RELEASED", platform_actor_id: intent.actorId,
    }]),
  })
  assert.equal(audit.status, "NEEDS_AUDIT")
  assert.match(audit.message || "", /不能仅凭请求号/)
  const storage = memoryStorage(), key = closureReturnStorageKey(context)
  storeClosureReturnIntent(storage, key, intent)
  const sent: ClosureReturnIntent[] = []
  const result = await submitClosureReturnWrite(intent, {
    save(value) { storeClosureReturnIntent(storage, key, value) },
    async send(value) {
      sent.push(value)
      const released = row("READY_TO_PREPARE", { case_id: frozen.case_id, version: 3 })
      return parseClosureReturnReleaseResult({ ...released, replayed: true, wallet_balance: 12500, wallet_frozen_units: 0 }, intent)
    },
    clear(value) { clearStoredClosureReturnIntent(storage, key, value) },
  })
  assert.equal(result.replayed, true)
  assert.deepEqual(sent, [intent])
  assert.equal(storage.values.has(key), false)
})

test("实付与解除恢复互斥：其他请求已付不重复登记，已付禁止解除，其他冻结不重复准备", async () => {
  const paidByOther = await recoverClosureReturnWrite(paymentIntent(), {
    findCase: unusedFind, readCase: async () => detail(paid, []),
  })
  assert.equal(paidByOther.status, "ALREADY_EXISTS")
  const releasePaid = await recoverClosureReturnWrite(releaseIntent(), {
    findCase: unusedFind, readCase: async () => detail(paid, []),
  })
  assert.equal(releasePaid.status, "CONFLICT")
  const preparedByOther = await recoverClosureReturnWrite(prepareIntent(), {
    findCase: async () => detail(frozen, []), readCase: unusedRead,
  })
  assert.equal(preparedByOther.status, "ALREADY_EXISTS")
  const originalPrepare = prepareIntent()
  const foundPrepare = await recoverClosureReturnWrite(originalPrepare, {
    findCase: async () => detail(frozen, [recoveryEvent(originalPrepare)]), readCase: unusedRead,
  })
  assert.equal(foundPrepare.status, "FOUND")
  const originalRelease = releaseIntent()
  const released = row("READY_TO_PREPARE", { case_id: frozen.case_id, version: 3 })
  const foundRelease = await recoverClosureReturnWrite(originalRelease, {
    findCase: unusedFind, readCase: async () => detail(released, [recoveryEvent(originalRelease)]),
  })
  assert.equal(foundRelease.status, "FOUND")
})

test("409 保留服务端状态语义并允许刷新，403 清除写权限和本地待核，网络错误仍保留", async () => {
  for (const failure of [new PlatformRequestError("状态变化", 409, "RETURN_CASE_STATE_CONFLICT"), new PlatformRequestError("denied", 403)]) {
    const storage = memoryStorage(), key = closureReturnStorageKey(context)
    storeClosureReturnIntent(storage, key, paymentIntent())
    await assert.rejects(submitClosureReturnWrite(paymentIntent(), {
      save(value) { storeClosureReturnIntent(storage, key, value) },
      async send() { throw failure },
      clear(value) { clearStoredClosureReturnIntent(storage, key, value) },
    }))
    assert.equal(storage.values.has(key), false)
  }
  assert.deepEqual(closureReturnWriteFailure(new PlatformRequestError("状态变化", 409)), { kind: "conflict", clear: true })
  assert.match(closureReturnWriteMessage(new PlatformRequestError("状态变化", 409)), /保留服务端状态并刷新/)
  assert.match(closureReturnWriteMessage(new PlatformRequestError("同号异内容", 409, "REQUEST_ID_CONFLICT")), /原记录未被覆盖/)
  assert.deepEqual(closureReturnWriteFailure(new PlatformRequestError("denied", 403)), { kind: "denied", clear: true })
  assert.deepEqual(closureReturnWriteFailure(new PlatformNetworkError("offline")), { kind: "unknown", clear: false })
})

test("迟到回包不能清除新请求，门店、会话、scope 和账号周期均隔离", () => {
  const storage = memoryStorage(), key = closureReturnStorageKey(context), original = paymentIntent()
  storeClosureReturnIntent(storage, key, original)
  const newer = releaseIntent("new-release-request")
  storage.setItem(key, JSON.stringify(newer))
  assert.equal(clearStoredClosureReturnIntent(storage, key, original), false)
  assert.deepEqual(readStoredClosureReturnIntent(storage, key, context), newer)
  assert.equal(clearStoredClosureReturnIntent(storage, key, newer), true)
  for (const changed of [
    { ...context, storeId: 3, storeSlug: "xinan" }, { ...context, sessionId: "session-2" },
    { ...context, scopeSignature: "[]" },
  ]) {
    assert.notEqual(closureReturnStorageKey(changed), key)
    assert.equal(sameClosureReturnScope(original, changed), false)
  }
  assert.equal(sameClosureReturnCase(original, { ...paid, business_cycle: 4 }), false)
  assert.equal(sameClosureReturnCase(original, { ...paid, case_id: "other-case" }), false)
  const raw = JSON.stringify(original)
  assert.throws(() => parseClosureReturnIntent(raw, { ...context, storeId: 3 }), /已阻止新写入/)
  assert.throws(() => parseClosureReturnIntent(raw, { ...context, scopeSignature: "[]" }), /已阻止新写入/)
})

test("closure:view/manage 分离且门店 scope 不串权", () => {
  const finance = closureReturnsAccess(profile([{ role: "FINANCE", scope_slug: "shenglang" }]), "shenglang")
  assert.deepEqual([finance.view, finance.manage], [true, false])
  const admin = closureReturnsAccess(profile([{ role: "SUPER_ADMIN", scope_slug: "shenglang" }]), "shenglang")
  assert.deepEqual([admin.view, admin.manage], [true, true])
  const wrongStore = closureReturnsAccess(profile([{ role: "SUPER_ADMIN", scope_slug: "xinan" }]), "shenglang")
  assert.deepEqual([wrongStore.view, wrongStore.manage], [false, false])
  assert.notEqual(finance.scopeSignature, wrongStore.scopeSignature)
})

test("组件读取模型覆盖加载、空、首次错误、旧快照、就绪和权限清理", () => {
  assert.equal(closureReturnsViewMode({ denied: false, hasSnapshot: false, loading: true, error: "", rowCount: 0 }), "loading")
  assert.equal(closureReturnsViewMode({ denied: false, hasSnapshot: true, loading: false, error: "", rowCount: 0 }), "empty")
  assert.equal(closureReturnsViewMode({ denied: false, hasSnapshot: false, loading: false, error: "读取失败", rowCount: 0 }), "error")
  assert.equal(closureReturnsViewMode({ denied: false, hasSnapshot: true, loading: false, error: "刷新失败", rowCount: 1 }), "stale")
  assert.equal(closureReturnsViewMode({ denied: false, hasSnapshot: true, loading: false, error: "", rowCount: 1 }), "ready")
  assert.equal(closureReturnsViewMode({ denied: true, hasSnapshot: false, loading: false, error: "权限拒绝", rowCount: 0 }), "denied")
})

test("详情拒绝完整银行资料、凭证正文、未脱敏外部编号和伪造渠道成功", () => {
  assert.throws(() => parseClosureReturnDetail({ ...paid, events: [{ bank_account: "6222020200112233445" }], channel_status: "NOT_REQUESTED" }), /不得公开/)
  assert.throws(() => parseClosureReturnDetail({ ...paid, events: [{ proof_content: "完整凭证" }], channel_status: "NOT_REQUESTED" }), /不得公开/)
  assert.throws(() => parseClosureReturnDetail({ ...paid, events: [{ external_reference: "6222020200112233445" }], channel_status: "NOT_REQUESTED" }), /未脱敏/)
  assert.throws(() => parseClosureReturnDetail({ ...paid, events: [], channel_status: "SUCCESS" }), /渠道状态/)
  assert.throws(() => parseClosureReturnDetail({ ...paid, case_id: null, events: [], channel_status: "NOT_REQUESTED" }), /账户行|详情/)
})


test("RELEASED 历史详情严格只读，不能补实付或重复解除，原解除事件仍可恢复", async () => {
  assert.deepEqual(closureReturnStatusPresentation("RELEASED"), {
    label: "历史已解除", tone: "muted", readOnly: "历史已解除，只读",
  })
  assert.throws(() => parseClosureReturnRow({ ...releasedHistory, case_id: null }), /账户行|case/)
  assert.throws(() => createClosureReturnPaymentIntent(context, releasedHistory, {
    amountFen: 12500, externalReference: "OFFLINE-RETURN-0001", proofSha256: sha, occurredAt,
  }, "payment-after-release"), /仅|实付|冻结/)
  assert.throws(() => createClosureReturnReleaseIntent(context, releasedHistory, "重复解除", "release-again"), /仅未实付冻结单/)
  const originalRelease = releaseIntent()
  const history = detail(releasedHistory, [recoveryEvent(originalRelease)])
  const recoveredRelease = await recoverClosureReturnWrite(originalRelease, {
    findCase: unusedFind, readCase: async () => history,
  })
  assert.equal(recoveredRelease.status, "FOUND")
  const stalePayment = await recoverClosureReturnWrite(paymentIntent(), {
    findCase: unusedFind, readCase: async () => history,
  })
  assert.equal(stalePayment.status, "CONFLICT")
  assert.match(stalePayment.message || "", /已解除，禁止再登记实付/)
  const receipt = parseClosureReturnReleaseResult({
    ...releasedHistory, replayed: true, wallet_balance: 12500, wallet_frozen_units: 0,
  }, originalRelease)
  assert.equal(receipt.row.status, "RELEASED")
  assert.deepEqual(closureReturnAllowedActions(receipt.row, true), [])
})

test("PAID／FROZEN 保留后来新增钱包余额和待核原因，部分汇总继续锁写", () => {
  const paidWithNewBalance = row("PAID", {
    wallet_units: 8800, blocking_reasons: ["NEW_AVAILABLE_BALANCE"],
  })
  const frozenWithNewBalance = row("FROZEN", {
    account_id: 8, wallet_units: 6600, blocking_reasons: ["NEW_AVAILABLE_BALANCE"],
  })
  const result = parseClosureReturnList(list([paidWithNewBalance, frozenWithNewBalance], false))
  assert.equal(result.summary.complete, false)
  assert.equal(result.list[0].wallet_units, 8800)
  assert.equal(result.list[1].wallet_units, 6600)
  assert.deepEqual(result.list[0].blocking_reasons, ["NEW_AVAILABLE_BALANCE"])
  assert.deepEqual(result.list[1].blocking_reasons, ["NEW_AVAILABLE_BALANCE"])
  assert.equal(paid.wallet_units, 0)
  assert.deepEqual(closureReturnAllowedActions(paidWithNewBalance, true, true), [])
  assert.deepEqual(closureReturnAllowedActions(frozenWithNewBalance, true, true), [])
  const receipt = parseClosureReturnPaymentResult({
    ...paymentReceipt(), wallet_units: 8800, wallet_balance: 8800,
    blocking_reasons: ["NEW_AVAILABLE_BALANCE"],
  }, paymentIntent())
  assert.equal(receipt.wallet_balance, 8800)
  assert.equal(receipt.row.wallet_units, 8800)
  assert.equal(receipt.row.paid_fen, 12500)
  assert.deepEqual(receipt.row.blocking_reasons, ["NEW_AVAILABLE_BALANCE"])
})

test("旧 RELEASED 详情不被最新 READY 或新 FROZEN 覆盖，新一轮准备不携带旧 case", async () => {
  const originalRelease = releaseIntent()
  const history = detail(releasedHistory, [recoveryEvent(originalRelease)])
  const latestReady = row("READY_TO_PREPARE", {
    case_id: releasedHistory.case_id, wallet_units: 13000, principal_units: 13000,
    refund_fen: 13000, version: 5,
  })
  assert.deepEqual(closureReturnAllowedActions(latestReady, true), ["prepare"])
  const nextIntent = prepareIntent("prepare-after-released-history", latestReady)
  assert.equal(nextIntent.caseId, null)
  assert.equal(nextIntent.payload.expected_wallet_units, 13000)
  assert.equal(nextIntent.payload.expected_principal_units, 13000)
  const nextFrozen = row("FROZEN", {
    case_id: "case-frozen-2", principal_units: 13000, refund_fen: 13000,
    frozen_units: 13000, version: 6,
  })
  const nextReceipt = parseClosureReturnPrepareResult({
    ...nextFrozen, replayed: false, wallet_balance: 0, wallet_frozen_units: 13000,
  }, nextIntent)
  assert.equal(nextReceipt.row.case_id, "case-frozen-2")
  const recovered = await recoverClosureReturnWrite(nextIntent, {
    findCase: async () => detail(nextFrozen, [recoveryEvent(nextIntent)]), readCase: unusedRead,
  })
  assert.equal(recovered.status, "FOUND")
  assert.equal(history.row.status, "RELEASED")
  assert.equal(history.row.case_id, releasedHistory.case_id)
  assert.equal(history.row.wallet_units, 12500)
  assert.equal(history.events[0].kind, "RELEASED")
  assert.deepEqual(closureReturnAllowedActions(history.row, true), [])
  assert.deepEqual(closureReturnAllowedActions(nextReceipt.row, true), ["record-payment", "release"])
})


const reviewedCurrentReadyApiResponse = {
  list: [{
    case_id: null,
    account_id: 807101,
    business_cycle: 1,
    account_label: "老板 807101",
    status: "READY_TO_PREPARE",
    wallet_units: 100,
    principal_units: 100,
    gift_units: 0,
    compensation_units: 0,
    points_hundredths: 321,
    frozen_units: 0,
    refund_fen: 100,
    paid_fen: 0,
    version: 0,
    blocking_reasons: [],
    updated_at: "2026-11-25T06:00:00+00:00",
  }],
  total: 1,
  page: 1,
  page_size: 20,
  has_more: false,
  summary: {
    complete: true,
    counts: { READY_TO_PREPARE: 1, REVIEW_REQUIRED: 0, FROZEN: 0, PAID: 0, RELEASED: 0 },
    totals: {
      wallet_units: 100,
      principal_units: 100,
      gift_units: 0,
      compensation_units: 0,
      points_hundredths: 321,
      frozen_units: 0,
      refund_fen: 100,
      paid_fen: 0,
    },
  },
}
const reviewedPaidHistoryCaseId = "923e4567-e89b-42d3-a456-426614174000"
const reviewedPaidHistoryApiResponse = {
  case_id: reviewedPaidHistoryCaseId,
  account_id: 807101,
  business_cycle: 1,
  account_label: "老板 807101",
  status: "PAID",
  wallet_units: 100,
  principal_units: 12500,
  gift_units: 0,
  compensation_units: 0,
  points_hundredths: 321,
  frozen_units: 0,
  refund_fen: 12500,
  paid_fen: 12500,
  version: 3,
  blocking_reasons: ["NEW_AVAILABLE_BALANCE"],
  updated_at: "2026-11-25T05:00:00+00:00",
  events: [{
    event_id: "823e4567-e89b-42d3-a456-426614174000",
    kind: "PAYMENT_RECORDED",
    client_request_id: "return-payment-1",
    platform_actor_id: context.actorId,
    from_status: "FROZEN",
    to_status: "PAID",
    detail: {
      amount_fen: 12500,
      external_reference_masked: "OF************01",
      external_reference_sha256: "db8731986ed2c1f4cd536eec6bf361bb8cf6da7afbba3288d430ff0bd79ec198",
      proof_sha256: sha,
      occurred_at: occurredAt,
      channel_status: "NOT_REQUESTED",
    },
    created_at: "2026-11-25T05:00:00+00:00",
  }],
  channel_status: "NOT_REQUESTED",
}

test("真实新本金 100 的无案 READY 原始清单可准备新案，旧案实付不混入当前行", () => {
  const parsed = parseClosureReturnList(reviewedCurrentReadyApiResponse, { page: 1, pageSize: 20 })
  const current = parsed.list[0]
  assert.equal(current.case_id, null)
  assert.equal(current.status, "READY_TO_PREPARE")
  assert.equal(current.wallet_units, 100)
  assert.equal(current.principal_units, 100)
  assert.equal(current.refund_fen, 100)
  assert.equal(current.paid_fen, 0)
  assert.equal(parsed.summary.complete, true)
  const actions = closureReturnAllowedActions(current, true, !parsed.summary.complete)
  assert.deepEqual(actions, ["prepare"])
  assert.equal(closureReturnStatusPresentation(current.status).readOnly, "")
  const intent = createClosureReturnPrepareIntent(context, current, "prepare-current-100") as Extract<ClosureReturnIntent, { operation: "prepare" }>
  assert.equal(intent.caseId, null)
  assert.deepEqual(intent.payload, {
    account_id: 807101,
    business_cycle: 1,
    expected_wallet_units: 100,
    expected_principal_units: 100,
    client_request_id: "prepare-current-100",
  })
  const newCaseId = "a23e4567-e89b-42d3-a456-426614174000"
  const receipt = {
    case_id: newCaseId,
    account_id: 807101,
    business_cycle: 1,
    account_label: "老板 807101",
    status: "FROZEN",
    wallet_units: 0,
    principal_units: 100,
    gift_units: 0,
    compensation_units: 0,
    points_hundredths: 321,
    frozen_units: 100,
    refund_fen: 100,
    paid_fen: 0,
    version: 1,
    blocking_reasons: [],
    updated_at: "2026-11-25T06:01:00+00:00",
    replayed: false,
    wallet_balance: 0,
    wallet_frozen_units: 100,
  }
  const prepared = parseClosureReturnPrepareResult(receipt, intent)
  assert.equal(prepared.row.case_id, newCaseId)
  assert.equal(prepared.row.status, "FROZEN")
  assert.equal(prepared.row.principal_units, 100)
  assert.throws(() => parseClosureReturnPrepareResult({ ...receipt, principal_units: 12500 }, intent), /准备冻结回执/)
})

test("真实旧 PAID 详情保留 12500 历史实付和当前 100 新余额，只读且与最新 READY 独立", () => {
  const history = parseClosureReturnDetail(reviewedPaidHistoryApiResponse, {
    caseId: reviewedPaidHistoryCaseId,
    accountId: 807101,
    businessCycle: 1,
  })
  assert.equal(history.row.status, "PAID")
  assert.equal(history.row.paid_fen, 12500)
  assert.equal(history.row.refund_fen, 12500)
  assert.equal(history.row.wallet_units, 100)
  assert.deepEqual(history.row.blocking_reasons, ["NEW_AVAILABLE_BALANCE"])
  assert.equal(history.events[0].kind, "PAYMENT_RECORDED")
  assert.deepEqual(closureReturnAllowedActions(history.row, true), [])
  assert.equal(closureReturnStatusPresentation(history.row.status).readOnly, "只读，不可解除")
  assert.throws(() => createClosureReturnPrepareIntent(context, history.row, "prepare-from-paid-history"), /仅全部余额/)
  assert.throws(() => createClosureReturnPaymentIntent(context, history.row, {
    amountFen: 12500,
    externalReference: "OFFLINE-RETURN-0001",
    proofSha256: sha,
    occurredAt,
  }, "repeat-paid-history"), /实付必须/)
  assert.throws(() => createClosureReturnReleaseIntent(context, history.row, "试图解除旧实付", "release-paid-history"), /仅未实付冻结单/)
  assert.throws(() => parseClosureReturnDetail({ ...reviewedPaidHistoryApiResponse, business_cycle: 2 }, {
    caseId: reviewedPaidHistoryCaseId,
    accountId: 807101,
    businessCycle: 1,
  }), /详情/)
  const latest = parseClosureReturnList(reviewedCurrentReadyApiResponse, { page: 1, pageSize: 20 }).list[0]
  assert.equal(latest.status, "READY_TO_PREPARE")
  assert.equal(latest.case_id, null)
  assert.equal(latest.paid_fen, 0)
  assert.deepEqual(closureReturnAllowedActions(latest, true), ["prepare"])
  const next = createClosureReturnPrepareIntent(context, latest, "prepare-independent-current-100") as Extract<ClosureReturnIntent, { operation: "prepare" }>
  assert.equal(next.caseId, null)
  assert.equal(next.payload.expected_principal_units, 100)
  assert.equal(history.row.status, "PAID")
  assert.equal(history.row.paid_fen, 12500)
  assert.equal(history.row.refund_fen, 12500)
  assert.equal(history.row.wallet_units, 100)
})
