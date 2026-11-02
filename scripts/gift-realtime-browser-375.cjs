// Real H5 pages and a real ASGI websocket; only the local static/proxy server is test infrastructure.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.api), root = path.resolve(input.providerRoot);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
});
server.on('upgrade', (req, client, head) => {
  const upstream = net.connect(Number(target.port), target.hostname, () => {
    const headers = req.rawHeaders.reduce((out, value, i, all) => i % 2 ? out : out + `${value}: ${all[i + 1]}\r\n`, '');
    upstream.write(`${req.method} ${req.url} HTTP/1.1\r\n${headers}\r\n`); if (head.length) upstream.write(head);
    client.pipe(upstream).pipe(client);
  });
  upstream.on('error', () => client.destroy()); client.on('error', () => upstream.destroy());
});
async function observe(page) {
  let ready; const connected = new Promise(resolve => { ready = resolve; });
  page.on('websocket', socket => socket.on('framereceived', frame => { if (String(frame.payload).includes('pong')) ready(); }));
  return connected;
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); let browser, vite;
  try {
    const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
    const adminPort = probe.address().port; await new Promise(resolve => probe.close(resolve));
    const adminRoot = path.resolve('apps/admin-web'), adminBase = `http://127.0.0.1:${adminPort}`;
    vite = spawn(process.execPath, [path.join(adminRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(adminPort), '--strictPort'],
      { cwd: adminRoot, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('admin Vite exited');
      try { if ((await fetch(adminBase)).ok) break; } catch {}
      if (i === 119) throw Error('admin Vite unavailable');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, input.provider);
    const base = `http://127.0.0.1:${server.address().port}`, wallet = await context.newPage(), messages = await context.newPage();
    const admin = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await admin.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.dashboardToken);
    const dashboard = await admin.newPage(), dashboardReady = observe(dashboard);
    const walletReady = observe(wallet), messageReady = observe(messages);
    await Promise.all([wallet.goto(base + '/#/pages/wallet/index'), messages.goto(base + '/#/pages/messages/index'), dashboard.goto(adminBase + '/dashboard')]);
    await wallet.locator('taro-button-core:visible').filter({ hasText: /^礼物$/ }).first().click({ timeout: 20000 });
    await wallet.getByText('暂无礼物打赏', { exact: true }).waitFor({ timeout: 20000 });
    await messages.getByText('暂无消息', { exact: true }).waitFor({ timeout: 20000 });
    await dashboard.getByText('服务 0 · 礼物 0 · 含未支付及取消', { exact: true }).waitFor({ timeout: 20000 });
    await Promise.race([Promise.all([walletReady, messageReady, dashboardReady]), new Promise((_, reject) => setTimeout(() => reject(Error('websocket did not connect')), 15000))]);
    const response = await fetch(input.api + '/api/orders/gifts/send/', { method: 'POST', headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [{ gift_service_id: input.giftId, quantity: 1 }], provider_id: input.provider.id, expected_amount: 2100, client_request_id: 'gift-browser-realtime-01' }) });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await messages.getByText('收到老板礼物', { exact: true }).waitFor({ timeout: 15000 });
    await wallet.getByText('隔离实时礼物', { exact: true }).waitFor({ timeout: 15000 });
    await wallet.getByText('+168币', { exact: true }).first().waitFor({ timeout: 15000 });
    await dashboard.getByText('服务 0 · 礼物 1 · 含未支付及取消', { exact: true }).waitFor({ timeout: 15000 });
    await dashboard.getByText('含礼物净额 210币 · 已扣退款，非平台利润', { exact: true }).waitFor({ timeout: 15000 });
    const giftOrder = result.data.orders[0];
    const preview = await fetch(`${input.api}/api/admin/orders/${giftOrder.id}/refund-preview/`, { headers: { Authorization: 'Bearer ' + input.staffToken } });
    const quote = await preview.json(); assert.equal(quote.code, 0, JSON.stringify(quote));
    const refund = await fetch(`${input.api}/api/admin/orders/${giftOrder.id}/refund/`, { method: 'POST', headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: '隔离实时验收退款', plan_token: quote.data.plan_token }) });
    const refunded = await refund.json(); assert.equal(refunded.code, 0, JSON.stringify(refunded));
    await dashboard.getByText('含礼物净额 0币 · 已扣退款，非平台利润', { exact: true }).waitFor({ timeout: 15000 });
    for (const page of [wallet, messages, dashboard]) assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Open provider wallet and messages refreshed through real websocket and Redis');
  } finally { if (browser) await browser.close(); if (vite) vite.kill(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
