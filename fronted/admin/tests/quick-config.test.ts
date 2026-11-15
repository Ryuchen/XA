import assert from "node:assert/strict"
import { test } from "node:test"
import { checklistEditor, quickConfigGroups, type QuickCatalog } from "../src/lib/quick-config.ts"
import { buildNavigation, searchNavigation } from "../src/lib/navigation.ts"

const catalog: QuickCatalog = {
  legacy: [
    { id: "game-categories", can_view: true, permission: "service:view" },
    { id: "service-categories", can_view: true, permission: "service:view" },
  ],
  policies: [
    { id: "club-config", can_view: true, permission: "engagement:view" },
    { id: "provider-capacity", can_view: true, permission: "order:view" },
    { id: "withdraw", can_view: true, permission: "withdraw:view" },
    { id: "commission", can_view: true, permission: "report:view" },
    { id: "payment-fee", can_view: false, permission: "payment_fee:view" },
  ],
}
const only = (...permissions: string[]) => (permission: string) => permissions.includes(permission)
const entries = (setup: boolean, hasPerm: (permission: string) => boolean, data: QuickCatalog | null = catalog) =>
  quickConfigGroups(data, setup, hasPerm).flatMap(group => group.entries)

test("category shortcuts open distinct editors, and catalog denials are respected", () => {
  const items = entries(false, () => true)
  const links = items.flatMap(item => "to" in item ? [item.to] : [])
  assert.ok(links.includes("/service-items?config=games") && links.includes("/service-items?config=categories"))
  assert.ok(links.includes("/member-settings") && !links.includes("/dictionaries?section=points"))
  assert.ok(!links.includes("/payment-fee"))
})

test("view-only users retain the product list and scoped points without financial actions", () => {
  const items = entries(false, only("dictionary:view", "service:view", "withdraw:view", "report:view"))
  assert.ok(items.some(item => "to" in item && item.to === "/service-items"))
  assert.ok(items.some(item => "to" in item && item.to === "/dictionaries?section=points"))
  assert.ok(!items.some(item => "editor" in item && ["commission", "withdraw", "capacity"].includes(item.editor)))
  assert.ok(!items.some(item => "to" in item && item.to.includes("config=")))
})

test("setup stores reach scoped editors without opening operating pages", () => {
  const items = entries(true, () => true)
  assert.ok(items.every(item => !("to" in item) || item.to.startsWith("/dictionaries?") || item.to === "/deposit-rules"))
  assert.ok(items.some(item => "editor" in item && item.editor === "capacity"))
  assert.equal(checklistEditor("withdraw", true, only("dictionary:view")), undefined)
  assert.equal(checklistEditor("withdraw", true, only("withdraw:config")), "withdraw")
  assert.equal(checklistEditor("reservation_deadlines", true, () => true), undefined)
})

test("unread catalog preserves local entries without inferring remote access", () => {
  const items = entries(false, () => true, null)
  assert.ok(items.some(item => "editor" in item && item.editor === "order-account-fields"))
  assert.equal(items.filter(item => "to" in item && /section=(AD_CHANNEL|COST_CATEGORY|SALARY_POSITION)$/.test(item.to)).length, 3)
  assert.ok(!items.some(item => "to" in item && item.to === "/member-settings"))
})

test("both menu names find the same authorized shortcut page", () => {
  const routes = [{ path: "dictionaries", meta: { perm: "dictionary:view" } }]
  const nav = buildNavigation(routes, permission => permission === "dictionary:view")
  for (const query of ["统一字典", "快捷配置"])
    assert.deepEqual(searchNavigation(nav, query).map(item => item.title), ["快捷配置"])
  assert.deepEqual(searchNavigation(buildNavigation(routes, () => false), "快捷配置"), [])
})
