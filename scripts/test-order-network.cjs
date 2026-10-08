const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = createRequire(path.resolve('apps/provider-miniapp/package.json'))('typescript');
function load() {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync('apps/provider-miniapp/src/utils/orderNetwork.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, Date, Map, Math });
  return module.exports;
}
const pool = '/orders/orders/?status=pending&role=provider', grab = '/orders/orders/12/grab/';
test('RTT, p95, jitter separate transport failures from HTTP 429 and service errors', () => {
  const m = load();
  m.recordOrderRequest(pool, 100, 200, 1, 0, 1000);
  m.recordOrderRequest(grab, 300, 200, 1, 0, 2000);
  m.recordOrderRequest(grab, 400, 429, 1, 7, 3000);
  m.recordOrderRequest(pool, 15000, 0, 1, 0, 4000);
  m.recordOrderRequest(pool, 200, 503, 1, 0, 5000);
  const s = m.networkSnapshot(1, 5000);
  assert.equal(s.samples, 5); assert.equal(s.average_ms, 250); assert.equal(s.p95_ms, 400);
  assert.equal(s.jitter_ms, 167); assert.equal(s.failure_percent, 20);
  assert.equal(s.limited, 1); assert.equal(s.server_errors, 1); assert.equal(s.grab_wait, 5);
  assert.equal(m.cooldownRemaining('pool', 1, 5000), 0);
});
test('session change clears telemetry and cooldown; safe unrelated requests are excluded', () => {
  const m = load(); m.recordOrderRequest(grab, 100, 429, 1, 60, 1000);
  m.recordOrderRequest('/wallet/info/', 10, 0, 1, 0, 1000);
  m.recordOrderRequest(grab, 100, 401, 1, 0, 1000);
  assert.equal(m.networkSnapshot(1, 1000).samples, 1);
  assert.equal(m.networkSnapshot(2, 1000).samples, 0);
  assert.equal(m.cooldownRemaining('grab', 2, 1000), 0);
});
test('samples are bounded, stale results age out and successful reports are not resent', () => {
  const m = load(); for (let i=0; i<100; i++) m.recordOrderRequest(pool, 100, 200, 1, 0, 1000+i);
  const report = m.pendingNetworkReport(1, 1200); assert.equal(report.samples, 60);
  m.acknowledgeNetworkReport(report.cursor, 1); assert.equal(m.pendingNetworkReport(1, 1200).samples, 0);
  m.recordOrderRequest(grab, 120, 200, 1, 0, 2000); assert.equal(m.pendingNetworkReport(1, 2200).samples, 1);
  assert.equal(m.networkSnapshot(1, 40000).stale, true);
  assert.equal(m.networkSnapshot(1, 63000).samples, 0);
});
