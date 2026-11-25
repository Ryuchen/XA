/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { captureAddonExecution, checkAddonExecution, checkExactAddonExecution, executeAddonExecution, inspectExactAddonExecution, lookupExactAddonReceipt, readAddonReceiptTarget, readExecutedAddonProposal, resumeAddonExecution, sameSignedProposal, parseAddonExecution, verifiedAddonExecution, type AddonExecutionIntent } from "./addon-execution.ts"

const target = { orderId: 3, paymentId: 8, caseId: 12, caseRequestId: "case-cycle-1", accounts: [20], segments: [32] }
const signed = { id: 50, payment_id: 8, case_id: 12, case_request_id: "case-cycle-1", status: "ACCEPTED", amount_raw: 500, explanation: "核对原付款", accepted_at: "2026-11-24T12:00:00Z", participants: [{ account_id: 20, share_plan: [{ segment_id: 32 }] }], decisions: [{ role: "BOSS", account_id: 10, accepted: true, decided_at: "2026-11-24T11:00:00Z" }, { role: "PROVIDER", account_id: 20, accepted: true, decided_at: "2026-11-24T12:00:00Z" }], refund_executed: false, execution_available: true }

test("只捕获本工单轮次、付款及已签方案的正整数内部单位金额", () => {
  const intent = captureAddonExecution(signed, target)
  assert.equal(intent.proposalId, 50)
  assert.equal(intent.amountRaw, 500)
})
test("ACCEPTED不能代替执行资格，签字未齐、金额未知或已执行均不发起新执行", () => {
  for (const changed of [{ execution_available: false }, { execution_available: undefined }, { refund_executed: true }, { refund_executed: undefined }, { amount_raw: null }, { status: "PENDING" }, { decisions: signed.decisions.slice(0, 1) }, { decisions: [{ ...signed.decisions[0], accepted: null }, signed.decisions[1]] }]) assert.throws(() => captureAddonExecution({ ...signed, ...changed }, target))
})
test("重读不能换列表另一方案、付款、工单轮次、金额或已签决定", () => {
  const intent = captureAddonExecution(signed, target)
  for (const changed of [{ id: 51 }, { payment_id: 9 }, { case_request_id: "new-cycle" }, { amount_raw: 600 }, { decisions: [{ ...signed.decisions[0], decided_at: "2026-11-24T10:00:00Z" }, signed.decisions[1]] }]) assert.throws(() => sameSignedProposal({ ...signed, ...changed }, intent))
  sameSignedProposal(signed, intent)
})
test("恢复自然键按门店账号外层隔离，并拒绝换工单、付款与损坏金额", () => {
  const intent: AddonExecutionIntent = captureAddonExecution(signed, target)
  assert.deepEqual(parseAddonExecution(JSON.stringify(intent), target), intent)
  assert.throws(() => parseAddonExecution(JSON.stringify(intent), { ...target, paymentId: 9 }))
  assert.throws(() => parseAddonExecution(JSON.stringify({ ...intent, amountRaw: 0 }), target))
})
const executed = { ...signed, refund_executed: true, execution_available: false, execution_receipt: { refund_id: 60, buyer_transaction_id: 70, amount_raw: 500, executed_at: "2026-11-24T12:30:00Z" } }
test("首次执行的重读失败、资格丢失或金额变化时不POST，也不保存待执行记录", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const read of [async () => { throw { status: 400 } }, async () => [{ ...signed, execution_available: false }], async () => [{ ...signed, amount_raw: 600 }]]) {
    let sent = false, saved = false
    await assert.rejects(executeAddonExecution(intent, { read, save: () => { saved = true }, send: async () => { sent = true; return executed }, clear: () => {} }))
    assert.equal(sent, false); assert.equal(saved, false)
  }
})
test("POST断线保存同一自然键，GET只有真实退款和返款流水证明后才清", async () => {
  const intent = captureAddonExecution(signed, target)
  let raw: string | null = null
  const io = { read: async () => [signed], save: (value: AddonExecutionIntent) => { raw = JSON.stringify(value) }, clear: () => { raw = null } }
  await assert.rejects(executeAddonExecution(intent, { ...io, send: async () => { throw { status: 0 } } }))
  assert.deepEqual(parseAddonExecution(raw, target), intent)
  await assert.rejects(checkAddonExecution(intent, { read: async () => [{ ...executed, execution_receipt: null }], clear: io.clear }))
  assert.notEqual(raw, null)
  await checkAddonExecution(intent, { read: async () => [executed], clear: io.clear })
  assert.equal(raw, null)
})
test("历史GET范围被拒仅允许已保存原键的显式重试，403或读到错方案仍不POST", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const [status, retry, allowed] of [[400, false, false], [400, true, true], [401, true, false], [403, true, false]] as const) {
    let sent = false
    const run = executeAddonExecution(intent, { read: async () => { throw { status } }, save: () => {}, send: async value => { sent = true; assert.deepEqual(value, intent); return executed }, clear: () => {} }, retry)
    if (allowed) await run; else await assert.rejects(run)
    assert.equal(sent, allowed)
  }
  let sent = false
  await assert.rejects(executeAddonExecution(intent, { read: async () => [{ ...signed, id: 99 }], save: () => {}, send: async () => { sent = true; return executed }, clear: () => {} }, true))
  assert.equal(sent, false)
  await assert.rejects(executeAddonExecution(intent, { read: async () => { throw { code: "SESSION_CHANGED" } }, save: () => {}, send: async () => { sent = true; return executed }, clear: () => {} }, true))
  assert.equal(sent, false)
})
test("ACCEPTED、空退款证明、其他返款金额与缺流水均不能宣布已退款", () => {
  const intent = captureAddonExecution(signed, target)
  for (const changed of [signed, { ...executed, execution_receipt: null }, { ...executed, execution_receipt: { ...executed.execution_receipt, amount_raw: 600 } }, { ...executed, execution_receipt: { ...executed.execution_receipt, buyer_transaction_id: null } }]) assert.throws(() => verifiedAddonExecution(changed, intent))
})
test("刷新后的已执行GET没有本地待核对记录或创建人原号，也能按同方案核对真实回执", () => {
  const done = { ...signed, request_id: undefined, refund_executed: true, execution_available: false, execution_receipt: { refund_id: 80, buyer_transaction_id: 90, amount_raw: 500, executed_at: "2026-11-24T13:00:00Z" } }
  assert.equal(readExecutedAddonProposal(done, target).execution_receipt.refund_id, 80)
  assert.throws(() => readExecutedAddonProposal({ ...done, execution_receipt: null }, target))
  assert.throws(() => readExecutedAddonProposal({ ...done, case_request_id: "other-cycle" }, target))
})

