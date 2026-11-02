const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) { const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); }); proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return; }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
const api = async (url, token, body) => { const response = await fetch(input.base + url, { method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }); const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data; };
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, input);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
    const card = page.locator('.order-card').filter({ hasText: input.orderNo });
    if (input.ambiguous) {
      await card.getByText('本人收益明细待核对，请查看钱包流水或联系客服。', { exact: true }).waitFor({ timeout: 15000 });
      assert.doesNotMatch(await card.innerText(), /本人服务收益已追回：|本人服务收益流水净额：/);
      await context.close(); return;
    }
    if (input.inProgress) {
      await card.getByText('服务中', { exact: true }).waitFor({ timeout: 15000 });
      await card.getByText('订单尚未结算，到账请查看钱包流水。', { exact: true }).waitFor();
      assert.match(await card.innerText(), /预计/);
      assert.doesNotMatch(await card.innerText(), /订单已结算/);
      await context.close();
      return;
    }
    await card.getByText(input.partial ? '售后处理中，已结算收益请查看钱包流水。' : '售后处理中，本单暂不结算。', { exact: true }).waitFor({ timeout: 15000 });
    const preview = await api(`/api/admin/orders/${input.orderId}/refund-preview/${input.partial ? '?refund_amount=2500' : ''}`, input.staffToken);
    await api(`/api/admin/aftersales/${input.caseId}/refund/`, input.staffToken, { resolution: '隔离售后退款', plan_token: preview.plan_token, client_request_id: 'vue-aftersale-receipt', ...(input.partial ? { refund_amount: 2500 } : {}) });
    const boss = await api(`/api/orders/orders/${input.orderId}/`, input.bossToken);
    assert.equal(boss.payment_status, input.partial ? 'PARTIALLY_REFUNDED' : 'REFUNDED');
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await card.getByText(input.partial ? '订单部分退款' : '订单已退款', { exact: true }).waitFor({ timeout: 15000 });
    await card.getByText('结算状态待核对，请查看钱包流水。', { exact: true }).waitFor();
    await card.getByText(input.partial ? '已退老板：250 币' : '已退老板：1000 币', { exact: true }).waitFor({ timeout: 5000 });
    if (input.partial) {
      await card.getByText('本人服务收益已追回：200 币', { exact: true }).waitFor();
      await card.getByText('本人服务收益流水净额：600 币', { exact: true }).waitFor();
    } else assert.doesNotMatch(await card.innerText(), /本人服务收益已追回：|本人服务收益流水净额：/);
    assert.match(await card.innerText(), /原预计/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await context.close();
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
