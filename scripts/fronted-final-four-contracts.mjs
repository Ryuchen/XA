/** Current React controls + isolated HTTP/Redis; this is not DOM acceptance. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { business as ui, contractRealtime, login, raw, readInput, rows, setBase } from './fronted-test-runtime.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) { assert.ok(Date.now() < deadline, message); await sleep(10); }
}
const frames = [];
const RealWebSocket = globalThis.WebSocket;
globalThis.WebSocket = class extends RealWebSocket {
  constructor(url, ...args) {
    super(url, ...args);
    this.addEventListener('message', event => {
      try { frames.push({ url: String(url), message: JSON.parse(String(event.data)) }); } catch { /* Ignore non-JSON transport noise. */ }
    });
  }
};
async function connect(role, base, receive) {
  assert.ok(base, 'the realtime branch must use a distinct isolated Daphne/Redis endpoint');
  const before = frames.filter(row => row.message.type === 'pong').length;
  const close = await contractRealtime(role, base, receive);
  await until(() => frames.filter(row => row.message.type === 'pong').length > before,
    'the production React hub must receive a real server PONG');
  return close;
}

async function watch(role, read, relevant, wsBase) {
  let value = await read(), reads = 0, events = 0, error;
  async function reread() { value = await read(); reads++; return value; }
  const coordinator = ui.createRefreshCoordinator({ role, reload: async () => {
    try { await reread(); } catch (cause) { error = cause; }
  }, isVisible: () => true, delay: 1 });
  const close = wsBase ? await connect(role, wsBase, event => {
    if (relevant(event)) { events++; coordinator.event(event.type); }
  }) : () => {};
  return { value: () => value, counters: () => ({ reads, events }), reread,
    async after(before, predicate) {
      if (!wsBase) await reread();
      await until(() => error || ((wsBase ? events > before.events && reads > before.reads : true) && predicate(value)),
        'an actual Redis event must drive the React coordinator to reread the committed HTTP state');
      if (error) throw error;
      return value;
    }, dispose() { close(); coordinator.dispose(); } };
}

export async function reportReview(input) {
  setBase(input.base);
  const provider = login(input.token), boss = login(input.bossToken, 'boss'), admin = login(input.staffToken, 'admin');
  const readReport = async () => {
    const result = await ui.loadProviderReportPage(1, { orderId: String(input.orderId), reportId: String(input.reportId) });
    assert.equal(result.list.length, 1);
    return result.list[0];
  };
  const report = await watch('provider', readReport, event =>
    (event.type === 'report_status_update' && event.data?.report_id === input.reportId) ||
    (event.type === 'order_status_update' && event.data?.id === input.orderId), input.wsBase);
  const order = await watch('boss', () => boss.get(`/orders/orders/${input.orderId}/`),
    event => event.type === 'order_status_update' && event.data?.id === input.orderId, input.wsBase);
  try {
    assert.equal(report.value().status, 'PENDING');
    assert.equal(report.value().status_display, '待审核');
    assert.equal(report.value().result_image_urls.length, 1);
    assert.ok(order.value().consumption_ready_at);
    const firstSubmission = Date.parse(report.value().submitted_at);
    assert.equal(Date.parse(order.value().consumption_ready_at), firstSubmission + 72 * 3600000);
    let providerBefore = report.counters(), bossBefore = order.counters();
    await admin.post(`/admin/reports/${input.reportId}/reject/`, { audit_remark: '请补充清晰战绩' });
    const rejected = await report.after(providerBefore, row => row.status === 'REJECTED');
    await order.after(bossBefore, row => row.consumption_ready_at === null);
    assert.equal(rejected.status_display, '已驳回');
    assert.equal(rejected.audit_remark, '请补充清晰战绩');
    assert.equal(ui.providerReportPresentation(rejected).canEdit, true);
    assert.equal(order.value().report_ready_at, null);

    const uploaded = await ui.uploadProviderReportImages({ reportId: input.reportId, kind: 'RESULT',
      files: [new File([Buffer.from(input.image, 'base64')], 'evidence.png', { type: 'image/png' })],
      existingCount: rejected.result_image_urls.length, reload: report.reread });
    assert.equal(uploaded.error, undefined);
    assert.equal(uploaded.uploaded, 1);
    assert.equal(report.value().result_image_urls.length, 2);
    assert.equal(ui.providerReportPresentation(report.value()).canSubmit, true);
    providerBefore = report.counters(); bossBefore = order.counters();
    const submitted = await ui.mutateAndReload({ role: 'provider',
      mutate: () => provider.post(`/wallet/reports/${input.reportId}/submit/`, {}), reload: report.reread });
    assert.equal(submitted.error, undefined);
    const pending = await report.after(providerBefore, row => row.status === 'PENDING' && row.audit_remark === '');
    await order.after(bossBefore, row => !!row.consumption_ready_at);
    assert.equal(pending.status_display, '待审核');
    assert.equal(pending.result_image_urls.length, 2);
    assert.ok(Date.parse(pending.submitted_at) > firstSubmission);
    assert.equal(Date.parse(order.value().consumption_ready_at), Date.parse(pending.submitted_at) + 72 * 3600000);
    const restoredClock = order.value().consumption_ready_at;
    providerBefore = report.counters();
    await admin.post(`/admin/reports/${input.reportId}/approve/`, {});
    const approved = await report.after(providerBefore, row => row.status === 'APPROVED');
    // Approval before the window changes the report, not the already-restored
    // boss clock. Only its preceding clear/restore require order invalidations.
    assert.equal((await order.reread()).consumption_ready_at, restoredClock);
    assert.equal(approved.status_display, '已通过');
    assert.equal(ui.providerReportPresentation(approved).canEdit, false);
    assert.equal(ui.providerReportPresentation(approved).canSubmit, false);
    assert.equal(ui.providerReportPresentation(approved).expectedIncome, 8000);
    assert.equal((await provider.get('/wallet/info/')).balance, 0);
    assert.equal((await boss.get('/wallet/info/')).balance, 11000);
    console.log(JSON.stringify({ reactReportReview: 'passed', realtime: !!input.wsBase,
      provider: report.counters(), boss: order.counters(), resultImages: 2,
      clock: 'submission + 72h; cleared on rejection; restored on resubmission' }));
  } finally { report.dispose(); order.dispose(); }
}

