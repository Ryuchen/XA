import assert from "node:assert/strict"
import test from "node:test"
import { ApiError } from "../src/client.ts"
import {
  disabledStoreAnnouncement,
  storeEntryPresentation,
  type StoreEntryIssue,
} from "../src/store-closed.tsx"
import {
  storeEntryStatusIssueKind,
  storeEntryTransportIssueKind,
} from "../src/store-ui.tsx"

const checkedAt = new Date("2026-11-25T04:00:00Z")
const issue = (kind: StoreEntryIssue["kind"], message = "fixture"): StoreEntryIssue => ({
  kind,
  message,
  checkedAt,
})

test("only an explicit, consistent DISABLED current-store response is terminal disabled", () => {
  assert.equal(storeEntryStatusIssueKind({ status: "ACTIVE", is_active: true }), null)
  assert.equal(storeEntryStatusIssueKind({ status: "DISABLED", is_active: false }), "DISABLED")
  assert.equal(storeEntryStatusIssueKind({ status: "PROVISIONING", is_active: false }), "NOT_OPEN")
  assert.equal(storeEntryStatusIssueKind({ status: "CONFIGURING", is_active: false }), "NOT_OPEN")
  for (const value of [
    { status: "ACTIVE", is_active: false },
    { status: "DISABLED", is_active: true },
    { status: "UNKNOWN", is_active: false },
    { status: "ACTIVE", is_active: undefined },
  ]) assert.equal(storeEntryStatusIssueKind(value), "UNAVAILABLE")
})

test("ordinary 403 and 503 transport failures never become DISABLED", () => {
  assert.equal(storeEntryTransportIssueKind(new ApiError(
    "门店尚未开放或已停用，请联系门店管理员",
    403,
    { code: 403, msg: "门店尚未开放或已停用，请联系门店管理员" },
  )), "NOT_OPEN")
  for (const cause of [
    new ApiError("门店数据库尚未配置，请联系平台管理员", 403, { code: 403 }),
    new ApiError("门店不存在", 404, { code: 404 }),
    new ApiError("门店服务暂不可用，请稍后重试", 503, { code: 503 }),
  ]) assert.equal(storeEntryTransportIssueKind(cause), "UNAVAILABLE")
})

test("boss and provider disabled entries have no reopening promise", () => {
  for (const role of ["boss", "provider"] as const) {
    const disabled = storeEntryPresentation(role, issue("DISABLED"))
    assert.equal(disabled.title, "门店已停用")
    assert.equal(disabled.description, disabledStoreAnnouncement)
    assert.equal(disabled.reopening, false)
    assert.equal(disabled.showOtherEntry, false)
    assert.match(disabled.description, /不再接受新增业务/)
    assert.match(disabled.description, /不代表已完成退款或结清/)

    const notOpen = storeEntryPresentation(role, issue("NOT_OPEN"))
    assert.equal(notOpen.title, "门店暂未开放")
    assert.equal(notOpen.reopening, true)
    assert.equal(notOpen.showOtherEntry, true)
  }
})

test("ordinary entry errors remain query failures rather than disabled or not open", () => {
  for (const role of ["boss", "provider"] as const) {
    for (const message of ["门店数据库尚未配置，请联系平台管理员", "门店服务暂不可用，请稍后重试"]) {
      const result = storeEntryPresentation(role, issue("UNAVAILABLE", message))
      assert.equal(result.title, "暂时无法进入门店")
      assert.equal(result.description, message)
      assert.equal(result.reopening, false)
      assert.equal(result.showOtherEntry, false)
      assert.equal(result.alert, true)
    }
  }
})
