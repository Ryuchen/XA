import assert from "node:assert/strict"
import { test } from "node:test"
import { adminRealtimeTypes, isAdminRealtimeInvalidation } from "../src/lib/realtime.ts"

test("order regions ignore heartbeat, chat and unrelated financial events", () => {
  const types = adminRealtimeTypes("/orders")
  for (const type of ["pong", "connected", "chat_message", "wallet_update", "claim_queue_update", "unknown"])
    assert.equal(isAdminRealtimeInvalidation(type, types), false, type)
  assert.equal(isAdminRealtimeInvalidation("order_status_update", types), true)
  assert.equal(isAdminRealtimeInvalidation("reconnected", types), true)
})

test("operations tabs invalidate their own source rather than all operations", () => {
  const claims = adminRealtimeTypes("/club-operations", "?tab=claims")
  assert.equal(isAdminRealtimeInvalidation("claim_queue_update", claims), true)
  assert.equal(isAdminRealtimeInvalidation("club_feed_update", claims), false)
  const feed = adminRealtimeTypes("/club-operations", "?tab=feed")
  assert.equal(isAdminRealtimeInvalidation("club_feed_update", feed), true)
  assert.equal(isAdminRealtimeInvalidation("claim_queue_update", feed), false)
})

test("pages with no matching resource event never react to arbitrary messages", () => {
  for (const page of ["/roles", "/provider-passes", "/service-items", "/unknown"])
    assert.equal(isAdminRealtimeInvalidation("chat_message", adminRealtimeTypes(page)), false)
  assert.equal(isAdminRealtimeInvalidation("wallet_update", adminRealtimeTypes("/provider-passes")), true)
  assert.equal(isAdminRealtimeInvalidation("site_message_update", adminRealtimeTypes("/messages")), true)
})
