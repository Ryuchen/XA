// Exercise the real provider H5 and isolated API after a staff transfer.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-transfer-settlement-h5-20261101'), target = new URL(input.api);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    for (const person of input.providers) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      }, person);
      const page = await context.newPage();
      await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
      await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
      await page.getByText(input.fullRefund ? '已取消' : input.completed ? '已完成' : '服务中', { exact: true }).first().click({ timeout: 20000 });
      const card = page.locator('[class*="orderCard"]').filter({ hasText: input.orderNo });
      await card.waitFor({ timeout: 15000 });
      assert.equal(await card.getByText('本人已转出', { exact: true }).count(), person.released ? 1 : 0);
      assert.equal(await card.getByText(person.income, { exact: true }).count(), 1);
      if (input.participantText) assert.ok((await card.innerText()).includes(input.participantText));
      else assert.equal(await card.getByText('隔离替补陪玩', { exact: true }).count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'horizontal overflow');
      await card.getByRole('button', { name: /收益与结算/ }).click();
      const settledNotice = card.getByText('转单份额已结算，实际净收益及冻结以钱包流水为准', { exact: true });
      if (person.settledNotice) await settledNotice.waitFor({ state: 'visible', timeout: 5000 });
      else assert.equal(await settledNotice.count(), 0);
      if (person.waitingNotice) await card.getByText(person.waitingNotice, { exact: true }).waitFor({ state: 'visible', timeout: 5000 });
      if (input.partialRefund) {
        await card.getByText('实际净收益及剩余冻结以钱包流水为准', { exact: true }).waitFor({ state: 'visible' });
        await card.getByText(`服务净收益 ${person.netIncome}币`, { exact: true }).waitFor({ state: 'visible' });
        await context.close();
        continue;
      }
      if (input.fullRefund) {
        await card.getByText('已退款', { exact: true }).waitFor({ state: 'visible' });
        await card.getByText('订单已取消/退款，实际净收益以钱包流水为准', { exact: true }).waitFor({ state: 'visible' });
        await card.getByText('服务净收益 0币', { exact: true }).waitFor({ state: 'visible' });
        assert.equal(await card.getByText('原预计单钱', { exact: true }).count(), 1);
        await context.close();
        continue;
      }
      const report = card.getByText('查看 / 补充报单', { exact: true });
      if (!person.canReport) {
        assert.equal(await report.count(), 0);
        assert.equal(await card.getByText('转单后本人无结算份额，无需提交报单', { exact: true }).count(), 1);
        assert.equal(await card.getByText('完成订单', { exact: true }).count(), 0);
      } else {
        if (person.released) assert.equal(await card.getByText('完成订单', { exact: true }).count(), 0);
        await report.click();
        await page.waitForURL(/pages\/report\/index/, { timeout: 10000 });
      }
      await context.close();
    }
    console.log('375px transferred-order cards verified against isolated real API');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
