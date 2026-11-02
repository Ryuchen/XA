// Actual admin charts, using only isolated Django responses and local Vite.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function main() {
  const port = await freePort();
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true,
  });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
      if (i === 119) throw new Error('Vite did not start');
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origins = new Set([base, input.api]);
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const screenshotDir = path.resolve('.tools/admin-dashboard-charts-20261031');
    fs.mkdirSync(screenshotDir, { recursive: true });
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByText('服务 1 · 礼物 1 · 含未支付及取消', { exact: true }).waitFor({ timeout: 30000 });
    await page.getByText('含礼物净额 210币 · 已扣退款，非平台利润', { exact: true }).waitFor();
    const giftRows = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('order_kind') === 'GIFT' && !!url.searchParams.get('created_day');
    });
    await page.getByRole('button', { name: '查看礼物单', exact: true }).click();
    const giftData = (await (await giftRows).json()).data;
    assert.equal(giftData.order_kind_applied, 'GIFT'); assert.equal(giftData.total, 1);
    assert.equal(giftData.list[0].is_gift_snapshot, true);
    await page.getByText('订单类型：礼物单', { exact: true }).waitFor();
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const bar = page.locator('.db-charts canvas').first();
    await bar.waitFor({ timeout: 30000 });
    await bar.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    const rect = await bar.boundingBox();
    assert.ok(rect && rect.width > 100 && rect.height > 100, 'bar chart has dimensions');
    // Today is the last of seven categories; hover over the actual bar.
    await page.mouse.move(rect.x + rect.width * .90, rect.y + rect.height * .50);
    await page.locator('.db-charts article').first().locator('div[style*="z-index"]').filter({ hasText: '2' }).waitFor({ timeout: 5000 });
    await page.screenshot({ path: path.join(screenshotDir, 'bar-light.png') });
    const canvasBefore = await bar.evaluate(el => el.toDataURL());
    assert.ok(canvasBefore.length > 3000, 'bar rendered nonempty canvas');
    const dateResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && !!url.searchParams.get('created_day') && url.searchParams.get('exclude_direct_test') === '1';
    });
    await page.locator('.db-daily-list li').last().getByRole('button').click();
    const dateData = (await (await dateResponse).json()).data;
    assert.equal(dateData.total, 2, 'daily order count and drilldown differ');
    assert.equal(dateData.list.length, 2);
    await page.getByText('工作台口径：不含直接支付测试单', { exact: true }).waitFor();
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByText('服务 1 · 礼物 1 · 含未支付及取消', { exact: true }).waitFor({ timeout: 30000 });
    const statusResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('status') === 'COMPLETED' && url.searchParams.get('exclude_direct_test') === '1';
    });
    await page.getByRole('button', { name: '已完成 1 单，查看订单' }).click();
    const statusData = (await (await statusResponse).json()).data;
    assert.equal(statusData.total, 1, 'status count and drilldown differ');
    assert.equal(statusData.list[0].status, 'COMPLETED');
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await bar.waitFor({ timeout: 30000 });
    await page.getByRole('link', { name: '深色模式' }).click();
    await page.waitForFunction(() => document.documentElement.classList.contains('dark'));
    await page.waitForTimeout(400);
    const canvasDark = await page.locator('.db-charts canvas').first().evaluate(el => el.toDataURL());
    assert.notEqual(canvasDark, canvasBefore, 'theme change repaints chart');
    await page.screenshot({ path: path.join(screenshotDir, 'bar-dark.png') });
    await page.goto(`${base}/player-dashboard`, { waitUntil: 'domcontentloaded' });
    const pie = page.locator('.pd-charts article').first().locator('canvas').first();
    await pie.waitFor({ timeout: 30000 });
    await pie.scrollIntoViewIfNeeded();
    await page.getByText('游戏单 1000 币 · 礼物单 210 币', { exact: true }).waitFor();
    const pieRect = await pie.boundingBox();
    assert.ok(pieRect && pieRect.width > 100 && pieRect.height > 100, 'pie chart has dimensions');
    assert.ok((await pie.evaluate(el => el.toDataURL())).length > 3000, 'pie rendered nonempty canvas');
    await page.mouse.move(pieRect.x + pieRect.width * .67, pieRect.y + pieRect.height * .22);
    await page.getByText(/游戏单: 1000币/).waitFor({ timeout: 5000 });
    await page.screenshot({ path: path.join(screenshotDir, 'pie-dark.png') });
    await page.setViewportSize({ width: 375, height: 812 });
    await pie.scrollIntoViewIfNeeded();
    await page.waitForTimeout(400);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'mobile horizontal overflow');
    assert.ok((await pie.evaluate(el => el.toDataURL())).length > 3000, 'mobile pie remains rendered');
    await page.screenshot({ path: path.join(screenshotDir, 'pie-mobile.png') });
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByText('服务 1 · 礼物 1 · 含未支付及取消', { exact: true }).waitFor({ timeout: 30000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'dashboard mobile horizontal overflow');
    const serviceRows = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('order_kind') === 'SERVICE' && !!url.searchParams.get('created_day');
    });
    await page.getByRole('button', { name: '查看服务单', exact: true }).click();
    const serviceData = (await (await serviceRows).json()).data;
    assert.equal(serviceData.total, 1); assert.equal(serviceData.list[0].is_gift_snapshot, false);
    await page.getByText('订单类型：服务单', { exact: true }).waitFor();
    const allRows = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && !url.searchParams.get('order_kind') && !!url.searchParams.get('created_day');
    });
    await page.getByRole('button', { name: '清除类型', exact: true }).click();
    assert.equal((await (await allRows).json()).data.total, 2);
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '查看礼物单', exact: true }).click();
    await page.getByText('订单类型：礼物单', { exact: true }).waitFor();
    const pendingRows = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('actionable_staff') === '1';
    });
    await page.getByRole('button', { name: '客服待派', exact: true }).click();
    const pendingResponse = await pendingRows;
    const pendingUrl = new URL(pendingResponse.url());
    assert.equal(pendingUrl.searchParams.get('order_kind'), null, pendingResponse.url());
    assert.equal(pendingUrl.searchParams.get('created_day') || '', '', pendingResponse.url());
    assert.equal((await pendingResponse.json()).code, 0);
    assert.equal(await page.getByText('订单类型：礼物单', { exact: true }).count(), 0);
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const mobileStatus = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/orders/' && url.searchParams.get('status') === 'COMPLETED' && url.searchParams.get('exclude_direct_test') === '1';
    });
    await page.getByRole('button', { name: '已完成 1 单，查看订单' }).click();
    assert.equal((await (await mobileStatus).json()).data.total, 1);
    await page.locator('.od-mobile-card').first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'mobile drilldown horizontal overflow');
    assert.deepEqual(errors, [], JSON.stringify(errors));
    console.log('Real order charts and drilldowns verified at desktop and 375px; theme switched.');
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
