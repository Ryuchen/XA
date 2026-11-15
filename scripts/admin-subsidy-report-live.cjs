const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const net = require('node:net'), { spawnSync } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), api = new URL(x.base);
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
const providerRoot = path.resolve(x.providerRoot);
const provider = http.createServer((req, res) => {
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
if (x.wsBase) {
  const ws = new URL(x.wsBase);
  for (const instance of [server, provider]) instance.on('upgrade', (req, socket, head) => {
    const peer = net.connect(ws.port, ws.hostname, () => {
      peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
      for (let i = 0; i < req.rawHeaders.length; i += 2) peer.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
      peer.write('\r\n'); if (head.length) peer.write(head); socket.pipe(peer).pipe(socket);
    });
    peer.on('error', () => socket.destroy()); socket.on('error', () => peer.destroy()); socket.on('close', () => peer.destroy());
  });
}
function socketReady(page) {
  if (!x.wsBase) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('报单页面 WebSocket 未连接')), 15000);
    page.on('websocket', socket => socket.on('framereceived', frame => {
      if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); }
    }));
  });
}
(async () => {
  await Promise.all([server, provider].map(instance => new Promise(resolve => instance.listen(0, '127.0.0.1', resolve))));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.token);
    const page = await context.newPage();
    const adminReady = socketReady(page);
    await page.goto(`http://127.0.0.1:${server.address().port}/reports`);
    await adminReady;
    for (const item of x.orders) {
      const card = page.locator('.rp-mobile-card').filter({ hasText: item.orderNo });
      const expected = item.settled ? '800 币 · 已入账（可能冻结）' : '800 币 · 待平台补款';
      await card.getByText('平台补贴报酬', { exact: true }).waitFor();
      await card.getByText(expected, { exact: true }).waitFor();
      await card.getByRole('button', { name: '查看详情' }).click();
      const dialog = page.getByRole('dialog', { name: '报单详情' });
      await dialog.getByText('平台补贴报酬', { exact: true }).waitFor();
      await dialog.getByText(expected, { exact: true }).waitFor();
      await page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const pc = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await pc.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '验收陪玩', username: '验收陪玩' } }));
    }, { token: x.providerToken, id: x.providerId });
    const pp = await pc.newPage();
    const providerReady = socketReady(pp);
    await pp.goto(`http://127.0.0.1:${provider.address().port}/#/reports`);
    await providerReady;
    const waiting = pp.locator('.report').filter({ hasText: x.orders[0].orderNo });
    const settled = pp.locator('.report').filter({ hasText: x.orders[1].orderNo });
    await waiting.getByText('平台补贴资金暂不足，补足后将自动结算。', { exact: true }).waitFor();
    await settled.getByText('收益已结算，冻结及可用金额以钱包为准。', { exact: true }).waitFor();
    assert.equal(await pp.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (x.wsBase) {
      const response = await fetch(`${x.base}/api/admin/wallets/platform-topup/`, { method: 'POST',
        headers: { Authorization: `Bearer ${x.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: 8000, reference: 'report-realtime-001', client_request_id: 'report-realtime-001' }) });
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
      const task = spawnSync(x.python, ['-c', 'import django; django.setup(); from engagement.tasks import confirm_due_consumption; assert confirm_due_consumption.run() == 1'],
        { cwd: x.backend, env: process.env, encoding: 'utf8', timeout: 30000 });
      assert.equal(task.status, 0, task.stderr || task.stdout);
      const updated = page.locator('.rp-mobile-card').filter({ hasText: x.orders[0].orderNo });
      await updated.getByText('800 币 · 已入账（可能冻结）', { exact: true }).waitFor({ timeout: 15000 });
      await waiting.getByText('收益已结算，冻结及可用金额以钱包为准。', { exact: true }).waitFor({ timeout: 15000 });
    }
    console.log('Admin and provider see correct platform-funding status at 375px' + (x.wsBase ? ' and update through Redis/WebSocket' : ''));
  } finally { await browser.close(); server.closeAllConnections(); server.close(); provider.closeAllConnections(); provider.close(); }
})().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
