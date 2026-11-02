// Real H5 report page against the isolated Django service with a non-China device timezone.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('.tools/provider-report-time-h5-20261101'), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true, timezoneId: 'America/New_York' });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/report/index`);
    await page.getByText(`订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 20000 });
    if (input.resubmit) {
      await page.getByText('审核备注：请补充清晰战绩截图', { exact: true }).waitFor();
      const chooser = page.waitForEvent('filechooser');
      await page.getByText('添加战绩截图', { exact: true }).click();
      const uploaded = page.waitForResponse(response => response.url().includes(`/api/wallet/reports/${input.reportId}/images/`) && response.request().method() === 'POST' && response.status() === 200);
      await (await chooser).setFiles(path.resolve('apps/boss-miniapp/native/assets/icons/chat.png'));
      assert.equal((await (await uploaded).json()).code, 0);
      await page.getByText('2/9 张', { exact: true }).waitFor();
      const submitted = page.waitForResponse(response => response.url().includes(`/api/wallet/reports/${input.reportId}/submit/`) && response.request().method() === 'POST' && response.status() === 200);
      await page.getByText('提交审核', { exact: true }).click();
      assert.equal((await (await submitted).json()).code, 0);
      await page.getByText('待审核', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      assert.deepEqual(errors, []);
      console.log('Real rejected report corrected and resubmitted in 375px H5'); return;
    }
    await page.getByText(`提交时间：${input.beijingTime}（北京时间）`, { exact: false }).waitFor();
    const refund = page.getByText(`退款 #${input.refundId} · 250币`, { exact: false });
    await refund.waitFor();
    assert.ok((await refund.innerText()).includes(`${input.refundTime}（北京时间）`));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Real submitted report shows Beijing time at 375px in New York timezone');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
