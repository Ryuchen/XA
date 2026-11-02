// Render the actual admin page against an isolated Django server at 375px.
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
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let socketReady;
    const ready = new Promise(resolve => { socketReady = resolve; });
    page.on('websocket', socket => socket.on('framesent', socketReady));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    if (input.legacyTeam) {
      await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
      const tile = page.getByRole('button', { name: /旧版双陪待核对/ });
      await tile.waitFor({ timeout: 30000 });
      assert.match(await tile.innerText(), /1\s*单/);
      await tile.click();
      await page.getByText('旧版双陪待核对 · 已接单或服务中', { exact: true }).waitFor();
    } else await page.goto(`${base}/orders`, { waitUntil: 'domcontentloaded' });
    const card = page.locator('.od-mobile-card').filter({ hasText: input.orderNo });
    await card.waitFor({ timeout: 30000 });
    await card.getByRole('button', { name: '查看详情' }).click();
    const drawer = page.locator('.el-drawer');
    if (input.legacyTeam) {
      await drawer.getByText('此双陪单使用旧版履约流程', { exact: true }).waitFor();
      await drawer.getByText('订单可能由一名陪玩的截图推进；请与双方核对入队、结单凭证和服务进度。', { exact: true }).waitFor();
      assert.equal(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await drawer.getByRole('button', { name: '核实后启用双方凭证' }).click();
      const confirmation = page.locator('.el-message-box');
      await confirmation.getByText(/请先与两位陪玩和老板核实服务尚未开始/).waitFor();
      await confirmation.getByRole('button', { name: '已核实，启用' }).click();
      await drawer.getByText('此双陪单使用旧版履约流程', { exact: true }).waitFor({ state: 'hidden' });
      await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: /已支付待接单/ }).waitFor();
      assert.equal(await page.getByRole('button', { name: /旧版双陪待核对/ }).count(), 0);
      assert.deepEqual(errors, []);
      console.log('Legacy team workflow can be confirmed and enabled at 375px'); return;
    }
    await drawer.getByText('处理说明：客服已与老板沟通', { exact: true }).waitFor();
    assert.equal(await drawer.locator('.od-aftersale-history').count(), 0);
    if (input.realtime) await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(Error('staff websocket did not connect')), 15000))]);
    const response = await fetch(`${input.api}/api/orders/orders/${input.orderId}/aftersale/`, {
      method: 'POST', headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: '第二次核对服务', client_request_id: 'history-mobile-reopen' }),
    });
    const reopened = await response.json(); assert.equal(reopened.code, 0, JSON.stringify(reopened));
    if (!input.realtime) await drawer.getByRole('button', { name: '刷新订单', exact: true }).click();
    await drawer.getByText('申请说明：第二次核对服务', { exact: true }).waitFor();
    const history = drawer.locator('.od-aftersale-history');
    await history.locator('summary').click();
    await history.getByText('申请说明：仅客服核对的具体售后说明', { exact: true }).waitFor();
    await history.getByText('处理说明：客服已与老板沟通', { exact: true }).waitFor();
    assert.equal(await history.locator('.od-aftersale-history-item').count(), 1);
    assert.equal(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true, 'drawer must not overflow horizontally');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Real aftersale reopen, history disclosure and 375px drawer passed');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
