// Exercise the built provider H5 against the isolated Django live server.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');

const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_REPORT_BUILD || '.tools/provider-report-nav-h5-20261101');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data);
  });
});

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: fixture.providerToken, id: fixture.providerId });
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/report/index`);
    await page.getByText(fixture.orderNo).first().waitFor({ timeout: 15000 });
    const entry = page.getByText('前往订单', { exact: true }).first();
    await entry.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'report horizontal overflow');
    const folder = path.join(root, 'proofs');
    fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'report-375.png') });
    await entry.click();
    await page.screenshot({ path: path.join(folder, 'after-click-375.png') });
    await page.waitForURL(/#\/pages\/orders\/index/, { timeout: 10000 });
    await page.getByText(fixture.orderNo).first().waitFor({ timeout: 10000 });
    await page.getByText('开始服务', { exact: true }).first().waitFor({ timeout: 10000 });
    await page.getByText('正在查看关联订单', { exact: true }).waitFor({ timeout: 10000 });
    assert.equal(await page.getByText(fixture.otherOrderNo).count(), 0, 'other order must not be focused');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'order horizontal overflow');
    await page.screenshot({ path: path.join(folder, 'order-375.png') });
    console.log('375px built H5 opens the associated team order');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
