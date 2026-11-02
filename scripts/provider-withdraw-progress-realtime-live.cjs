// Real H5, HTTP and WebSocket; mutations use isolated staff accounts.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_WITHDRAW_H5_ROOT);
const target = new URL(input.api);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(Number(target.port), target.hostname, () => {
    const headers = req.rawHeaders.reduce((result, value, index, all) => index % 2 ? result : result + `${value}: ${all[index + 1]}\r\n`, '');
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
    }, input);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let socketReady;
    const ready = new Promise(resolve => { socketReady = resolve; });
    page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) socketReady(); }));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/wallet/index`);
    await page.locator('[aria-pressed]').filter({ hasText: /^提现$/ }).click({ timeout: 20000 });
    await page.getByText('等待客服领取', { exact: true }).waitFor({ timeout: 20000 });
    let readyTimeout;
    try {
      await Promise.race([ready, new Promise((_, reject) => { readyTimeout = setTimeout(() => reject(Error('provider websocket did not connect')), 15000); })]);
    } finally { clearTimeout(readyTimeout); }
    const post = async (action, token, body = {}) => {
      const response = await fetch(`${input.api}/api/admin/withdrawals/${input.withdrawId}/${action}/`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
    };
    const claimed = await post('claim', input.staffToken);
    await page.getByText('客服已领取，等待处理', { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText('处理中', { exact: true }).waitFor();
    await post('release-claim', input.managerToken, { confirmed_unpaid: true, reason: '隔离验收已核实未打款',
      expected_claimed_by_account: claimed.claimed_by_account, expected_claimed_at: claimed.claimed_at });
    await page.getByText('等待客服领取', { exact: true }).waitFor({ timeout: 10000 });
    const reclaimed = await post('claim', input.staffToken);
    await page.getByText('客服已领取，等待处理', { exact: true }).waitFor({ timeout: 10000 });
    await post('approve', input.staffToken, { payout_reference: 'ISOLATED-REALTIME', expected_claimed_at: reclaimed.claimed_at });
    await page.getByText('打款凭证：ISOLATED-REALTIME', { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText('已打款 ¥196.00', { exact: true }).waitFor();
    const createdResponse = await fetch(`${input.api}/api/wallet/withdraw/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(input.secondPayload),
    });
    const created = await createdResponse.json(); assert.equal(created.code, 0, JSON.stringify(created));
    const secondId = created.data.request.id;
    await page.getByText('等待客服领取', { exact: true }).waitFor({ timeout: 10000 });
    const secondClaimResponse = await fetch(`${input.api}/api/admin/withdrawals/${secondId}/claim/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const secondClaim = await secondClaimResponse.json(); assert.equal(secondClaim.code, 0, JSON.stringify(secondClaim));
    await page.getByText('客服已领取，等待处理', { exact: true }).waitFor({ timeout: 10000 });
    const rejectionResponse = await fetch(`${input.api}/api/admin/withdrawals/${secondId}/reject/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ audit_remark: '收款资料需重新核对', expected_claimed_at: secondClaim.data.claimed_at }),
    });
    const rejection = await rejectionResponse.json(); assert.equal(rejection.code, 0, JSON.stringify(rejection));
    await page.getByText('驳回：收款资料需重新核对', { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText('已退回余额', { exact: true }).waitFor();
    assert.equal(await page.locator('[class*="balance"]').first().innerText(), '4000币');
    assert.equal(await page.locator('[class*="miniValue"]').nth(1).innerText(), '0币');
    const overviewResponse = await fetch(`${input.api}/api/wallet/withdraw/`, { headers: { Authorization: `Bearer ${input.token}` } });
    const overview = await overviewResponse.json(); assert.equal(overview.code, 0, JSON.stringify(overview));
    assert.equal(overview.data.balance, 40000); assert.equal(overview.data.frozen_amount, 0);
    const statsResponse = await fetch(`${input.api}/api/admin/dashboard/stats`, { headers: { Authorization: `Bearer ${input.staffToken}` } });
    const stats = await statsResponse.json(); assert.equal(stats.code, 0, JSON.stringify(stats));
    assert.equal(stats.data.pending_withdraw_total, 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
