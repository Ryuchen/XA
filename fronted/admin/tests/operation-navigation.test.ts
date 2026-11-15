import assert from "node:assert/strict"
import { test } from "node:test"
import { operationPages, resolveOperationLocation } from "../src/lib/operation-navigation.ts"
import { buildNavigation, navigationDomains, searchNavigation } from "../src/lib/navigation.ts"
import { adminRealtimeTypes } from "../src/lib/realtime.ts"

const routes = operationPages.map(page => ({ path: page.path, meta: { perm: page.permission } }))
const only = (permission: string) => (value: string | string[] | undefined) =>
  !!value && (Array.isArray(value) ? value : [value]).includes(permission)

test("fulfillment staff find their queue under service without gaining configuration access", () => {
  const menu = buildNavigation(routes, only("engagement:fulfill"))
  assert.deepEqual(menu.map(domain => domain.id), ["support"])
  assert.deepEqual(searchNavigation(menu, "权益").map(page => page.path), ["club-claims"])
  assert.equal(resolveOperationLocation("?tab=settings", only("engagement:fulfill")), null)
})

test("external point operators can register points without seeing VIP or integration settings", () => {
  const menu = buildNavigation(routes, only("engagement:points"))
  assert.deepEqual(menu.map(domain => domain.id), ["customers"])
  assert.deepEqual(searchNavigation(menu, "").map(page => page.path), ["external-points"])
  assert.equal(resolveOperationLocation("?tab=kook", only("engagement:points")), null)
  assert.equal(resolveOperationLocation("", only("engagement:points"))?.pathname, "/external-points")
})

test("queue links keep status, search and other task filters after entering the independent page", () => {
  const target = resolveOperationLocation("?tab=claims&status=PENDING&search=VIP&needs_tag_review=1", only("engagement:fulfill"))!
  assert.equal(target.pathname, "/club-claims")
  const filters = new URLSearchParams(target.search)
  assert.equal(filters.get("status"), "PENDING")
  assert.equal(filters.get("search"), "VIP")
  assert.equal(filters.get("needs_tag_review"), "1")
  assert.equal(filters.has("tab"), false)
  assert.equal(resolveOperationLocation("?tab=unknown", () => true), null)
})

test("operational pages each have one canonical sidebar destination across their owning modules", () => {
  const paths = navigationDomains.flatMap(domain => domain.centers.flatMap(center => center.features.map(feature => feature.path)))
  assert.equal(paths.length, new Set(paths).size)
  assert.equal(paths.includes("club-operations"), false)
  for (const page of operationPages) assert.equal(paths.filter(path => path === page.path).length, 1, page.path)
  const moduleFor = (path: string) => navigationDomains.find(domain => domain.centers.some(center => center.features.some(page => page.path === path)))?.id
  assert.equal(moduleFor("club-rewards"), "catalog-marketing")
  assert.equal(moduleFor("lotteries"), "catalog-marketing")
  assert.equal(moduleFor("recharge-packages"), "finance")
  assert.equal(moduleFor("provider-namings"), "providers")
  assert.equal(moduleFor("broadcast-rules"), "system")
})

test("moved pages keep their own realtime invalidations and ignore unrelated events", () => {
  for (const page of operationPages) {
    assert.deepEqual(adminRealtimeTypes(`/${page.path}`), adminRealtimeTypes("/club-operations", `?tab=${page.section}`), page.path)
  }
  assert.deepEqual(adminRealtimeTypes("/club-claims"), ["claim_queue_update"])
  assert.deepEqual(adminRealtimeTypes("/broadcasts"), ["club_feed_update"])
  assert.equal(adminRealtimeTypes("/vip-tiers").includes("claim_queue_update"), false)
})
