import assert from "node:assert/strict"
import { test } from "node:test"
import { isUnreadCustomerMessage } from "../src/lib/chatNotice.ts"

test("only unread customer messages can trigger a chat alert", () => {
  assert.equal(isUnreadCustomerMessage({ id: 1, is_from_support: false, is_read: false }), true)
  assert.equal(isUnreadCustomerMessage({ id: 1, is_from_support: false, is_read: true }), false)
  assert.equal(isUnreadCustomerMessage({ id: 1, is_from_support: true, is_read: false }), false)
  assert.equal(isUnreadCustomerMessage({ id: 1, is_from_support: false }), false)
  assert.equal(isUnreadCustomerMessage({ id: 0, is_from_support: false, is_read: false }), false)
})
