import assert from "node:assert/strict"
import test from "node:test"
import { formatProviderName, providerNameParts } from "../src/business/provider-name-format.ts"

test("each store renders its own display abbreviation without mutating the nickname", () => {
  const nickname = "小星"
  assert.equal(formatProviderName(nickname, "SL"), "SL-小星")
  assert.equal(formatProviderName(nickname, "XA"), "XA-小星")
  assert.equal(nickname, "小星")
})

test("existing display prefix is not duplicated and unconfigured prefixes are not guessed", () => {
  assert.deepEqual(providerNameParts("sl-小星", "SL"), { prefix: "SL", name: "小星" })
  assert.equal(formatProviderName("SL-小星", "SL"), "SL-小星")
  assert.equal(formatProviderName("SL-sl-小星", "SL"), "SL-小星")
  assert.equal(formatProviderName("小星", ""), "小星")
  assert.equal(formatProviderName("小星", "XA1"), "小星")
  assert.equal(formatProviderName(null, "XA"), "")
})
