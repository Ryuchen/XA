// Use the actual admin application and isolated Django HTTP service; no business responses are mocked.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const build = path.resolve(process.env.XA_ADMIN_FEE_BUILD || '.tools/admin-payment-fee-build-20261101');
  const target = new URL(input.api);
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
      proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
    }
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(build, pathname.startsWith('/assets/') ? pathname.slice(1) : 'index.html');
    if (!file.startsWith(build + path.sep)) { res.writeHead(404).end(); return; }
    fs.readFile(file, (error, data) => {
      if (error) { res.writeHead(404).end(); return; }
      const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': type }).end(data);
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/payment-fee`, { waitUntil: 'domcontentloaded' });
    await page.getByText('尚未配置', { exact: true }).waitFor({ timeout: 30000 });
    const form = page.locator('.fee-form');
    await form.getByLabel('微信支付商户号').fill('1234567890');
    await form.getByLabel('小程序 AppID').fill('wx1234567890abcdef');
    await form.getByLabel(/商户费率/).fill('0.6');
    await form.getByLabel('向老板报价的手续费计算基数').selectOption('PAYMENT_TOTAL');
    await form.getByLabel('手续费取整方式').selectOption('UP');
    await page.getByRole('button', { name: '保存配置' }).click();
    await page.getByText('当前配置版本 1', { exact: true }).waitFor();
    const preview = page.getByRole('region', { name: '手续费试算' });
    await preview.locator('input').fill('100');
    await preview.getByRole('button', { name: '试算已保存配置' }).click();
    const estimate = preview.getByRole('status');
    await estimate.getByText('试算版本 1', { exact: true }).waitFor();
    for (const [label, value] of [['服务金额', '100.00 元'], ['预计手续费', '0.61 元'], ['预计合计', '100.61 元']]) {
      await estimate.getByText(label, { exact: true }).locator('..').getByText(value, { exact: true }).waitFor();
    }
    const history = page.getByRole('region', { name: '费率变更记录' });
    await history.getByText('版本 1', { exact: true }).waitFor();
    assert.equal(await history.isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Real 375px fee configuration, preview and history passed.');
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
