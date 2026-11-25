/// <reference types="node" />
import assert from "node:assert/strict"
import test from "node:test"
import { parseReviewIntent, submitRoomCompletionReview, type ReviewIntent } from "./room-completion-review.ts"

const intent: ReviewIntent = {
  id: 7, order_id: 22,
  payload: { decision: "VERIFIED", note: "已核对双方凭证", client_request_id: "room-review-fixed-key" },
}
const receipt = { id: 7, order_id: 22, status: "VERIFIED", review_note: "已核对双方凭证" }

test("网络结果未知后，恢复原申请及完全相同的审核内容和请求号", async () => {
  let saved: string | null = null
  const sent: ReviewIntent[] = []
  const options = {
    save: (value: ReviewIntent) => { saved = JSON.stringify(value) },
    clear: () => { saved = null },
    send: async (value: ReviewIntent) => { sent.push(value); throw new Error("网络中断") },
  }
  await assert.rejects(submitRoomCompletionReview(intent, options), /网络中断/)
  const recovered = parseReviewIntent(saved)
  assert.deepEqual(recovered, intent)
  const result = await submitRoomCompletionReview(recovered!, { ...options, send: async value => { sent.push(value); return receipt } })
  assert.deepEqual(sent[0], sent[1])
  assert.equal(saved, null)
  assert.equal(result.status, "VERIFIED")
})

test("请求保存失败时不发送审核", async () => {
  let sent = false
  await assert.rejects(submitRoomCompletionReview(intent, {
    save: () => { throw new Error("存储不可用") },
    clear: () => {}, send: async () => { sent = true; return receipt },
  }), /存储不可用/)
  assert.equal(sent, false)
})

test("首次结果未知后遇到限流或权限暂失，恢复后仍能用同号同内容取回原回执", async () => {
  let saved: string | null = null
  const sent: ReviewIntent[] = []
  const storage = {
    save: (value: ReviewIntent) => { saved = JSON.stringify(value) },
    clear: () => { saved = null },
  }
  for (const failure of [new Error("网络中断"), ...[429, 403, 401, 408].map(status => ({ status, body: { code: status, msg: "稍后重试" } }))]) {
    const current = parseReviewIntent(saved) || intent
    await assert.rejects(submitRoomCompletionReview(current, { ...storage, send: async value => { sent.push(value); throw failure } }))
    assert.deepEqual(parseReviewIntent(saved), intent)
  }
  await submitRoomCompletionReview(parseReviewIntent(saved)!, { ...storage, send: async value => { sent.push(value); return receipt } })
  assert.equal(saved, null)
  for (const value of sent) assert.deepEqual(value, intent)
})

test("接口明确的业务拒绝允许重新核对决定；无法解析的错误响应继续保留原请求", async () => {
  for (const failure of [{ status: 400, body: { code: 400, msg: "请填写有效说明" } }, { status: 409, body: { code: 409, msg: "订单状态已变化，请核对" } }]) {
    let cleared = false
    await assert.rejects(submitRoomCompletionReview(intent, { save: () => {}, clear: () => { cleared = true }, send: async () => { throw failure } }))
    assert.equal(cleared, true)
  }
  let cleared = false
  await assert.rejects(submitRoomCompletionReview(intent, { save: () => {}, clear: () => { cleared = true }, send: async () => { throw { status: 400 } } }))
  assert.equal(cleared, false)
})

test("不接受错申请、错订单、错决策或错说明的回执，继续保留原请求", async () => {
  for (const changed of [{ id: 8 }, { order_id: 23 }, { status: "REJECTED" }, { review_note: "其他说明" }]) {
    let cleared = false
    await assert.rejects(submitRoomCompletionReview(intent, {
      save: () => {}, clear: () => { cleared = true }, send: async () => ({ ...receipt, ...changed }),
    }), /回执/)
    assert.equal(cleared, false)
  }
})

test("驳回回执按实际结果返回，不推造老板确认状态", async () => {
  const rejection = { ...intent, payload: { ...intent.payload, decision: "REJECTED" as const } }
  const result = await submitRoomCompletionReview(rejection, {
    save: () => {}, clear: () => {}, send: async () => ({ ...receipt, status: "REJECTED", confirmation: null }),
  })
  assert.equal(result.status, "REJECTED")
  assert.equal(result.confirmation, null)
})

test("无法核对的浏览器恢复记录不会生成新审核请求", () => {
  assert.equal(parseReviewIntent(null), null)
  for (const value of ["broken", JSON.stringify({ ...intent, id: 0 }), JSON.stringify({ ...intent, payload: { ...intent.payload, client_request_id: "x".repeat(65) } })]) {
    assert.throws(() => parseReviewIntent(value), /恢复记录/)
  }
})
