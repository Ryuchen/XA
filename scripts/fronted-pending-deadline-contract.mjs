/** Real HTTP acceptance of the controller used by the React waiting pages. */
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { business as ui, login, rows, setBase } from './fronted-test-runtime.mjs';

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
const messages = lines[Symbol.asyncIterator]();
const first = await messages.next();
assert.equal(first.done, false, 'the isolated fixture must provide its HTTP session');
const input = JSON.parse(first.value);
setBase(input.base);
const boss = login(input.session.token, 'boss');
const provider = login(input.providerToken, 'provider');
const writes = [];
const clientFetch = globalThis.fetch;
globalThis.fetch = (target, options = {}) => {
  const method = String(options.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) writes.push({ target: String(target), method });
  return clientFetch(target, options);
};

const orderId = Number(input.orderId);
const target = records => records.find(row => Number(row.id) === orderId);
const readers = {
  detail: async () => [await boss.get(`/orders/orders/${orderId}/`)],
  list: async () => rows(await boss.get('/orders/orders/', { status: 'all' })),
  pool: async () => rows(await provider.get('/orders/orders/', { status: 'pending', role: 'provider' })),
};
const states = {}, refreshes = {}, completedAt = {}, disposers = {};
let failure;
const assertWaiting = records => {
  const order = target(records);
  assert.ok(order, 'the order must be present before its real persisted deadline');
  assert.equal(order.status, 'PENDING');
  assert.equal(order.payment_status, 'PAID');
  assert.equal(order.pending_deadline_expired, false);
  return order;
};
function arm(name) {
  disposers[name]?.();
  disposers[name] = ui.schedulePendingOrderRefresh(name === 'pool' ? 'provider' : 'boss', states[name], async () => {
    try {
      refreshes[name]++;
      states[name] = await readers[name]();
      completedAt[name] = Date.now();
      // React rerenders with the response and replaces the effect's schedule.
      // A server that still reports pending remains authoritative over the clock.
      arm(name);
    } catch (error) { failure = error; }
  });
}

try {
  for (const [name, read] of Object.entries(readers)) {
    states[name] = await read();
    assertWaiting(states[name]);
    refreshes[name] = 0;
  }
  for (const name of ['detail', 'list']) {
    assert.ok(target(states[name]).allowed_actions.includes('SELECT_PROVIDER'));
    assert.equal(ui.bossOrderStatusText(target(states[name])), '待接单');
  }
  assert.ok(ui.providerOrderActions(target(states.pool)).includes('GRAB'));
  process.stdout.write(JSON.stringify({ ready_for_deadline: true }) + '\n');

  const next = await messages.next();
  assert.equal(next.done, false, 'the fixture must arm the five-second persisted deadline after readiness');
  const { deadline } = JSON.parse(next.value);
  lines.close();
  const expiresAt = Date.parse(deadline);
  assert.ok(Number.isFinite(expiresAt));
  for (const [name, read] of Object.entries(readers)) {
    states[name] = await read();
    const waiting = assertWaiting(states[name]);
    assert.equal(Date.parse(waiting.auto_cancel_at), expiresAt, 'read the actual newly persisted deadline');
    arm(name);
  }

  const expired = () => target(states.detail)?.pending_deadline_expired &&
    target(states.list)?.pending_deadline_expired && !target(states.pool);
  // This loop only observes responses from the production scheduled callbacks.
  // It never performs the read that the controller is required to initiate.
  const stop = expiresAt + 6000;
  while (!expired() && !failure && Date.now() < stop)
    await new Promise(resolve => setTimeout(resolve, 25));
  if (failure) throw failure;
  for (const name of ['detail', 'list']) {
    const order = target(states[name]);
    assert.equal(order.pending_deadline_expired, true, `the open React ${name} must reread after the deadline`);
    assert.equal(ui.bossOrderStatusText(order), '已到接单期限，等待处理');
    assert.equal(order.allowed_actions.includes('SELECT_PROVIDER'), false);
    assert.equal(order.status, 'PENDING', 'the client must not cancel an expired order');
    assert.equal(order.payment_status, 'PAID', 'the client must not refund an expired order');
  }
  assert.equal(target(states.pool), undefined, 'the React pool must remove the order using the actual deadline-filtered API');
  for (const name of Object.keys(readers)) {
    assert.ok(refreshes[name] > 0, `${name} must actually perform its scheduled HTTP reread`);
    assert.ok(completedAt[name] >= expiresAt, `${name} must observe real server state after expiry`);
  }
  assert.equal((await boss.get('/wallet/info/')).balance, 10000);
  assert.equal((await provider.get('/wallet/info/')).balance, 0);
  assert.deepEqual(writes, [], 'deadline refresh must issue reads only; no cancel/refund write');
  console.log(JSON.stringify({ react_pending_deadline: 'passed', surfaces: Object.keys(readers),
    scheduledReads: refreshes, status: 'PENDING', paymentStatus: 'PAID', bossBalance: 10000, providerBalance: 0 }));
} finally {
  for (const dispose of Object.values(disposers)) dispose();
  lines.close();
}