const reopenedTarget = { ...target, caseRequestId: "case-cycle-2", accounts: [21], segments: [33] }
const originalOpening = { status: "OPEN", client_request_id: target.caseRequestId, addon_payment_id: target.paymentId, affected_provider_account_ids: target.accounts, affected_segment_ids: target.segments }
test("同ID重开后的旧轮次退款仅凭唯一OPEN原工单历史核对只读回执", () => {
  const history = [originalOpening, { ...originalOpening, status: "RESOLVED" }, { ...originalOpening, client_request_id: reopenedTarget.caseRequestId, affected_provider_account_ids: [21], affected_segment_ids: [33] }]
  const original = readAddonReceiptTarget(executed, reopenedTarget, history)
  assert.deepEqual(original, target)
  assert.equal(readExecutedAddonProposal(executed, original).execution_receipt.refund_id, 60)
  assert.deepEqual(reopenedTarget.accounts, [21])
  assert.throws(() => captureAddonExecution(signed, reopenedTarget))
})
test("旧轮次缺失、重复OPEN或付款/人员/履约段历史不符时维持未知，不信任方案自声明", () => {
  for (const history of [undefined, [], [originalOpening, originalOpening], [{ ...originalOpening, status: "RESOLVED" }], [{ ...originalOpening, addon_payment_id: 9 }], [{ ...originalOpening, affected_provider_account_ids: [99] }], [{ ...originalOpening, affected_segment_ids: [30] }]]) assert.throws(() => readAddonReceiptTarget(executed, reopenedTarget, history))
  assert.throws(() => readAddonReceiptTarget({ ...executed, payment_id: 9 }, reopenedTarget, [originalOpening]))
  assert.throws(() => readAddonReceiptTarget({ ...executed, case_id: 99 }, reopenedTarget, [originalOpening]))
})
test("当前新轮次仍使用当前工单范围，旧历史不会授予旧方案执行能力", () => {
  const current = { ...signed, id: 51, case_request_id: reopenedTarget.caseRequestId, participants: [{ account_id: 21, share_plan: [{ segment_id: 33 }] }], decisions: [signed.decisions[0], { ...signed.decisions[1], account_id: 21 }] }
  assert.equal(captureAddonExecution(current, reopenedTarget).proposalId, 51)
  assert.deepEqual(readAddonReceiptTarget(current, reopenedTarget, undefined), reopenedTarget)
  const currentDone = { ...current, refund_executed: true, execution_available: false, execution_receipt: { ...executed.execution_receipt, refund_id: 61, buyer_transaction_id: 71 } }
  assert.equal(readExecutedAddonProposal(currentDone, readAddonReceiptTarget(currentDone, reopenedTarget, [originalOpening])).execution_receipt.refund_id, 61)
  assert.throws(() => readAddonReceiptTarget({ ...current, participants: signed.participants }, reopenedTarget, [originalOpening]))
  readAddonReceiptTarget(executed, reopenedTarget, [originalOpening])
  assert.throws(() => captureAddonExecution(signed, reopenedTarget))
})

