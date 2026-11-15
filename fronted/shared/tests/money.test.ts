import test from "node:test";
import assert from "node:assert/strict";
import { formatXaCoin, formatRmb, formatRmbFen, toRawAmount } from "@xa/money";

test("store coin amounts preserve the accounting unit and thousands separators", () => {
  assert.equal(formatXaCoin(0), "0");
  assert.equal(formatXaCoin(13200), "1,320");
  assert.equal(formatXaCoin(125), "12.5");
  assert.equal(formatXaCoin(-125), "-12.5");
  assert.equal(formatXaCoin(-13206), "-1,320.6");
  assert.equal(formatXaCoin(2024), "202.4");
  assert.equal(formatXaCoin(336), "33.6");
  assert.equal(formatXaCoin(Number.NaN), "0");
});

test("money input and RMB presentation keep their existing units", () => {
  assert.equal(toRawAmount("132.1"), 1321);
  assert.equal(formatRmb(13200), "132.00");
  assert.equal(formatRmbFen(13200), "132.00");
});
