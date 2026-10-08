const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/utils/coalescedRefresh.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
function setup(refresh) {
  const timers = new Map();
  let next = 0;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Math: { ...Math, random: () => .5, floor: Math.floor },
    setTimeout: (fn, ms) => { timers.set(++next, { fn, ms }); return next; },
    clearTimeout: id => timers.delete(id) });
  return { personal: module.exports.isPersonalOrderUpdate, queue: module.exports.createCoalescedRefresh(refresh), timers, fire() {
    assert.equal(timers.size, 1);
    const [id, timer] = timers.entries().next().value;
    timers.delete(id);
    assert.equal(timer.ms, 400);
    return timer.fn();
  } };
}
test('a burst refreshes once and later events cannot postpone its timer', async () => {
  let count = 0;
  const s = setup(async () => { count++; });
  for (let i = 0; i < 100; i++) s.queue.request();
  await s.fire();
  assert.equal(count, 1);
  assert.equal(s.timers.size, 0);
});
test('events during an in-flight refresh produce one trailing read of final state', async () => {
  let finish, count = 0, state = 'pending';
  const seen = [];
  const s = setup(async () => { count++; const snapshot = state; await new Promise(r => finish = r); seen.push(snapshot); });
  s.queue.request();
  const first = s.fire();
  state = 'cancelled';
  for (let i = 0; i < 20; i++) s.queue.request();
  assert.equal(s.timers.size, 0);
  finish(); await first;
  const second = s.fire(); finish(); await second;
  assert.equal(count, 2);
  assert.deepEqual(seen, ['pending', 'cancelled']);
});
test('unmount cancels a pending timer and ignores later events', () => {
  const s = setup(async () => assert.fail('must not refresh'));
  s.queue.request(); s.queue.dispose(); s.queue.request();
  assert.equal(s.timers.size, 0);
});
test('unmount during a request prevents a queued follow-up', async () => {
  let finish;
  const s = setup(() => new Promise(r => finish = r));
  s.queue.request(); const first = s.fire();
  s.queue.request(); s.queue.dispose(); finish(); await first;
  assert.equal(s.timers.size, 0);
});
test('failed refresh releases its lock and a queued update can recover', async () => {
  let count = 0;
  const s = setup(async () => { if (++count === 1) { s.queue.request(); throw Error('offline'); } });
  s.queue.request(); await s.fire(); await s.fire();
  assert.equal(count, 2);
  assert.equal(s.timers.size, 0);
});
test('public pool invalidation does not reload personal income; full owned updates do', () => {
  const { personal } = setup(async () => {});
  for (const value of [null, undefined, {}, { id: 1, status: 'PENDING' }]) assert.equal(personal(value), false);
  assert.equal(personal({ id: 1, status: 'GRABBED', provider: { id: 3 } }), true);
  assert.equal(personal({ id: 1, status: 'CANCELLED', provider: null }), true);
});
