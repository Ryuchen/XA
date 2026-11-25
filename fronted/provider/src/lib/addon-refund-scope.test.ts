/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import * as bossScope from "../../../boss/src/business/addon-refund-scope.ts"
import * as providerScope from "./addon-refund-scope.ts"

type ScopeModule = typeof providerScope
type Proposal = providerScope.AddonRefundProposal

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  }
}

function pendingDecision(scope: ScopeModule, storage: ReturnType<typeof memoryStorage>, key: string) {
  const value = scope.readPendingAddonRefundDecision(storage, key)
  assert.ok(value && value !== "invalid")
  return value
}

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 21,
    payment_id: 11,
    case_id: 31,
    addon_name: "测试加购",
    amount_raw: 50,
    provider_burden_raw: 50,
    provider_income_retained_raw: 50,
    explanation: "测试方案",
    status: "PENDING",
    refund_executed: false,
    decisions: [
      { account_id: 7, role: "PROVIDER", accepted: null, decided_at: null },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ],
    participants: [
      { account_id: 7, share_plan: [{ segment_id: 101, basis: 100, refund_basis: 50, burden: 50 }] },
      { account_id: 8, share_plan: [{ segment_id: 102, basis: 80, refund_basis: 0, burden: 0 }] },
    ],
    ...overrides,
  }
}

function clientWith(getProposal: () => Proposal | Promise<Proposal>, postProposal?: (path: string, payload: unknown) => Proposal | Promise<Proposal>) {
  const gets: string[] = []
  const posts: { path: string; payload: unknown }[] = []
  return {
    gets,
    posts,
    client: {
      async get<T>(path: string): Promise<T> {
        gets.push(path)
        return await getProposal() as T
      },
      async post<T>(path: string, payload: unknown): Promise<T> {
        posts.push({ path, payload })
        if (!postProposal) throw new Error("unexpected post")
        return await postProposal(path, payload) as T
      },
    },
  }
}

const implementations: [string, ScopeModule][] = [
  ["boss", bossScope],
  ["provider", providerScope],
]

