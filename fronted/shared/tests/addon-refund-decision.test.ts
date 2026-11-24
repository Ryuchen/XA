import test from "node:test"
import assert from "node:assert/strict"
import {
  addonRefundProposalPath,
  loadAddonRefundProposal,
  loadAddonRefundProposalTargets,
  ownAddonRefundDecision,
  readAddonRefundDecision,
  recordAddonRefundDecision,
  type AddonRefundProposal,
} from "../src/addon-refund-decision.ts"

function fixture() {
  const values = new Map<string, string>()
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  }
  const proposal: AddonRefundProposal = {
    id: 8, payment_id: 5, addon_name: "独立加购", amount_raw: 500,
    provider_burden_raw: 500, provider_income_raw: 800, provider_income_retained_raw: 300,
    explanation: "退还本次未履行的加购", status: "PENDING", refund_executed: false,
    decisions: [{ account_id: 9, role: "BOSS", accepted: null, decided_at: null },
      { account_id: 12, role: "PROVIDER", accepted: true, decided_at: "2026-11-24" }],
  }
  const decision = { paymentId: 5, proposalId: 8, accepted: true }
  const options = { storage, key: "this-store:boss:9:order7", orderId: 7, accountId: 9, role: "boss" as const, decision }
  return { storage, values, proposal, decision, options }
}

test("signs only the selected payment and proposal using the exact accepted payload", async () => {
  const { storage, proposal, decision, options } = fixture()
  const path = addonRefundProposalPath(7, 5, 8)
  const calls: unknown[] = []
  const client = {
    async get<T>(url: string) {
      assert.equal(url, path)
      assert.deepEqual(readAddonRefundDecision(storage, options.key), decision)
      calls.push(["GET", url])
      return proposal as T
    },
    async post<T>(url: string, payload: unknown) {
      calls.push(["POST", url, payload])
      proposal.decisions[0].accepted = true
      return proposal as T
    },
  }
  const result = await recordAddonRefundDecision({ ...options, client })
  assert.equal(result.refund_executed, false)
  assert.deepEqual(calls, [["GET", path], ["POST", path, { accepted: true }]])
  assert.equal(readAddonRefundDecision(storage, options.key), null)
})

test("a lost successful response is recovered by reading the original own decision", async () => {
  const { storage, proposal, decision, options } = fixture()
  let posts = 0
  const client = {
    async get<T>() { return proposal as T },
    async post<T>(): Promise<T> {
      posts++
      proposal.decisions[0].accepted = true
      throw new Error("回包丢失")
    },
  }
  await assert.rejects(recordAddonRefundDecision({ ...options, client }), /回包丢失/)
  assert.deepEqual(readAddonRefundDecision(storage, options.key), decision)
  await recordAddonRefundDecision({ ...options, client })
  assert.equal(posts, 1)
  assert.equal(readAddonRefundDecision(storage, options.key), null)
})

test("pending recovery keeps the original accepted value and target", async () => {
  const { storage, decision, options } = fixture()
  storage.setItem(options.key, JSON.stringify(decision))
  const client = {
    async get<T>(): Promise<T> { throw new Error("must not read another target") },
    async post<T>(): Promise<T> { throw new Error("must not reverse the decision") },
  }
  await assert.rejects(recordAddonRefundDecision({ ...options, client, decision: { ...decision, accepted: false } }), /原方案和原决定/)
  await assert.rejects(recordAddonRefundDecision({ ...options, client, decision: { ...decision, paymentId: 6 } }), /原方案和原决定/)
  assert.deepEqual(readAddonRefundDecision(storage, options.key), decision)
})

