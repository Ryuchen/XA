// Production H5 and real isolated API: notice -> linked order at 375px.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_AFTERSALE_BUILD || '.tools/provider-aftersale-h5-20261101');
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
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.token, id: input.providerId });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/messages/index`);
    const notice = page.getByText(input.quickTeam ? '客服派单给你' : '订单售后已处理', { exact: true }).first();
    await notice.waitFor({ timeout: 20000 });
    await notice.click();
    await page.getByText('查看关联订单', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.getByText('查看关联订单', { exact: true }).click();
    await page.waitForURL(/#\/pages\/orders\/index/);
    await page.getByText('正在查看关联订单', { exact: true }).waitFor();
    await page.getByText(input.orderNo, { exact: true }).first().waitFor();
    if (input.quickTeam) {
      await page.getByText(`本单陪玩`, { exact: true }).waitFor();
      for (const name of input.names) await page.getByText(name, { exact: false }).first().waitFor();
      if (input.submitEntry) {
        const chooser = page.waitForEvent('filechooser');
        await page.getByRole('button', { name: '开始服务', exact: true }).click();
        await (await chooser).setFiles({ name: 'isolated-entry.png', mimeType: 'image/png', buffer: Buffer.from(input.image, 'base64') });
        await page.getByText('截图已提交，等待队友提交', { exact: true }).waitFor({ timeout: 10000 });
        await page.getByText('入队截图 · 1/2 已提交', { exact: false }).waitFor();
      }
    } else await page.getByText('售后已处理，结算状态请查看下方收益与结算。', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (input.checkReport) {
      await page.getByRole('button', { name: '报单', exact: true }).click();
      await page.waitForURL(/#\/pages\/report\/index/);
      const report = page.locator('[class*="__card___"]').filter({ hasText: input.orderNo });
      await report.waitFor();
      await report.getByText('售后已处理，收益状态以本单结算信息为准。', { exact: true }).waitFor();
      await report.getByText('凭证已通过，等待全部审核及售后窗口结束', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    }
    assert.deepEqual(errors, []);
    console.log('Real resolved aftersale message opens the associated order at 375px');
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
