const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), net = require('node:net'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), api = new URL(x.base), ws = new URL(x.wsBase);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const peer = http.request({ hostname: api.hostname, port: api.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: api.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    peer.on('error', () => res.writeHead(502).end()); req.pipe(peer); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
  const file = path.resolve(root, name.startsWith('assets/') ? name : 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
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
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.financeToken);
    const page = await context.newPage(), events = [];
    const ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('钱包页面 WebSocket 未连接')), 15000);
      page.on('websocket', socket => socket.on('framereceived', frame => {
        try { const event = JSON.parse(frame.payload); events.push(event); if (event.type === 'pong') { clearTimeout(timer); resolve(); } } catch (_) {}
      }));
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/wallets`); await ready;
    const funds = page.getByRole('region', { name: '平台补贴资金' });
    await funds.getByText('待结算 0 单 · 0 币', { exact: true }).waitFor();
    const dashboardContext = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await dashboardContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.dashboardToken);
    const dashboardPage = await dashboardContext.newPage(), dashboardEvents = [];
    const dashboardReady = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('工作台 WebSocket 未连接')), 15000);
      dashboardPage.on('websocket', socket => socket.on('framereceived', frame => {
        try { const event = JSON.parse(frame.payload); dashboardEvents.push(event); if (event.type === 'pong') { clearTimeout(timer); resolve(); } } catch (_) {}
      }));
    });
    await dashboardPage.goto(`http://127.0.0.1:${server.address().port}/dashboard`); await dashboardReady;
    const dashboard = dashboardPage.getByRole('button', { name: /待结算平台补贴/ });
    await dashboard.getByText('0 币 · 可能等待审核或售后期', { exact: true }).waitFor();
    const body = new FormData();
    body.set('completion_image', new Blob([Buffer.from(x.evidence, 'base64')], { type: 'image/png' }), 'result.png');
    const response = await fetch(`${x.base}/api/orders/orders/${x.orderId}/complete/`, {
      method: 'POST', headers: { Authorization: `Bearer ${x.playerToken}` }, body });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await funds.getByText('待结算 1 单 · 800 币', { exact: true }).waitFor({ timeout: 15000 });
    await dashboard.getByText('800 币 · 可能等待审核或售后期', { exact: true }).waitFor({ timeout: 15000 });
    const refresh = events.find(event => event.type === 'platform_funds_update');
    assert.deepEqual(refresh?.data, {});
    assert.ok(!events.some(event => event.type === 'order_status_update'));
    assert.deepEqual(dashboardEvents.find(event => event.type === 'dashboard_order_update')?.data, {});
    assert.ok(!dashboardEvents.some(event => event.type === 'order_status_update'));
    assert.ok(!(await funds.innerText()).includes(x.orderNo));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await dashboardPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Wallet-only operator receives aggregate refresh without order detail');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
