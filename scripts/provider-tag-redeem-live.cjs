// Redeem through the current provider application against the real isolated API.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), target = new URL(x.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const upstream = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    upstream.on('error', () => res.writeHead(502).end()); req.pipe(upstream); return;
  }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ id, token }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id: String(id), role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, x);
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/#/points`);
    const reward = page.locator('.reward-card').filter({ hasText: '陪玩专属TAG' });
    await reward.getByRole('button', { name: '兑换', exact: true }).click({ timeout: 15000 });
    const dialog = page.getByRole('dialog', { name: '确认兑换' });
    await dialog.getByRole('button', { name: '确认兑换', exact: true }).click();
    await page.getByText('兑换已提交', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByRole('tab', { name: '兑换记录' }).click();
    await page.locator('.claim-card').filter({ hasText: '陪玩专属TAG' }).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Provider H5 redeemed TAG using the real API');
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
