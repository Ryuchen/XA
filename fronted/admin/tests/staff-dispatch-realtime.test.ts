import assert from "node:assert/strict"
import { test } from "node:test"
import { createStaffDispatchNotice } from "../src/lib/staffDispatchNotice.ts"

test("a healthy queue waits for socket invalidation rather than minute polling", async () => {
  const timers: Array<{ callback: () => void; delay: number }> = []
  let calls = 0
  const controller = createStaffDispatchNotice({
    session: () => 1, allowed: () => true,
    retrieve: async () => { throw new Error("no pending order") },
    listPending: async () => { calls++; return { list: [], total: 0, staff_queue_needs_review: false } },
    queue: () => {}, show: () => ({ close() {} }), open: () => {},
    schedule: (callback, delay) => { const timer = { callback, delay }; timers.push(timer); return timer as never },
    cancel: () => {},
  })
  await controller.reconcile()
  assert.equal(calls, 1)
  assert.deepEqual(timers, [])
  await controller.receive({ id: 42, dispatch_route: "STAFF", status: "CANCELLED" })
  assert.equal(timers.length, 1)
  assert.equal(timers[0].delay, 500)
  timers[0].callback()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls, 2)
  assert.equal(timers.some(t => t.delay === 60000), false)
  controller.reset()
})
