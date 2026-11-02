const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '查看礼物单', exact: true }).waitFor({ timeout: 30000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.getByRole('button', { name: '查看礼物单', exact: true }).click();
    await page.getByText('订单类型：礼物单', { exact: true }).waitFor();
    await page.locator('.od-mobile-card').filter({ hasText: '隔离礼物' }).waitFor();
    let release, started;
    const gate = new Promise(resolve => { release = resolve; });
    const requestStarted = new Promise(resolve => { started = resolve; });
    await page.route('**/api/admin/orders/**', async route => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('actionable_staff') === '1') { started(); await gate; }
      await route.continue();
    });
    await page.getByRole('button', { name: '客服待派', exact: true }).click();
    await requestStarted;
    await page.getByText(/已筛选已支付、可派的客服订单/).waitFor({ timeout: 30000 });
    const cards = page.locator('.od-mobile-card');
    assert.equal(await cards.count(), 0, 'old gift rows must disappear while the new filter is loading');
    await page.getByText('正在加载记录…', { exact: true }).waitFor();
    release();
    await cards.filter({ hasText: input.staffNo }).waitFor({ timeout: 30000 });
    assert.equal(await cards.count(), 1);
    assert.match(await cards.first().innerText(), new RegExp(input.staffNo));
    assert.doesNotMatch(await cards.first().innerText(), new RegExp(input.publicNo));
    await cards.first().getByRole('button', { name: '派单', exact: true }).click();
    const assign = page.locator('.el-dialog:visible').filter({ hasText: '指派待接单订单' });
    await assign.locator('.el-select input').fill(input.providerName, { timeout: 10000 });
    await page.getByRole('option').filter({ hasText: input.providerName }).first().click({ timeout: 15000 });
    await assign.getByRole('button', { name: '确认派单' }).click();
    await assign.waitFor({ state: 'hidden', timeout: 15000 });
    async function get(token, route) {
      const response = await fetch(input.api + route, { headers: { Authorization: 'Bearer ' + token } });
      const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
    }
    const bossOrders = await get(input.bossToken, '/api/orders/orders/');
    const assigned = bossOrders.find(order => order.order_no === input.staffNo);
    assert.equal(assigned.status, 'GRABBED'); assert.equal(assigned.provider_name_snapshot, input.providerName);
    const providerOrders = await get(input.providerToken, '/api/orders/orders/?status=grabbed');
    assert.ok(providerOrders.some(order => order.order_no === input.staffNo));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
