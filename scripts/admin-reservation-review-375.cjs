// Real production admin UI and isolated HTTP backend at 375px.
const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const build = path.resolve(process.env.XA_RESERVATION_BUILD || '.tools/admin-reservation-review-build-20261101'), target = new URL(input.api);
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
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    if (input.convert) {
      await page.goto(`${base}/reservations`, { waitUntil: 'domcontentloaded' });
      const entry = page.locator('.entry').filter({ hasText: input.reservationNo });
      await entry.waitFor({ timeout: 30000 });
      await entry.getByRole('button', { name: '查看详情' }).click();
      const drawer = page.locator('.el-drawer');
      await drawer.getByRole('button', { name: '报价并转单' }).click();
      const prompt = page.locator('.el-message-box');
      await prompt.getByText(/确认预约转单/).waitFor();
      const content = await prompt.innerText();
      for (const value of [input.serviceName, input.bossName, input.providerName, '1000 币', '北京时间']) assert.ok(content.includes(value), `missing ${value}: ${content}`);
      assert.equal(await prompt.evaluate(el => el.getBoundingClientRect().width <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 1), true);
      await prompt.getByRole('button', { name: '确认转单' }).click();
      await drawer.getByText('已转单', { exact: true }).waitFor();
      await drawer.getByRole('button', { name: '查看订单' }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      assert.deepEqual(errors, []);
      console.log('375px reservation confirmation and actual conversion passed'); return;
    }
    if (input.queue) {
      await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
      const pending = page.getByRole('button', { name: /待确认预约/ });
      await pending.waitFor({ timeout: 30000 });
      assert.match(await pending.innerText(), /3\s*条/);
      assert.match(await page.getByRole('button', { name: /已确认待转单/ }).innerText(), /1\s*条/);
      await pending.click();
      await page.getByText('按预约开始时间从近到远排列', { exact: true }).waitFor();
      await page.locator('.entry').first().getByText(input.pendingNos[0], { exact: true }).waitFor();
      const entries = page.locator('.entry');
      assert.equal(await entries.count(), 3);
      for (let i = 0; i < input.pendingNos.length; i++) assert.ok((await entries.nth(i).innerText()).includes(input.pendingNos[i]));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: /已确认待转单/ }).click();
      await page.locator('.entry').getByText(input.confirmedNo, { exact: true }).waitFor();
      assert.equal(await page.locator('.entry').count(), 1);
      assert.deepEqual(errors, []);
      console.log('Real workbench counts and nearest-first reservation drilldowns passed at 375px'); return;
    }
    await page.goto(`${base}/reservations`, { waitUntil: 'domcontentloaded' });
    await page.getByText(input.reservationNo, { exact: true }).waitFor({ timeout: 30000 });
    // Seed the same persisted marker written after a real earlier uncertain submission.
    await page.evaluate(({ key, summary }) => localStorage.setItem(key, summary), { key: `xa:reservation-create-pending:${input.staffId}`, summary: input.summary });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '代老板预约' }).click();
    const dialog = page.locator('.el-dialog.reservation-dialog');
    await dialog.getByText('预约结果未确认，请先在预约列表核对，避免重复预约。').waitFor();
    await dialog.getByText(input.summary, { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '创建预约' }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await dialog.evaluate(el => el.getBoundingClientRect().width <= innerWidth), true);
    for (const button of await dialog.locator('.el-dialog__footer button').all()) {
      const box = await button.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= 376 && box.y >= 0 && box.y + box.height <= 813, 'each action remains inside the 375px viewport');
    }
    await dialog.getByRole('button', { name: '查看预约' }).click();
    await page.getByText(input.reservationNo, { exact: true }).waitFor();
    await page.getByRole('button', { name: '代老板预约' }).click();
    await dialog.getByRole('button', { name: '已核对，继续' }).click();
    await dialog.getByRole('button', { name: '创建预约' }).waitFor();
    assert.equal(await page.evaluate(key => localStorage.getItem(key), `xa:reservation-create-pending:${input.staffId}`), null);
    assert.deepEqual(errors, []);
    console.log('375px admin reload, reservation review and explicit acknowledgement passed');
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
