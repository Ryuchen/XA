// Keep a real provider H5 report page open through an ASGI refund and audit.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-team-cancel-h5-20261101');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
async function request(url, method = 'GET', body) {
  const response = await fetch(`http://127.0.0.1:19166/api${url}`, { method,
    headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  let nativePage, nativeRealtime;
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage();
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('provider websocket did not send a heartbeat')), 12000);
      page.on('websocket', ws => ws.on('framesent', () => { clearTimeout(timer); resolve(); }));
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/report/index`);
    await page.getByText(`订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('待审核', { exact: true }).first().waitFor();
    await connected;
    const nativeRoot = path.resolve('apps/boss-miniapp/native'), storage = new Map();
    let socketOpened;
    const bossConnected = new Promise(resolve => { socketOpened = resolve; });
    global.wx = {
      getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      showToast() {}, showModal: options => options.success({ confirm: true }), stopPullDownRefresh() {},
      request(options) {
        const url = new URL(options.url);
        fetch(`http://127.0.0.1:19166${url.pathname}${url.search}`, { method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
          .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
      },
      connectSocket(options) {
        const original = new URL(options.url);
        const socket = new WebSocket(`ws://127.0.0.1:19166${original.pathname}${original.search}`);
        return {
          onOpen(fn) { socket.addEventListener('open', () => { fn(); socketOpened(); }); },
          onMessage(fn) { socket.addEventListener('message', event => fn({ data: event.data })); },
          onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
          send({ data }) { socket.send(data); }, close() { socket.close(); },
        };
      },
    };
    require(path.join(nativeRoot, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
    let definition; global.Page = value => { definition = value; };
    require(path.join(nativeRoot, 'pages/order-detail/index.js'));
    nativePage = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
    for (const [key, value] of Object.entries(nativePage)) if (typeof value === 'function') nativePage[key] = value.bind(nativePage);
    nativePage.onLoad({ id: input.orderId }); nativePage._visible = true; await nativePage.load();
    assert.equal(nativePage.data.order.payment_status, 'PAID');
    nativeRealtime = require(path.join(nativeRoot, 'core/realtime')); nativeRealtime.show();
    await Promise.race([bossConnected, new Promise((_, reject) => setTimeout(() => reject(new Error('boss websocket unavailable')), 12000))]);
    const preview = await request(`/admin/orders/${input.orderId}/refund-preview/`);
    await request(`/admin/orders/${input.orderId}/refund/`, 'POST', { reason: '隔离验收终止服务',
      plan_token: preview.plan_token, client_request_id: 'report-live-refund' });
    await page.getByText('订单已取消，报单流程已停止', { exact: true }).waitFor({ timeout: 15000 });
    for (let i = 0; i < 150 && nativePage.data.order?.payment_status !== 'REFUNDED'; i++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(nativePage.data.order.payment_status, 'REFUNDED');
    assert.equal(nativePage.data.order.refundAmountText, '1000');
    assert.equal(nativePage.data.order.statusText, '已取消');
    await request(`/admin/reports/${input.reportId}/reject/`, 'POST', { audit_remark: '订单已退款，请核对' });
    await page.getByText('审核备注：订单已退款，请核对', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.getByText('提交审核', { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (nativeRealtime) nativeRealtime.hide(); if (nativePage) nativePage.onUnload(); await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
