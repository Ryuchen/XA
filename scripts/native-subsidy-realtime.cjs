// Real API, Redis and WebSocket; wx only supplies the mini-program host surface.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const http = require('node:http'), net = require('node:net');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const { spawnSync } = require('node:child_process');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const store = new Map();
let socketOpened;
const connected = new Promise(resolve => { socketOpened = resolve; });
global.wx = {
  getStorageSync: key => store.get(key), setStorageSync: (key, value) => store.set(key, value),
  removeStorageSync: key => store.delete(key), getStorageInfoSync: () => ({ keys: [...store.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(x.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
  connectSocket({ url }) {
    const source = new URL(url), target = new URL(x.wsBase);
    const socket = new WebSocket(`ws://${target.host}${source.pathname}${source.search}`);
    return { onOpen(fn) { socket.addEventListener('open', () => { fn(); socketOpened(); }); },
      onMessage(fn) { socket.addEventListener('message', event => fn({ data: event.data })); },
      onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
      send({ data }) { socket.send(data); }, close() { socket.close(); } };
  },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: x.bossToken, user: { id: x.bossId, role: 'customer' } });
const realtime = require(path.join(root, 'core/realtime'));
function pageFor(filename) {
  require(path.join(root, filename));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  return page;
}
async function waitFor(predicate, description) {
  for (let i = 0; i < 400; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 25)); }
  throw Error(description);
}
const providerRoot = path.resolve(x.providerRoot), api = new URL(x.base), ws = new URL(x.wsBase);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const peer = http.request({ hostname: api.hostname, port: api.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: api.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    peer.on('error', () => res.writeHead(502).end()); req.pipe(peer); return;
  }
  const file = path.resolve(providerRoot, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(providerRoot + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, bytes) => error ? res.writeHead(404).end() : res.writeHead(200,
    { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }).end(bytes));
});
server.on('upgrade', (req, socket, head) => {
  const peer = net.connect(ws.port, ws.hostname, () => {
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (let i = 0; i < req.rawHeaders.length; i += 2) peer.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
    peer.write('\r\n'); if (head.length) peer.write(head); socket.pipe(peer).pipe(socket);
  });
  peer.on('error', () => socket.destroy()); socket.on('error', () => peer.destroy()); socket.on('close', () => peer.destroy());
});
const adminRoot = path.resolve(x.adminRoot);
const admin = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const peer = http.request({ hostname: api.hostname, port: api.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: api.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    peer.on('error', () => res.writeHead(502).end()); req.pipe(peer); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
  const file = path.resolve(adminRoot, name.startsWith('assets/') ? name : 'index.html');
  if (!file.startsWith(adminRoot + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, bytes) => error ? res.writeHead(404).end() : res.writeHead(200,
    { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }).end(bytes));
});
admin.on('upgrade', (req, socket, head) => {
  const peer = net.connect(ws.port, ws.hostname, () => {
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (let i = 0; i < req.rawHeaders.length; i += 2) peer.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
    peer.write('\r\n'); if (head.length) peer.write(head); socket.pipe(peer).pipe(socket);
  });
  peer.on('error', () => socket.destroy()); socket.on('error', () => peer.destroy()); socket.on('close', () => peer.destroy());
});
(async () => {
  const order = pageFor('pages/order-detail/index.js');
  const coupons = pageFor('pages/coupons/index.js');
  let browser;
  try {
    await Promise.all([server, admin].map(instance => new Promise(resolve => instance.listen(0, '127.0.0.1', resolve))));
    order.onLoad({ id: x.orderId }); order._visible = true; await order.load();
    coupons.onLoad({ tab: 'mine' }); coupons._visible = true; await coupons.load();
    assert.equal(order.data.order.refundRecords[0].replenishmentText, '报酬结算后，按会员权益发放下一张券');
    assert.equal(coupons.data.items.filter(item => item.scope === 'SAFE_BOX').length, 0);
    realtime.show();
    await Promise.race([connected, new Promise((_, reject) => setTimeout(() => reject(Error('WebSocket 未连接')), 12000))]);
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    async function providerPage(route) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ id, token }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '实时陪玩', username: '实时陪玩' } }));
      }, { id: x.providerId, token: x.providerToken });
      const page = await context.newPage();
      const connected = new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('陪玩端 WebSocket 未连接')), 15000);
        page.on('websocket', socket => socket.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } })); });
      await page.goto(`http://127.0.0.1:${server.address().port}/#/${route}`); await connected;
      return page;
    }
    const playerOrder = await providerPage(`orders?order=${x.orderId}`);
    const card = playerOrder.locator('.order-card').filter({ hasText: x.orderNo });
    await card.getByText('本人协商报酬待结算，请查看钱包或联系客服。', { exact: true }).waitFor();
    const wallet = await providerPage('wallet');
    await wallet.locator('.balance').getByText('0 币', { exact: true }).waitFor();
    async function adminPage(route) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.staffToken);
      const page = await context.newPage();
      const ready = new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('管理端 WebSocket 未连接')), 15000);
        page.on('websocket', socket => socket.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } })); });
      await page.goto(`http://127.0.0.1:${admin.address().port}/${route}`); await ready; return page;
    }
    const adminWallet = await adminPage('wallets'), adminDashboard = await adminPage('dashboard');
    const funds = adminWallet.getByRole('region', { name: '平台补贴资金' });
    const dashboard = adminDashboard.getByRole('button', { name: /待结算平台补贴/ });
    const subsidyMetric = adminDashboard.locator('.ap-kpi').filter({ hasText: '今日平台补贴净支出' });
    await subsidyMetric.getByText('0币', { exact: true }).waitFor();
    await funds.getByText('待结算 1 单 · 800 币', { exact: true }).waitFor();
    await dashboard.getByText('800 币 · 可能等待审核或售后期', { exact: true }).waitFor();
    const response = await fetch(x.base + '/api/admin/wallets/platform-topup/', { method: 'POST',
      headers: { Authorization: 'Bearer ' + x.staffToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: 1, reference: 'realtime-offline-001', client_request_id: 'realtime-offline-001' }) });
    const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
    // Invoke the same business task used by Celery; the independent beat test verifies scheduling.
    const task = spawnSync(x.python, ['-c', 'import django; django.setup(); from engagement.tasks import retry_completion_subsidies; assert retry_completion_subsidies.run() == 1'],
      { cwd: x.backend, env: process.env, encoding: 'utf8', timeout: 30000 });
    assert.equal(task.status, 0, task.stderr || task.stdout);
    await waitFor(() => order.data.order?.refundRecords?.[0]?.replenishmentText === '报酬已结算，会员权益可在我的券查看', '订单页面未实时刷新');
    await waitFor(() => coupons.data.items.some(item => item.scope === 'SAFE_BOX' && item.id !== x.voucherId), '券页面未实时刷新');
    await card.getByText('本人协商报酬已结算，可用和冻结金额请查看钱包。', { exact: true }).waitFor({ timeout: 15000 });
    await wallet.locator('.balance').getByText('800 币', { exact: true }).waitFor({ timeout: 15000 });
    await wallet.locator('.record').filter({ hasText: x.orderNo }).getByText('+800 币', { exact: true }).waitFor({ timeout: 15000 });
    await funds.getByText('待结算 0 单 · 0 币', { exact: true }).waitFor({ timeout: 15000 });
    await dashboard.getByText('0 币 · 可能等待审核或售后期', { exact: true }).waitFor({ timeout: 15000 });
    await subsidyMetric.getByText('800币', { exact: true }).waitFor({ timeout: 15000 });
    await subsidyMetric.getByText('已支付 800 · 已追回 0 币', { exact: true }).waitFor();
    for (const page of [playerOrder, wallet, adminWallet, adminDashboard]) assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await funds.getByRole('button', { name: '查看补贴流水' }).click();
    await adminWallet.waitForURL(/transactions/);
    await adminWallet.getByRole('combobox', { name: '流水类型' }).waitFor();
    assert.equal(await adminWallet.getByRole('combobox', { name: '流水类型' }).inputValue(), 'SUBSIDY_EXPENSE');
    await adminWallet.locator('.tx-card').filter({ hasText: x.orderNo }).getByText('-800 币', { exact: true }).waitFor();
    assert.equal(await adminWallet.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Open boss, provider and staff pages updated through Redis/WebSocket');
  } finally { realtime.hide(); order.onUnload(); coupons.onUnload(); if (browser) await browser.close(); server.closeAllConnections(); server.close(); admin.closeAllConnections(); admin.close(); }
})().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
