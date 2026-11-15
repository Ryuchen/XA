import test from "node:test";
import assert from "node:assert/strict";
import { coinText, coinUnit, registerStoreCurrency } from "../src/store.ts";

test("missing store currency never falls back to another store's brand", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "location");
  try {
    Object.defineProperty(globalThis, "location", { configurable: true, value: new URL("http://localhost/?store=unconfigured") });
    assert.equal(coinUnit(), "店币");
    registerStoreCurrency("unconfigured", "  ");
    assert.equal(coinUnit(), "店币");
    registerStoreCurrency("unconfigured", "新币");
    assert.equal(coinUnit(), "新币");
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "location", descriptor);
    else Reflect.deleteProperty(globalThis, "location");
  }
});

test("store coin names stay scoped without changing amounts or dynamic names", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "location");
  try {
    registerStoreCurrency("shenglang", "浪花");
    registerStoreCurrency("xinan", "星辉");
    Object.defineProperty(globalThis, "location", { configurable: true, value: new URL("http://localhost/?store=xinan") });
    assert.equal(coinUnit(), "星辉");
    assert.equal(coinText("可提现浪花 · 10 浪花"), "可提现星辉 · 10 星辉");
    const nickname = "浪花";
    assert.equal(`${nickname} 收到 ${120} ${coinUnit()}`, "浪花 收到 120 星辉");
    Object.defineProperty(globalThis, "location", { configurable: true, value: new URL("http://localhost/?store=shenglang") });
    assert.equal(coinUnit(), "浪花");
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "location", descriptor);
    else Reflect.deleteProperty(globalThis, "location");
  }
});
