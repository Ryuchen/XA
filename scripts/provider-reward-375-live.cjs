// Real provider workbench rendered against isolated authenticated order APIs.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type':
    { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const person = input.provider;
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, person);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/wallet/index`);
    await page.getByText('可提现兴安币', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('200币', { exact: true }).first().waitFor({ timeout: 15000 });
    await page.locator('[aria-pressed]').filter({ hasText: /^奖惩$/ }).click();
    await page.getByText(input.reason, { exact: true }).waitFor({ timeout: 15000 });
    await page.getByText('+300币', { exact: true }).first().waitFor();
    await page.getByText(input.penaltyReason, { exact: true }).waitFor();
    await page.getByText('-100币', { exact: true }).first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []); await context.close();
    console.log('Provider wallet displays actual reward at 375px');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
