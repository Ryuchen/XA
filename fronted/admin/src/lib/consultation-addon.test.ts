/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { checkedAddonCase, returnedAddonScope, parseAddonIntent, submitAddonCase, type AddonCaseIntent } from "./consultation-addon.ts"

const payment = { order_id: 4, paid: true, payment_id: 8, provider_account_id: 20, segment_id: 30 }
const handover = (version: number, target: number, segment: number) => ({ payment_id: 8, version, kind: "STANDARD", status: "ACCEPTED", accepted_at: "2026-11-24T12:00:00Z", source_provider_account_id: target === 20 ? 21 : 20, target_provider_account_id: target,
  decisions: [{ role: "BOSS", account_id: 10, accepted: true, decided_at: "2026-11-24" }, { role: "PROVIDER", account_id: 20, accepted: true, decided_at: "2026-11-24" }, { role: "PROVIDER", account_id: 21, accepted: true, decided_at: "2026-11-24" }],
  shares: [{ segment_id: 30, provider_account_id: 20, is_current: false, basis_raw: 400 }, { segment_id: segment, provider_account_id: target, is_current: true, basis_raw: 400 }] })
const rows = [handover(2, 20, 32), handover(1, 21, 31)]
const intent: AddonCaseIntent = { session_id: 6, payload: { order_id: 4, chat_message_id: 7, addon_payment_id: 8, affected_provider_account_ids: [20], affected_segment_ids: [32], reason: "核对本笔加购", client_request_id: "consultation-fixed-key" } }
const receipt = { id: 50, order: 4, addon_payment: 8, client_request_id: intent.payload.client_request_id, status: "OPEN", reason: intent.payload.reason, affected_provider_account_ids: [20], affected_segment_ids: [32], history: [{ ...intent.payload, consultation_message_id: 7, consultation_session_id: 6 }] }

test("同账号首段和转回段只选择该付款最新已确认的当前段", () => {
  assert.deepEqual(returnedAddonScope(payment, rows), { provider: 20, segment: 32, version: 2 })
})
test("待签、错付款、零额、缺确认或原段均不作为可提交转回范围", () => {
  for (const changed of [[{ ...rows[0], status: "PENDING" }, rows[1]], [{ ...rows[0], payment_id: 9 }, rows[1]], [{ ...rows[0], decisions: [] }, rows[1]], [{ ...rows[0], shares: [{ segment_id: 32, provider_account_id: 20, is_current: true, basis_raw: 0 }] }, rows[1]], [handover(2, 20, 30), rows[1]]]) {
    assert.throws(() => returnedAddonScope(payment, changed), /核对|确认|转回/)
  }
  assert.throws(() => returnedAddonScope({ ...payment, paid: false }, rows))
  for (const status of ["REJECTED", "SUPERSEDED"]) assert.throws(() => returnedAddonScope(payment, [{ ...rows[0], version: 3, status }, ...rows]))
  assert.throws(() => returnedAddonScope(payment, [rows[0], { ...rows[1], decisions: [] }]))
})
test("断线及权限/限流/冲突后，恢复同咨询同付款同段及原请求号", async () => {
  let saved: string | null = null
  const sent: AddonCaseIntent[] = []
  const io = { save: (value: AddonCaseIntent) => { saved = JSON.stringify(value) }, clear: () => { saved = null } }
  for (const failure of [new Error("断线"), ...[403, 429, 409].map(status => ({ status, body: { code: status, msg: "核对原申请" } }))]) {
    await assert.rejects(submitAddonCase(parseAddonIntent(saved, 6, 7, 4) || intent, { ...io, send: async value => { sent.push(value); throw failure } }))
    assert.deepEqual(parseAddonIntent(saved, 6, 7, 4), intent)
  }
  await submitAddonCase(parseAddonIntent(saved, 6, 7, 4)!, { ...io, send: async value => { sent.push(value); return receipt } })
  assert.equal(saved, null)
  sent.forEach(value => assert.deepEqual(value, intent))
})
test("错误付款/咨询/履约段回执不冒充工单受理", async () => {
  for (const changed of [{ addon_payment: 9 }, { affected_segment_ids: [30] }, { history: [{ ...receipt.history[0], consultation_message_id: 99 }] }]) {
    let cleared = false
    await assert.rejects(submitAddonCase(intent, { save: () => {}, clear: () => { cleared = true }, send: async () => ({ ...receipt, ...changed }) }), /回执/)
    assert.equal(cleared, false)
  }
})
test("原工单已处理或已更新时，按历史原号核对并返回当前真实状态", () => {
  const updated = { ...receipt, client_request_id: "new-request", status: "RESOLVED", reason: "更新说明", affected_segment_ids: [99], addon_payment: 9 }
  assert.equal(checkedAddonCase(intent, updated).status, "RESOLVED")
  assert.throws(() => checkedAddonCase(intent, { ...updated, history: [{ ...receipt.history[0], addon_payment_id: 9 }] }), /回执/)
})
test("恢复记录不能跨会话、消息或订单；保存失败不发送", async () => {
  assert.throws(() => parseAddonIntent(JSON.stringify(intent), 9, 7, 4))
  assert.throws(() => parseAddonIntent(JSON.stringify(intent), 6, 9, 4))
  assert.throws(() => parseAddonIntent(JSON.stringify(intent), 6, 7, 9))
  let sent = false
  await assert.rejects(submitAddonCase(intent, { save: () => { throw new Error("存储失败") }, clear: () => {}, send: async () => { sent = true; return receipt } }))
  assert.equal(sent, false)
})

test("断线后原号重放遇业务400，仍能保留原号并从GET历史核对原工单", async () => {
  let saved: string | null = null
  const io = { save: (value: AddonCaseIntent) => { saved = JSON.stringify(value) }, clear: () => { saved = null } }
  await assert.rejects(submitAddonCase(intent, { ...io, send: async () => { throw new Error("断线") } }))
  await assert.rejects(submitAddonCase(parseAddonIntent(saved, 6, 7, 4)!, { ...io, recovering: true, send: async () => { throw { status: 400, body: { code: 400, msg: "当前资金范围已变化" } } } }))
  assert.deepEqual(parseAddonIntent(saved, 6, 7, 4), intent)
  assert.equal(checkedAddonCase(parseAddonIntent(saved, 6, 7, 4)!, receipt).id, 50)
})
