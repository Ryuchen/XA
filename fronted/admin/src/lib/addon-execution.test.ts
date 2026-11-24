/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { captureAddonExecution, checkAddonExecution, executeAddonExecution, readExecutedAddonProposal, sameSignedProposal, parseAddonExecution, verifiedAddonExecution, type AddonExecutionIntent } from "./addon-execution.ts"

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