const exactReceipt = { order_id: 3, payment_id: 8, proposal_id: 50, case_id: 12, case_request_id: "case-cycle-1", refund_executed: true, execution_receipt: executed.execution_receipt }
test("精确只读凭证按保存原范围及金额核对，验证后清恢复且不需要列表或POST", async () => {
  const intent = captureAddonExecution(signed, target)
  let gets = 0, clears = 0
  const outcome = await checkExactAddonExecution(intent, { readExact: async () => { ++gets; return exactReceipt }, clear: () => { ++clears } })
  assert.equal(outcome.status, "verified")
  if (outcome.status === "verified") assert.equal(outcome.receipt.id, 50)
  assert.equal(gets, 1); assert.equal(clears, 1)
})
test("精确GET未执行与已有退款但凭证待核对都不清原键，不从回执授予执行资格", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const [refund_executed, status] of [[false, "not-executed"], [true, "unknown"]] as const) {
    let cleared = false
    const outcome = await checkExactAddonExecution(intent, { readExact: async () => ({ ...exactReceipt, refund_executed, execution_receipt: null }), clear: () => { cleared = true } })
    assert.equal(outcome.status, status)
    assert.equal(cleared, false)
    assert.equal("execution_available" in outcome, false)
  }
})
test("精确回执订单/付款/方案/工单/轮次/金额/流水任一不符时不清恢复", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const changed of [{ order_id: 4 }, { payment_id: 9 }, { proposal_id: 51 }, { case_id: 13 }, { case_request_id: "other" }, { execution_receipt: { ...executed.execution_receipt, amount_raw: 600 } }, { execution_receipt: { ...executed.execution_receipt, buyer_transaction_id: 0 } }]) {
    let cleared = false
    await assert.rejects(checkExactAddonExecution(intent, { readExact: async () => ({ ...exactReceipt, ...changed }), clear: () => { cleared = true } }))
    assert.equal(cleared, false)
  }
  assert.throws(() => inspectExactAddonExecution({ ...exactReceipt, refund_executed: undefined }, intent))
})
test("精确只读的403、404和会话变化均保留原自然键，不回退执行", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const error of [{ status: 403 }, { status: 404 }, { code: "SESSION_CHANGED" }]) {
    let cleared = false
    await assert.rejects(checkExactAddonExecution(intent, { readExact: async () => { throw error }, clear: () => { cleared = true } }))
    assert.equal(cleared, false)
  }
})
test("无本地意图且列表不可读时，可用已知原方案号和授权当前/历史工单范围只读核对", () => {
  assert.equal(lookupExactAddonReceipt(exactReceipt, 50, target, undefined).status, "verified")
  assert.equal(lookupExactAddonReceipt(exactReceipt, 50, reopenedTarget, [originalOpening]).status, "verified")
  for (const history of [undefined, [], [originalOpening, originalOpening], [{ ...originalOpening, addon_payment_id: 9 }], [{ ...originalOpening, affected_segment_ids: null }]]) assert.throws(() => lookupExactAddonReceipt(exactReceipt, 50, reopenedTarget, history))
  for (const changed of [{ order_id: 9 }, { payment_id: 9 }, { case_id: 9 }, { proposal_id: 9 }]) assert.throws(() => lookupExactAddonReceipt({ ...exactReceipt, ...changed }, 50, target, undefined))
})
test("原键恢复已核实时不读旧列表也不再POST，未知证据同样不执行", async () => {
  const intent = captureAddonExecution(signed, target)
  for (const data of [exactReceipt, { ...exactReceipt, execution_receipt: null }]) {
    let reads = 0, posts = 0, clears = 0
    const outcome = await resumeAddonExecution(intent, { readExact: async () => data, read: async () => { ++reads; throw { status: 400 } }, save: () => {}, send: async () => { ++posts; return executed }, clear: () => { ++clears } })
    assert.equal(reads, 0); assert.equal(posts, 0)
    assert.equal(outcome.status, data.execution_receipt ? "verified" : "unknown")
    assert.equal(clears, data.execution_receipt ? 1 : 0)
  }
})
test("原键显式重试未执行记录仍须标准列表确认资格，历史列表400不能转为POST", async () => {
  const intent = captureAddonExecution(signed, target)
  const unexecuted = { ...exactReceipt, refund_executed: false, execution_receipt: null }
  let posts = 0
  const io = { readExact: async () => unexecuted, save: () => {}, send: async () => { ++posts; return executed }, clear: () => {} }
  await assert.rejects(resumeAddonExecution(intent, { ...io, read: async () => { throw { status: 400 } } }))
  await assert.rejects(resumeAddonExecution(intent, { ...io, read: async () => [{ ...signed, execution_available: false }] }))
  assert.equal(posts, 0)
  assert.equal((await resumeAddonExecution(intent, { ...io, read: async () => [signed] })).status, "verified")
  assert.equal(posts, 1)
})