export async function zeroIncome(input) {
  setBase(input.base);
  const provider = login(input.token);
  const order = await provider.get(`/orders/orders/${input.orderId}/`);
  const view = ui.providerOrderPresentation(order);
  assert.equal(order.status, 'GRABBED');
  assert.equal(order.payment_status, 'PAID');
  assert.equal(order.amount, 0);
  assert.equal(order.expected_income, 0);
  assert.equal(view.expectedIncome, 0);
  assert.equal(view.displayIncome, 0);
  assert.equal(view.incomeLabel, '预计');
  assert.ok(view.actions.includes('START'));
  assert.equal(view.zeroIncomeNotice, '本单预计收益为0浪花，请核对后继续。');
  assert.equal((await provider.get('/wallet/info/')).balance, 0);
  console.log(JSON.stringify({ reactFreeVipIncome: 'passed', amount: 0, expectedIncome: 0,
    displayIncome: 0, notice: view.zeroIncomeNotice, status: 'GRABBED',
    dom: 'not exercised here; general page acceptance is separate' }));
}

export async function poolAlerts(input) {
  const { createProviderOrderAlerts } = await import('../fronted/shared/src/provider-alerts.ts');
  setBase(input.base);
  const provider = login(input.token);
  let plays = 0, running = false, poolReads = 0, latest = [], events = 0, refreshes = 0, error;
  const readPool = async () => { latest = rows(await provider.get('/orders/orders/', { status: 'pending', role: 'provider' })); poolReads++; return latest; };
  const alerts = createProviderOrderAlerts({ readPool, isVisible: () => true,
    enabled: true, persistEnabled: enabled => localStorage.setItem('xa_fronted_provider_sound', enabled ? 'on' : 'off'),
    audio: { isRunning: () => running, unlock: () => { running = true; }, play: () => { plays++; } } });
  await alerts.unlock(); await alerts.start();
  assert.equal(plays, 0, 'initial pool history must not play a new-order alert');
  assert.ok(latest.some(row => row.id === input.orderId));
  const coordinator = ui.createRefreshCoordinator({ role: 'provider', isVisible: () => true, delay: 1,
    reload: async () => { try { await readPool(); refreshes++; } catch (cause) { error = cause; } } });
  const close = input.wsBase ? await connect('provider', input.wsBase, event => {
    if (event.type === 'order_status_update') { events++; coordinator.event(event.type); }
    void alerts.event(event).catch(cause => { error = cause; });
  }) : () => {};
  try {
    const ids = [input.orderId];
    for (let index = 0; index < 2; index++) {
      const beforeEvents = events, beforeReads = poolReads, beforeRefreshes = refreshes;
      const created = await raw(input.bossToken, '/orders/create/', 'POST', {
        service_id: input.serviceId, dispatch_route: 'PUBLIC', dispatch_mode: 'AUTO_GRAB',
        client_request_id: 'react-alert-' + crypto.randomUUID() });
      ids.push(created.id);
      if (!input.wsBase) await alerts.pool();
      await until(() => error || (poolReads > beforeReads && ids.every(id => latest.some(row => row.id === id)) &&
        (!input.wsBase || events > beforeEvents && refreshes > beforeRefreshes) && (index === 1 || plays === 1)),
        'real order creation must reach the actual React alert controller and pool read');
      if (error) throw error;
      assert.equal(plays, 1, index ? 'muted new orders must not request audio playback' : 'one new order plays exactly once');
      if (!index) {
        assert.equal(alerts.toggle(), false);
        assert.equal(localStorage.getItem('xa_fronted_provider_sound'), 'off');
      }
    }
    assert.equal(alerts.toggle(), true);
    await alerts.pool();
    assert.equal(plays, 1, 're-reading existing order IDs must not play them again after unmuting');
    assert.equal(ids.length, 3);
    console.log(JSON.stringify({ reactPoolAlerts: 'passed', realtime: !!input.wsBase, events, refreshes,
      paidOrders: ids.length, audioCalls: plays, mutedAndDeduplicated: true, hardwareAudio: 'not exercised' }));
  } finally { close(); coordinator.dispose(); alerts.dispose(); }
}

