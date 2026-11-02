// Actual provider wallet and staff reward/penalty APIs on an isolated ASGI service.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root), target = new URL(input.api);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const upstream = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    upstream.on('error', () => res.writeHead(502).end()); req.pipe(upstream); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(Number(target.port), target.hostname, () => {
    const headers = req.rawHeaders.reduce((out, value, i, all) => i % 2 ? out : out + `${value}: ${all[i + 1]}\r\n`, '');
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, input.provider);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let socketReady; const ready = new Promise(resolve => { socketReady = resolve; });
    page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) socketReady(); }));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/wallet/index`);
    await page.getByText('可提现兴安币', { exact: true }).waitFor({ timeout: 20000 });
    let timer;
    try { await Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('wallet websocket did not connect')), 15000); })]); }
    finally { clearTimeout(timer); }
    if (input.gift) {
      const call = async (url, token, body) => {
        const response = await fetch(`${input.api}/api${url}`, {
          method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          ...(body ? { body: JSON.stringify(body) } : {}),
        });
        const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
      };
      await page.locator('[aria-pressed]').filter({ hasText: /^礼物$/ }).click();
      const gift = await call('/orders/gifts/send/', input.gift.bossToken, {
        gift_service_id: input.gift.serviceId, provider_id: Number(input.provider.id), quantity: 1,
        expected_amount: 10000, client_request_id: 'live-wallet-gift',
      });
      await page.getByText('隔离实时礼物', { exact: true }).waitFor({ timeout: 15000 });
      await page.getByText('+800币', { exact: true }).first().waitFor();
      const detail = await call(`/admin/orders/${gift.id}/`, input.staffToken);
      assert.equal(detail.amount, 10000);
      const plan = await call(`/admin/orders/${gift.id}/refund-preview/`, input.staffToken);
      await call(`/admin/orders/${gift.id}/refund/`, input.staffToken, {
        reason: '隔离赠礼退款', plan_token: plan.plan_token, client_request_id: 'live-wallet-gift-refund',
      });
      await page.getByText('已退款 · 收益已冲回', { exact: true }).waitFor({ timeout: 15000 });
      await page.getByText('0币', { exact: true }).first().waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      assert.deepEqual(errors, []);
      return;
    }
    const send = async (type, amount, reason) => {
      const response = await fetch(`${input.api}/api/admin/escorts/${input.profileId}/dispose/`, {
        method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ dispose_type: type, amount, reason, client_request_id: `live-${type.toLowerCase()}` }),
      });
      const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
    };
    await page.locator('[aria-pressed]').filter({ hasText: /^奖惩$/ }).click();
    await send('REWARD', 3000, '实时服务奖励');
    await page.getByText('实时服务奖励', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByText('+300币', { exact: true }).first().waitFor();
    await page.getByText('300币', { exact: true }).first().waitFor();
    await send('PENALTY', 1000, '实时服务罚款');
    await page.getByText('实时服务罚款', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByText('-100币', { exact: true }).first().waitFor();
    await page.getByText('200币', { exact: true }).first().waitFor();
    await page.locator('[aria-pressed]').filter({ hasText: /^流水$/ }).click();
    await page.getByText('实时服务奖励', { exact: true }).waitFor();
    await page.getByText('实时服务罚款', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
