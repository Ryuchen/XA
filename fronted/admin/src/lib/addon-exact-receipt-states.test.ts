/// <reference types="node" />
import test from "node:test"
import assert from "node:assert/strict"
import { lookupExactAddonReceipt } from "./addon-execution.ts"

const target = { orderId: 3, paymentId: 8, caseId: 12, caseRequestId: "case-1", accounts: [20], segments: [32] }
const exact = { order_id: 3, payment_id: 8, proposal_id: 50, case_id: 12, case_request_id: "case-1", refund_executed: true,
  execution_receipt: { refund_id: 60, buyer_transaction_id: 70, amount_raw: 500, executed_at: "2026-11-25T10:00:00Z" } }

test("精确回执状态矩阵：已验证、未执行、未知和异常均不附带执行资格", () => {
  const verified = lookupExactAddonReceipt(exact, 50, target, undefined)
  assert.equal(verified.status, "verified")
  assert.equal("execution_available" in verified, false)

  const unexecuted = lookupExactAddonReceipt({ ...exact, refund_executed: false, execution_receipt: null }, 50, target, undefined)
  assert.equal(unexecuted.status, "not-executed")
  assert.equal("execution_available" in unexecuted, false)

  const unknown = lookupExactAddonReceipt({ ...exact, execution_receipt: null }, 50, target, undefined)
  assert.equal(unknown.status, "unknown")
  assert.equal("execution_available" in unknown, false)

  for (const invalid of [null, {}, { ...exact, execution_receipt: { ...exact.execution_receipt, buyer_transaction_id: null } }, { ...exact, proposal_id: 51 }]) {
    assert.throws(() => lookupExactAddonReceipt(invalid as Record<string, unknown>, 50, target, undefined))
  }
})

test("空响应不能由精确回执校验器推断为未执行，页面须单独显示空状态", () => {
  assert.throws(() => lookupExactAddonReceipt(null as unknown as Record<string, unknown>, 50, target, undefined), /凭证|范围|核对/)
})