async function transferObserver(input) {
  setBase(input.base);
  const index = input.observer, provider = input.providers[index], client = login(provider.token);
  const read = async () => {
    try { return await client.get(`/orders/orders/${input.orderId}/`); }
    catch (error) { if (index === 1 && error.code === 404) return null; throw error; }
  };
  const order = await watch('provider', read, event => event.type === 'order_status_update' && event.data?.id === input.orderId, input.wsBase);
  try {
    if (index === 1) assert.equal(order.value(), null, 'incoming provider cannot read the order before transfer');
    else { assert.equal(order.value().status, 'IN_SERVICE'); assert.equal(order.value().my_assignment_released, false); }
    const before = order.counters();
    console.log(JSON.stringify({ phase: 'ready' }));
    const changed = await order.after(before, row => row && row.my_assignment_released === (index === 0) &&
      row.participants?.some(member => member.id === input.providers[1].id));
    const view = ui.providerOrderPresentation(changed);
    assert.equal(changed.status, 'IN_SERVICE');
    assert.equal(changed.my_assignment_settled, index === 0 && input.settled);
    if (index === 0) assert.deepEqual(view.actions, []);
    else assert.equal(view.actions.includes('COMPLETE'), changed.allowed_actions.includes('COMPLETE'));
    console.log(JSON.stringify({ phase: 'passed', observer: index, ...order.counters() }));
  } finally { order.dispose(); }
}

export async function transferRealtime(input) {
  assert.ok(input.wsBase, 'realtime transfer must use isolated Redis and Daphne');
  const observers = input.providers.map((_, observer) => {
    const child = spawn(process.execPath, [fileURLToPath(import.meta.url), 'transfer-observer'], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    let stderr = '', passed = false;
    child.stderr.on('data', chunk => { stderr += chunk; });
    let ready;
    const initialized = new Promise(resolve => { ready = resolve; });
    createInterface({ input: child.stdout }).on('line', line => {
      try { const value = JSON.parse(line); if (value.phase === 'ready') ready(); if (value.phase === 'passed') passed = value; } catch { stderr += line; }
    });
    const finished = new Promise((resolve, reject) => child.on('exit', code => {
      if (code === 0 && passed) resolve(passed);
      else reject(new Error(`React transfer observer ${observer} failed (${code}): ${stderr}`));
    }));
    // Attach immediately: an initialization failure is reported by this promise.
    finished.catch(() => {});
    child.stdin.end(JSON.stringify({ ...input, observer }));
    return { child, initialized, finished };
  });
  let timer;
  try {
    await Promise.race([Promise.all(observers.map(value => value.initialized)),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('React transfer observers did not reach real PONG and initial HTTP state')), 20000); })]);
    clearTimeout(timer);
    await raw(input.staffToken, `/admin/orders/${input.orderId}/transfer/`, 'POST', { transfers: [{
      old_provider_id: input.providers[0].id, new_provider_id: input.providers[1].id,
      split_type: 'PERCENT', split_value: 40, penalty_amount: 0 }], reason: '隔离实时转单' });
    const result = await Promise.all(observers.map(value => value.finished));
    assert.equal(result.length, 3);
    assert.ok(result.every(value => value.events > 0 && value.reads > 0));
    console.log(JSON.stringify({ reactTransfer: 'passed', realtime: true, observers: result }));
  } finally { clearTimeout(timer); for (const { child } of observers) if (child.exitCode === null) child.kill(); }
}

if (process.argv[2] === 'transfer-observer') {
  try { await transferObserver(await readInput()); }
  catch (error) { console.error(error); process.exitCode = 1; }
}
