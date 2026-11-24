/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { checkedFirstProposal, firstProposalBlocker, integerCoinRefund, parseFirstIntent, proposalsForCase, submitFirstProposal, verifyProposalScope, type AddonFirstIntent } from "./addon-first-proposal.ts"

const target = { orderId: 3, paymentId: 8, caseId: 12, caseRequestId: "case-cycle-1", accounts: [20], segments: [32] }
const proposal = { id: 50, payment_id: 8, case_id: 12, case_request_id: "case-cycle-1", request_id: "first-fixed-key", status: "PENDING", amount_raw: 500, explanation: "核对原付款", participants: [{ account_id: 20, share_plan: [{ segment_id: 32 }] }], decisions: [{ role: "BOSS", account_id: 10, accepted: null }, { role: "PROVIDER", account_id: 20, accepted: null }] }
const intent: AddonFirstIntent = { target, payload: { create_only: true, case_id: 12, case_request_id: "case-cycle-1", request_id: "first-fixed-key", amount_raw: 500, explanation: "核对原付款", affected_provider_account_ids: [20] } }

test("付款可有多个工单，只读取当前工单与付款同时匹配的方案", () => {
  assert.deepEqual(proposalsForCase([proposal, { ...proposal, id: 51, case_id: 13 }, { ...proposal, id: 52, payment_id: 9 }], target), [proposal])
  assert.throws(() => proposalsForCase(null, target), /读取/)
})
test("人民币与店币单位不混用，整数店币转换为十倍内部单位且拒绝溢出和小数", () => {
  assert.equal(integerCoinRefund("50"), 500)
  for (const value of ["", "0", "-1", "1.5", "1e2", " 50", "214748365", "9007199254740993"]) assert.equal(integerCoinRefund(value), null)
})
test("同账号原段不能代替工单转回段，付款、工单和陪玩均须一致", () => {
  verifyProposalScope(proposal, target)
  for (const changed of [{ payment_id: 9 }, { case_id: 13 }, { participants: [{ account_id: 21, share_plan: [{ segment_id: 32 }] }] }, { participants: [{ account_id: 20, share_plan: [{ segment_id: 30 }] }] }]) assert.throws(() => verifyProposalScope({ ...proposal, ...changed }, target), /范围/)
})
test("断线后遇权限/业务准入/首建冲突，原号与全部内容保留并可用GET精确恢复", async () => {
  let raw: string | null = null
  const sent: AddonFirstIntent[] = []
  const io = { save: (value: AddonFirstIntent) => { raw = JSON.stringify(value) }, clear: () => { raw = null } }
  for (const failure of [new Error("断线"), ...[400, 403, 409, 429].map(status => ({ status, body: { code: status, msg: "需要核对" } }))]) {
    await assert.rejects(submitFirstProposal(parseFirstIntent(raw, target) || intent, { ...io, recovering: raw !== null, send: async value => { sent.push(value); throw failure } }))
    assert.deepEqual(parseFirstIntent(raw, target), intent)
  }
  sent.forEach(value => assert.deepEqual(value, intent))
  assert.equal(checkedFirstProposal(proposal, parseFirstIntent(raw, target)!).id, 50)
})
test("金额相同的他人方案、其他轮次或缺原号的GET不证明本人原请求成功", () => {
  for (const changed of [{ request_id: undefined }, { request_id: "other-key" }, { case_request_id: "case-cycle-2" }, { amount_raw: 600 }, { decisions: [] }]) assert.throws(() => checkedFirstProposal({ ...proposal, ...changed }, intent), /回执|范围/)
})
test("本地存储失败不发送，明确首建回执后才释放原请求", async () => {
  let sent = false
  await assert.rejects(submitFirstProposal(intent, { save: () => { throw new Error("存储失败") }, clear: () => {}, send: async () => { sent = true; return proposal } }))
  assert.equal(sent, false)
  let cleared = false
  assert.equal((await submitFirstProposal(intent, { save: () => {}, clear: () => { cleared = true }, send: async () => proposal })).id, 50)
  assert.equal(cleared, true)
})
test("恢复记录不能换付款、工单或取消首建模式，工单新轮次也不会覆盖旧原号", () => {
  for (const changed of [{ ...target, paymentId: 9 }, { ...target, caseId: 13 }]) assert.throws(() => parseFirstIntent(JSON.stringify(intent), changed))
  assert.throws(() => parseFirstIntent(JSON.stringify({ ...intent, payload: { ...intent.payload, create_only: false } }), target))
  assert.deepEqual(parseFirstIntent(JSON.stringify(intent), { ...target, caseRequestId: "case-cycle-2" }), intent)
})

test("首次明确400允许修正金额，已有未知原号再遇400继续保留", async () => {
  let raw: string | null = null
  const io = { save: (value: AddonFirstIntent) => { raw = JSON.stringify(value) }, clear: () => { raw = null }, send: async () => { throw { status: 400, body: { code: 400, msg: "退款金额超过加购未退款金额" } } } }
  await assert.rejects(submitFirstProposal(intent, io))
  assert.equal(raw, null)
  await assert.rejects(submitFirstProposal(intent, { ...io, recovering: true }))
  assert.deepEqual(parseFirstIntent(raw, target), intent)
})
test("同付款其他工单或轮次未执行活跃方案阻止首建，已执行历史记录不冒充活跃", () => {
  const other = { ...proposal, case_id: 99, case_request_id: "old-cycle", refund_executed: false }
  assert.equal(firstProposalBlocker([other], target)?.id, 50)
  assert.equal(firstProposalBlocker([{ ...other, status: "ACCEPTED" }], target)?.id, 50)
  assert.equal(firstProposalBlocker([{ ...other, refund_executed: true }], target), undefined)
  assert.equal(firstProposalBlocker([{ ...proposal, status: "REJECTED" }], target)?.id, 50)
})
