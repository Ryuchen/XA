// Keep the native owner detail controller open while staff refunds an isolated order.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const net = require('node:net'), { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map();
let signalOpen;
const connected = new Promise(resolve => { signalOpen = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.api + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
  connectSocket({ url }) {
    const parsed = new URL(url), base = new URL(input.api);
    const socket = new WebSocket(`ws://${base.host}${parsed.pathname}${parsed.search}`);
    return { onOpen(fn) { socket.addEventListener('open', () => { fn(); signalOpen(); }); },
      onMessage(fn) { socket.addEventListener('message', event => fn({ data: event.data })); },
      onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
      send({ data }) { socket.send(data); }, close() { socket.close(); } };
  },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
const realtime = require(path.join(root, 'core/realtime'));
let browser, vite;
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
page.onLoad({ id: input.orderId }); page._visible = true;
async function waitFor(predicate, message) {
  for (let i = 0; i < 200; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 25)); }
  throw Error(message + ': ' + JSON.stringify(page.data.order));
}
async function staffRequest(route, method = 'GET', body) {
  const response = await fetch(input.api + route, { method, headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
}
(async () => {
  await page.load(); assert.equal(page.data.order.payment_status, input.before);
  realtime.show(); await connected;
  await waitFor(() => !page.data.loading && page.data.order?.payment_status === input.before, 'initial order unavailable');
  let dashboard;
  if (input.dashboard) {
    const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
    const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
    const adminRoot = path.resolve('apps/admin-web');
    vite = spawn(process.execPath, [path.join(adminRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
      { cwd: adminRoot, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite unavailable');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    dashboard = await context.newPage();
    await dashboard.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const wsReady = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('dashboard websocket unavailable')), 15000);
      dashboard.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await dashboard.goto(`${base}/dashboard`); await wsReady;
    await dashboard.locator('article').filter({ hasText: '累计订单净额' }).getByText(input.dashboard.before, { exact: true }).waitFor({ timeout: 30000 });
  }
  const query = input.amount ? `?refund_amount=${input.amount}` : '';
  const preview = await staffRequest(`/api/admin/orders/${input.orderId}/refund-preview/${query}`);
  await staffRequest(`/api/admin/orders/${input.orderId}/refund/`, 'POST', {
    reason: '隔离退款实时验收', client_request_id: input.requestId, plan_token: preview.plan_token,
    ...(input.amount ? { refund_amount: input.amount } : {}),
  });
  await waitFor(() => !page.data.loading && page.data.order?.payment_status === input.after, 'refund did not refresh owner page');
  assert.equal(page.data.order.refundAmountText, input.refunded);
  assert.equal(page.data.order.refundRemainingText, input.remaining);
  assert.equal(page.data.order.refundRecords.length, input.recordCount);
  if (dashboard) {
    await dashboard.locator('article').filter({ hasText: '累计订单净额' }).getByText(input.dashboard.after, { exact: true }).waitFor({ timeout: 15000 });
    await dashboard.locator('.db-daily-list li').last().getByText(`净额 ${input.dashboard.after.replace('币', ' 币')}`, { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await dashboard.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const scopedResponse = dashboard.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('exclude_direct_test') === '1' && !!url.searchParams.get('created_day');
    }, { timeout: 15000 });
    await dashboard.locator('.db-daily-list li').last().getByRole('button').click();
    const scoped = await (await scopedResponse).json();
    assert.equal(scoped.code, 0); assert.equal(scoped.data.exclude_direct_test_applied, true);
    assert.equal(scoped.data.total, 5);
    await dashboard.getByText('工作台口径：不含直接支付测试单', { exact: true }).waitFor({ timeout: 15000 });
    const allSourcesResponse = dashboard.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && !url.searchParams.has('exclude_direct_test') && !!url.searchParams.get('created_day');
    }, { timeout: 15000 });
    await dashboard.getByRole('button', { name: '查看全部来源' }).click();
    const allSources = await (await allSourcesResponse).json();
    assert.equal(allSources.code, 0); assert.equal(allSources.data.exclude_direct_test_applied, undefined);
    await dashboard.getByText('工作台口径：不含直接支付测试单', { exact: true }).waitFor({ state: 'hidden' });
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(async () => { realtime.hide(); page.onUnload(); if (browser) await browser.close(); if (vite) vite.kill(); });