for (const [name, scope] of implementations) {
  test(`${name}: 老板看到全部责任段，陪玩只看到本人责任段`, () => {
    const row = proposal()
    assert.deepEqual(scope.proposalScope(row, "boss", 1).map(item => [item.accountId, item.segmentId]), [[7, 101], [8, 102]])
    assert.deepEqual(scope.proposalScope(row, "provider", 7).map(item => [item.accountId, item.segmentId]), [[7, 101]])
    assert.notEqual(
      scope.proposalScopeKey(scope.proposalScope(row, "provider", 7)),
      scope.proposalScopeKey(scope.proposalScope(proposal({ payment_id: 12, participants: [
        { account_id: 7, share_plan: [{ segment_id: 202, basis: 100, refund_basis: 50, burden: 50 }] },
      ] }), "provider", 7)),
    )
  })

  test(`${name}: 方案列表保留多笔付款目标，详情不能串到另一笔付款`, async () => {
    const targets = [
      { payment_id: 11, proposal_id: 21, status: "PENDING", my_accepted: null },
      { payment_id: 12, proposal_id: 22, status: "PENDING", my_accepted: null },
    ]
    const listClient = { async get<T>(): Promise<T> { return targets as T } }
    const fake = clientWith(() => proposal({ id: 22, payment_id: 12 }))
    assert.deepEqual(await scope.loadAddonRefundProposalTargets(listClient, 5), targets)
    await assert.rejects(scope.loadAddonRefundProposal(fake.client, 5, 11, 22, 7, "provider"), /无法核对/)
  })

  test(`${name}: 缺失、重复或未知金额的履约段不能用于确认`, () => {
    assert.throws(() => scope.proposalScope(proposal({ participants: undefined }), "boss", 1), /履约段/)
    assert.throws(() => scope.proposalScope(proposal({ participants: [
      { account_id: 7, share_plan: [{ segment_id: 101, basis: 100, refund_basis: 50 }] },
    ] }), "provider", 7), /金额/)
    assert.throws(() => scope.proposalScope(proposal({ participants: [
      { account_id: 7, share_plan: [
        { segment_id: 101, basis: 100, refund_basis: 50, burden: 50 },
        { segment_id: 101, basis: 100, refund_basis: 50, burden: 50 },
      ] },
    ] }), "provider", 7), /重复/)
  })

  test(`${name}: 必须逐项勾选且不能勾选方案外履约段`, () => {
    const rows = scope.proposalScope(proposal(), "boss", 1)
    assert.equal(scope.scopeSelectionComplete(rows, new Set(["7:101"])), false)
    assert.equal(scope.scopeSelectionComplete(rows, new Set(["7:101", "8:102"])), true)
    assert.equal(scope.scopeSelectionComplete(rows, new Set(["7:101", "8:102", "9:999"])), false)
  })

  test(`${name}: 结果未知后保留原目标、原决定和原履约段并按原路径重试`, async () => {
    const storage = memoryStorage()
    const pending = proposal()
    const accepted = proposal({ decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    let attempts = 0
    const fake = clientWith(
      () => pending,
      () => {
        attempts += 1
        if (attempts === 1) throw new Error("network unknown")
        return accepted
      },
    )
    const args = {
      client: fake.client,
      storage,
      key: "decision",
      orderId: 5,
      accountId: 7,
      role: "provider" as const,
      decision: { paymentId: 11, proposalId: 21, accepted: true },
      scopeKey: scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7)),
    }
    await assert.rejects(scope.recordAddonRefundDecision(args), /network unknown/)
    const saved = scope.readPendingAddonRefundDecision(storage, "decision")
    assert.notEqual(saved, "invalid")
    assert.notEqual(saved, null)
    assert.equal(saved && saved !== "invalid" ? saved.paymentId : null, 11)
    assert.equal(saved && saved !== "invalid" ? saved.proposalId : null, 21)
    assert.equal(saved && saved !== "invalid" ? saved.accepted : null, true)
    assert.ok(saved && saved !== "invalid" ? saved.scopeKey : false)

    await scope.recordAddonRefundDecision(args)
    assert.equal(fake.posts.length, 2)
    assert.deepEqual(fake.posts.map(item => item.path), [
      "/orders/orders/5/addons/payments/11/refund-proposals/21/",
      "/orders/orders/5/addons/payments/11/refund-proposals/21/",
    ])
    assert.deepEqual(fake.posts.map(item => item.payload), [{ accepted: true }, { accepted: true }])
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 刷新后先 GET 核对已记录决定，履约段失效也不重复 POST`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, scopeKey }) })
    const accepted = proposal({ participants: undefined, decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const fake = clientWith(() => accepted)
    await scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true } })
    assert.equal(fake.gets.length, 1)
    assert.equal(fake.posts.length, 0)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 原付款、方案或决定不同不能覆盖待核对凭证`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, scopeKey }) })
    const fake = clientWith(() => pending)
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 12, proposalId: 21, accepted: true }, scopeKey }), /原付款/)
    assert.equal(fake.gets.length, 0)
    assert.equal(fake.posts.length, 0)
    assert.equal(pendingDecision(scope, storage, "decision").paymentId, 11)
  })

  test(`${name}: 刷新后履约段变化会阻止提交并保留原凭证`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, scopeKey }) })
    const changed = proposal({ participants: [
      { account_id: 7, share_plan: [{ segment_id: 999, basis: 100, refund_basis: 50, burden: 50 }] },
      { account_id: 8, share_plan: [{ segment_id: 102, basis: 80, refund_basis: 0, burden: 0 }] },
    ] })
    const fake = clientWith(() => changed)
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /履约段已变化/)
    assert.equal(fake.posts.length, 0)
    assert.equal(pendingDecision(scope, storage, "decision").scopeKey, scopeKey)
  })

  test(`${name}: 历史已执行状态只用于核对，不产生执行请求`, async () => {
    const executed = proposal({ refund_executed: true })
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(executed, "provider", 7))
    const storage = memoryStorage()
    const fake = clientWith(() => executed)
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /已结束/)
    assert.equal(fake.posts.length, 0)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 旧版无履约段凭证只在 GET 确认原决定后清除`, async () => {
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true }) })
    const accepted = proposal({ participants: undefined, decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const fake = clientWith(() => accepted)
    await scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true } })
    assert.equal(fake.posts.length, 0)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: POST 回执不匹配时保留凭证，供下次先 GET 恢复`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage()
    const fake = clientWith(() => pending, () => proposal({ payment_id: 12 }))
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /尚待核对/)
    assert.equal(pendingDecision(scope, storage, "decision").paymentId, 11)
  })

  test(`${name}: 金额未知或损坏凭证都不会发起 POST`, async () => {
    const unknown = proposal({ amount_raw: Number.NaN })
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(unknown, "provider", 7))
    const storage = memoryStorage()
    const fake = clientWith(() => unknown)
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /金额/)
    assert.equal(fake.posts.length, 0)
    assert.notEqual(scope.readPendingAddonRefundDecision(storage, "decision"), null)

    const broken = memoryStorage({ decision: "{broken" })
    const blocked = clientWith(() => unknown)
    await assert.rejects(scope.recordAddonRefundDecision({ client: blocked.client, storage: broken, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /凭证无法读取/)
    assert.equal(blocked.gets.length, 0)
    assert.equal(blocked.posts.length, 0)
  })

  test(`${name}: 老板决定绑定全部责任陪玩及其履约段`, async () => {
    const pending = proposal()
    const accepted = proposal({ status: "ACCEPTED", decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
    ] })
    const storage = memoryStorage()
    const rows = scope.proposalScope(pending, "boss", 1)
    const fake = clientWith(() => pending, () => accepted)
    await scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 1, role: "boss", decision: { paymentId: 11, proposalId: 21, accepted: true },
      scopeKey: scope.proposalScopeKey(rows) })
    assert.equal(fake.posts.length, 1)
    assert.equal(rows.length, 2)
    assert.deepEqual(fake.posts[0].payload, { accepted: true })
  })

  test(`${name}: 损坏或重复的方案列表不能进入选择`, async () => {
    const duplicate = [
      { payment_id: 11, proposal_id: 21, status: "PENDING", my_accepted: null },
      { payment_id: 11, proposal_id: 21, status: "PENDING", my_accepted: null },
    ]
    await assert.rejects(scope.loadAddonRefundProposalTargets(
      { async get<T>(): Promise<T> { return duplicate as T } }, 5), /无法读取/)
    await assert.rejects(scope.loadAddonRefundProposalTargets(
      { async get<T>(): Promise<T> { return [{ payment_id: 11, proposal_id: 21, status: "PENDING" }] as T } }, 5), /无法读取/)
  })

  test(`${name}: 详情缺少工单或声明了错单时拒绝读取`, async () => {
    const missingCase = proposal({ case_id: undefined })
    await assert.rejects(scope.loadAddonRefundProposal(clientWith(() => missingCase).client, 5, 11, 21, 7, "provider"), /无法核对/)
    const wrongOrder = proposal({ order_id: 6 })
    await assert.rejects(scope.loadAddonRefundProposal(clientWith(() => wrongOrder).client, 5, 11, 21, 7, "provider"), /无法核对/)
  })

  test(`${name}: GET 认证或跨店拒绝不会留下新的写锁`, async () => {
    for (const status of [401, 403]) {
      const storage = memoryStorage()
      const pending = proposal()
      const fake = clientWith(() => { throw Object.assign(new Error("denied"), { status }) })
      await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
        accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true },
        scopeKey: scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7)) }), /denied/)
      assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
      assert.equal(fake.posts.length, 0)
    }
  })

  test(`${name}: 已有未知凭证遇到认证拒绝时保留原锁`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, caseId: 31, scopeKey }) })
    const fake = clientWith(() => { throw Object.assign(new Error("expired"), { status: 401 }) })
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /expired/)
    assert.equal(pendingDecision(scope, storage, "decision").caseId, 31)
  })

  test(`${name}: POST 认证拒绝不会留下尚未提交的写锁`, async () => {
    const storage = memoryStorage()
    const pending = proposal()
    const fake = clientWith(() => pending, () => { throw Object.assign(new Error("forbidden"), { status: 403 }) })
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true },
      scopeKey: scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7)) }), /forbidden/)
    assert.equal(fake.posts.length, 1)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 已有未知凭证在 POST 认证拒绝后仍保留原锁`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, caseId: 31, scopeKey }) })
    const fake = clientWith(() => pending, () => { throw Object.assign(new Error("expired"), { status: 401 }) })
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /expired/)
    assert.equal(fake.posts.length, 1)
    assert.equal(pendingDecision(scope, storage, "decision").caseId, 31)
  })

  test(`${name}: 原工单轮次变化会阻止重试并保留原凭证`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true, caseId: 31, scopeKey }) })
    const fake = clientWith(() => proposal({ case_id: 32 }))
    await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /工单轮次/)
    assert.equal(fake.posts.length, 0)
    assert.equal(pendingDecision(scope, storage, "decision").caseId, 31)
  })

  test(`${name}: POST 回执的工单、状态或执行标志损坏时继续保留写锁`, async () => {
    const pending = proposal()
    const accepted = proposal({ decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    for (const receipt of [
      { ...accepted, case_id: 32 },
      { ...accepted, status: "DONE" },
      { ...accepted, refund_executed: "true" },
    ]) {
      const storage = memoryStorage()
      const fake = clientWith(() => pending, () => receipt as Proposal)
      await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
        accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true },
        scopeKey: scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7)) }), /尚待核对|无法核对/)
      assert.equal(pendingDecision(scope, storage, "decision").caseId, 31)
    }
  })

  test(`${name}: POST 回执金额变化、未知或不可能的成功状态不清凭证`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const accepted = proposal({ decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const receipts = [
      { ...accepted, amount_raw: null },
      { ...accepted, amount_raw: 999 },
      { ...accepted, provider_burden_raw: null },
      { ...accepted, provider_burden_raw: 999 },
      { ...accepted, provider_income_retained_raw: null },
      { ...accepted, provider_income_retained_raw: 999 },
      { ...accepted, status: "SUPERSEDED" },
      { ...accepted, status: "REJECTED" },
      { ...accepted, status: "ACCEPTED" },
      { ...accepted, status: "PENDING", decisions: accepted.decisions.map(row => ({ ...row, accepted: true })) },
      { ...accepted, refund_executed: true },
    ]
    for (const receipt of receipts) {
      const storage = memoryStorage()
      const fake = clientWith(() => pending, () => receipt as Proposal)
      await assert.rejects(scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
        accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey }), /尚待核对/)
      assert.equal(fake.posts.length, 1)
      const saved = pendingDecision(scope, storage, "decision")
      assert.equal(saved.caseId, 31)
      assert.equal(saved.paymentId, 11)
      assert.equal(saved.proposalId, 21)
      assert.equal(saved.accepted, true)
      assert.equal(saved.scopeKey, scopeKey)
    }
  })

  test(`${name}: 异议 POST 只接受对应的已记录拒绝回执`, async () => {
    const pending = proposal()
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const rejected = proposal({ status: "REJECTED", decisions: [
      { account_id: 7, role: "PROVIDER", accepted: false, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const storage = memoryStorage()
    const fake = clientWith(() => pending, () => rejected)
    await scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: false }, scopeKey })
    assert.equal(fake.posts.length, 1)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 重复核对同一已确认决定只读取不再次 POST`, async () => {
    const pending = proposal()
    const accepted = proposal({ decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const storage = memoryStorage()
    let reads = 0
    const fake = clientWith(() => ++reads === 1 ? pending : accepted, () => accepted)
    const args = { client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider" as const, decision: { paymentId: 11, proposalId: 21, accepted: true },
      scopeKey: scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7)) }
    await scope.recordAddonRefundDecision(args)
    await scope.recordAddonRefundDecision(args)
    assert.equal(fake.posts.length, 1)
    assert.equal(reads, 2)
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })

  test(`${name}: 旧版待核对凭证在重新勾选履约段后补存范围，不更换原决定`, async () => {
    const pending = proposal()
    const accepted = proposal({ decisions: [
      { account_id: 7, role: "PROVIDER", accepted: true, decided_at: "2026-11-24T10:00:00Z" },
      { account_id: 1, role: "BOSS", accepted: null, decided_at: null },
    ] })
    const storage = memoryStorage({ decision: JSON.stringify({ paymentId: 11, proposalId: 21, accepted: true }) })
    const scopeKey = scope.proposalScopeKey(scope.proposalScope(pending, "provider", 7))
    const fake = clientWith(() => pending, () => accepted)
    await scope.recordAddonRefundDecision({ client: fake.client, storage, key: "decision", orderId: 5,
      accountId: 7, role: "provider", decision: { paymentId: 11, proposalId: 21, accepted: true }, scopeKey })
    assert.equal(fake.posts.length, 1)
    assert.deepEqual(fake.posts[0].payload, { accepted: true })
    assert.equal(scope.readPendingAddonRefundDecision(storage, "decision"), null)
  })
}
