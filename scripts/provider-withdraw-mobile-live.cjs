// Provider H5 rendered against the isolated real withdrawal APIs.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-withdraw-h5-v2-20261101');
const target = new URL(input.base);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, input);
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/#/pages/wallet/index`);
    await page.locator('[aria-pressed]').filter({ hasText: /^提现$/ }).click({ timeout: 15000 });
    try { await page.getByText('已打款 ¥196.00', { exact: true }).waitFor({ timeout: 10000 }); }
    catch (error) { throw new Error(`${error.message}\n${await page.locator('body').innerText()}`); }
    await page.getByText('打款凭证：ISOLATED-QA-TRANSFER', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const folder = path.resolve('.tools/provider-withdraw-mobile-20261101'); fs.mkdirSync(folder, { recursive: true });
    await page.getByText('打款凭证：ISOLATED-QA-TRANSFER', { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(folder, 'wallet.png'), fullPage: true });
    await page.goto(`${base}/#/pages/messages/index`);
    await page.getByText('提现审核通过', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByText(/¥196\.00/).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Real provider withdrawal receipt and message rendered at 375px.');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
