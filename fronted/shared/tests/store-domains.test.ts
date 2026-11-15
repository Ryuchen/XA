import { test } from "node:test";
import assert from "node:assert/strict";
import { getStoreSlug, hostStoreSlug, storeUrl, switchStore, canonicalizeStoreUrl } from "../src/store.ts";

test("a bound store domain ignores attempts to switch via root or hash parameters", () => {
  for (const [host, slug] of [["sl.esports.club", "shenglang"], ["xa.esports.club", "xinan"]]) {
    Object.assign(globalThis, { location: { hostname: host, search: "?store=foreign", hash: "#/wallet?store=foreign" } });
    assert.equal(getStoreSlug(), slug);
  }
  assert.equal(hostStoreSlug("XA.ESPORTS.CLUB.:443"), "xinan");
  assert.equal(hostStoreSlug("missing.esports.club"), "");
  assert.equal(hostStoreSlug("esports.club"), "");
  assert.equal(hostStoreSlug("sl.esports.club.attacker.example"), undefined);
});

test("tenant links stay on their host and public roles cannot invoke store switching", () => {
  for (const role of ["boss", "player"]) {
    Object.assign(globalThis, { location: { hostname: "sl.esports.club", href: `http://sl.esports.club/${role}/#/wallet`, assign: () => assert.fail("must not navigate") } });
    assert.throws(() => switchStore("xinan"), /专属入口/);
    assert.throws(() => storeUrl(`http://sl.esports.club/${role}/`, "xinan"), /不一致/);
    assert.equal(storeUrl(`http://sl.esports.club/${role}/?store=foreign`, "shenglang"), `http://sl.esports.club/${role}/`);
  }
});

test("canonical tenant entry removes store selectors but preserves the route and login intent", () => {
  let result = "";
  Object.assign(globalThis, {
    location: { hostname: "xa.esports.club", href: "http://xa.esports.club/boss/?store=shenglang#/login?next=%2Forders&store=shenglang" },
    history: { state: null, replaceState: (_state: unknown, _title: string, url: string) => { result = url; } },
  });
  canonicalizeStoreUrl();
  assert.equal(result, "http://xa.esports.club/boss/#/login?next=%2Forders");
});
