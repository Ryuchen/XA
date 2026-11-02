// Real admin cancellation prompts, boss actions and ASGI websocket.
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
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const page = await context.newPage();
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('admin websocket unavailable')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.goto(`${base}/orders`);
    const first = page.locator('.od-mobile-card').filter({ hasText: input.orders[0].order_no });
    const second = page.locator('.od-mobile-card').filter({ hasText: input.orders[1].order_no });
    await first.waitFor({ timeout: 30000 }); await connected;
    await first.getByRole('button', { name: '取消订单', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '取消订单' });
    await dialog.getByPlaceholder('取消原因（可选，最多 255 字）').fill('正在填写');
    const response = await fetch(`${input.api}/api/orders/orders/${input.orders[0].id}/cancel/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await dialog.waitFor({ state: 'hidden', timeout: 10000 });
    await first.getByText('已取消', { exact: true }).waitFor({ timeout: 10000 });
    await second.getByRole('button', { name: '取消订单', exact: true }).click();
    await dialog.getByPlaceholder('取消原因（可选，最多 255 字）').fill('客服核实后取消');
    await dialog.getByRole('button', { name: '确认取消订单' }).click();
    await second.getByText('已取消', { exact: true }).waitFor({ timeout: 10000 });
    const service = page.locator('.od-mobile-card').filter({ hasText: input.serviceOrder.order_no });
    await service.getByRole('button', { name: '开始', exact: true }).click();
    await page.getByRole('dialog', { name: '开始服务' }).getByRole('button', { name: '确认开始' }).click();
    await service.getByText('服务中', { exact: true }).waitFor({ timeout: 10000 });
    await service.getByRole('button', { name: '转单', exact: true }).click();
    const transfer = page.getByRole('dialog', { name: '订单转单' });
    try { await transfer.locator('.el-select input').first().fill('隔离替补陪玩', { timeout: 10000 }); }
    catch (error) { throw new Error(`${error.message}\n${await page.locator('body').innerText()}`); }
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: '隔离替补陪玩' }).click({ timeout: 10000 });
    await transfer.getByRole('button', { name: '核对转单' }).click();
    const confirmation = page.getByRole('dialog', { name: '核对转单' });
    await confirmation.waitFor({ timeout: 10000 });
    await page.waitForTimeout(400); // Let the dialog's entrance animation finish before visual inspection.
    assert.equal(await confirmation.evaluate(el => {
      const bounds = el.getBoundingClientRect();
      const confirm = [...el.querySelectorAll('button')].find(button => button.textContent.trim() === '确认转单');
      const action = confirm?.getBoundingClientRect();
      return bounds.left >= -1 && bounds.right <= innerWidth + 1 && !!action && action.left >= -1 && action.right <= innerWidth + 1;
    }), true, 'transfer confirmation and its action must fit the mobile viewport');
    const screenshotDir = path.resolve('.tools/admin-transfer-compact-20261101');
    fs.mkdirSync(screenshotDir, { recursive: true });
    await page.screenshot({ path: path.join(screenshotDir, 'transfer-confirmation.png') });
    const completeResponse = await fetch(`${input.api}/api/admin/orders/${input.serviceOrder.id}/complete/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const completed = await completeResponse.json(); assert.equal(completed.code, 0, JSON.stringify(completed));
    await confirmation.waitFor({ state: 'hidden', timeout: 10000 });
    await transfer.getByText('订单已变化，请关闭并刷新后核对').waitFor({ timeout: 10000 });
    await transfer.getByRole('button', { name: '关闭并刷新订单' }).click();
    await service.getByText('已完成', { exact: true }).waitFor({ timeout: 10000 });
    const newResponse = await fetch(`${input.api}/api/orders/create/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ service_id: input.serviceId, provider_id: input.providerId, client_request_id: 'order-prompt-transfer-normal' }),
    });
    const created = await newResponse.json(); assert.equal(created.code, 0, JSON.stringify(created));
    const later = page.locator('.od-mobile-card').filter({ hasText: created.data.order_no });
    await later.waitFor({ timeout: 10000 });
    await later.getByRole('button', { name: '开始', exact: true }).click();
    await page.getByRole('dialog', { name: '开始服务' }).getByRole('button', { name: '确认开始' }).click();
    await later.getByText('服务中', { exact: true }).waitFor({ timeout: 10000 });
    await later.getByRole('button', { name: '转单', exact: true }).click();
    await transfer.locator('.el-select input').first().fill('隔离替补陪玩');
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: '隔离替补陪玩' }).click({ timeout: 10000 });
    await transfer.getByRole('button', { name: '核对转单' }).click();
    await page.getByRole('dialog', { name: '核对转单' }).getByRole('button', { name: '确认转单' }).click();
    await transfer.waitFor({ state: 'hidden', timeout: 10000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