test("provider signs only their own decision and server errors preserve the original intent", async () => {
  const { storage, proposal, decision, options } = fixture()
  proposal.decisions[1].accepted = null
  const client = {
    async get<T>() { return proposal as T },
    async post<T>(path: string, payload: unknown): Promise<T> {
      assert.equal(path, addonRefundProposalPath(7, 5, 8))
      assert.deepEqual(payload, { accepted: false })
      throw new Error("加购退款金额已变化，请联系客服")
    },
  }
  await assert.rejects(recordAddonRefundDecision({ ...options, role: "provider", accountId: 12,
    decision: { ...decision, accepted: false }, client }), /金额已变化/)
  assert.deepEqual(readAddonRefundDecision(storage, options.key), { ...decision, accepted: false })
  assert.throws(() => ownAddonRefundDecision(proposal, 13, "provider"), /本人确认记录/)
})

test("missing own decision, wrong proposal and damaged stored intents prevent a POST", async () => {
  for (const variant of ["missing", "wrong", "damaged"]) {
    const { storage, proposal, options } = fixture()
    if (variant === "missing") proposal.decisions = []
    if (variant === "wrong") proposal.payment_id = 6
    if (variant === "damaged") storage.setItem(options.key, "bad json")
    const client = {
      async get<T>() { return proposal as T },
      async post<T>(): Promise<T> { throw new Error("POST must not run") },
    }
    await assert.rejects(recordAddonRefundDecision({ ...options, client }), /本人确认记录|原加购退款方案|原确认凭证/)
    assert.notEqual(readAddonRefundDecision(storage, options.key), null)
  }
})

test("a recorded refusal is read back without changing to agreement or executing a refund", async () => {
  const { proposal, options } = fixture()
  proposal.status = "REJECTED"
  proposal.decisions[0].accepted = false
  const client = {
    async get<T>() { return proposal as T },
    async post<T>(): Promise<T> { throw new Error("must only read the existing refusal") },
  }
  const result = await recordAddonRefundDecision({ ...options, decision: { paymentId: 5, proposalId: 8, accepted: false }, client })
  assert.equal(result.status, "REJECTED")
  assert.equal(result.refund_executed, false)
})

test("discovery stays lightweight and a failed historical detail does not prevent choosing another proposal", async () => {
  const { proposal } = fixture()
  const discovery = "/orders/orders/7/addons/refund-proposals/"
  const oldPath = addonRefundProposalPath(7, 4, 6)
  const currentPath = addonRefundProposalPath(7, 5, 8)
  const targets = [
    { payment_id: 5, proposal_id: 8, status: "PENDING", my_accepted: null },
    ...Array.from({ length: 1000 }, (_, index) => ({ payment_id: 4, proposal_id: index + 10, status: "SUPERSEDED", my_accepted: true })),
    { payment_id: 4, proposal_id: 6, status: "REJECTED", my_accepted: false },
  ]
  const calls: string[] = []
  const client = {
    async get<T>(path: string): Promise<T> {
      calls.push(path)
      if (path === discovery) return targets as T
      if (path === currentPath) return proposal as T
      throw new Error("历史付款方案已结束")
    },
  }
  const listed = await loadAddonRefundProposalTargets(client, 7)
  assert.equal(listed.length, 1002)
  assert.deepEqual(calls, [discovery])
  await assert.rejects(loadAddonRefundProposal(client, 7, 4, 6, 9, "boss"), /历史付款方案已结束/)
  assert.equal((await loadAddonRefundProposal(client, 7, 5, 8, 9, "boss")).id, 8)
  assert.deepEqual(calls, [discovery, oldPath, currentPath])
  assert.equal(listed[0].status, "PENDING")
})

test("selected detail always uses the new order, target and account scope", async () => {
  const { proposal } = fixture()
  const calls: string[] = []
  const client = {
    async get<T>(path: string): Promise<T> {
      calls.push(path)
      return { ...proposal, id: 20, payment_id: 12,
        decisions: [{ account_id: 22, role: "PROVIDER", accepted: null, decided_at: null }] } as T
    },
  }
  assert.equal((await loadAddonRefundProposal(client, 19, 12, 20, 22, "provider")).payment_id, 12)
  await assert.rejects(loadAddonRefundProposal(client, 19, 12, 20, 9, "boss"), /本人确认记录/)
  assert.deepEqual(calls, [addonRefundProposalPath(19, 12, 20), addonRefundProposalPath(19, 12, 20)])
})
