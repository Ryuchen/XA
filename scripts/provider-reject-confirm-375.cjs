// Real H5 and isolated backend; actions and reads are not mocked.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(process.env.XA_PROVIDER_REJECT_H5_ROOT || '.tools/provider-reject-confirm-h5-20261101'), target = new URL(input.base);
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
const adminRoot = path.resolve(process.env.XA_PROVIDER_REJECT_ADMIN_ROOT || '.tools/admin-reservation-review-build-20261101');
const adminServer = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(adminRoot, pathname.startsWith('/assets/') ? pathname.slice(1) : 'index.html');
  if (!file.startsWith(adminRoot + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function read(token, pathname) {
  const response = await fetch(input.base + pathname, { headers: { Authorization: `Bearer ${token}` } });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
}
function checkNative(status, providerName) {
  if (!input.nativeCheck) return;
  const result = spawnSync(process.execPath, [path.resolve('.tools/native-recharge-checkout-375-20261102/native-direct-reject-view-375.cjs')], {
    input: JSON.stringify({ base: input.base, token: input.bossToken, id: input.bossId, orderId: input.orderId, status, providerName }),
    encoding: 'utf8', timeout: 40000,
  });
  assert.equal(result.status, 0, result.stdout + result.stderr + (result.error?.message || ''));
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await new Promise(resolve => adminServer.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
    await page.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click({ timeout: 20000 });
    const order = page.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo });
    await order.getByText('拒单', { exact: true }).click();
    await page.getByText('时间冲突', { exact: true }).click();
    let dialog = page.getByRole('dialog', { name: '拒单原因' });
    await dialog.getByText(!input.publicRoute ? '拒单后订单将退回客服重新安排，不进入公开接单池' : input.selectionMode ? '拒单后订单回到公开池，陪玩报名后由老板选择' : '拒单后订单回到公开池，其他陪玩可直接接单').waitFor();
    assert.equal(await dialog.locator('textarea').inputValue(), '时间冲突');
    assert.equal((await read(input.bossToken, `/api/orders/orders/${input.orderId}/`)).status, 'GRABBED');
    await dialog.getByText('取消', { exact: true }).click();
    assert.equal((await read(input.bossToken, `/api/orders/orders/${input.orderId}/`)).status, 'GRABBED');
    await order.getByText('拒单', { exact: true }).click();
    await page.getByText('时间冲突', { exact: true }).click();
    dialog = page.getByRole('dialog', { name: '拒单原因' });
    await dialog.getByText('确认拒单', { exact: true }).click();
    await order.waitFor({ state: 'hidden', timeout: 15000 });
    const boss = await read(input.bossToken, `/api/orders/orders/${input.orderId}/`);
    assert.deepEqual([boss.status, boss.dispatch_route], ['PENDING', input.publicRoute ? 'PUBLIC' : 'STAFF']);
    const queue = await read(input.staffToken, '/api/admin/orders/?actionable_staff=1');
    assert.equal(queue.list.some(row => row.id === input.orderId), !input.publicRoute);
    const logs = await read(input.staffToken, `/api/admin/orders/${input.orderId}/logs/`);
    assert.ok(logs.some(row => row.action === 'REJECT' && row.reason === '时间冲突'));
    if (!input.publicRoute) {
      const adminContext = await browser.newContext({ viewport: { width: 375, height: 812 } });
      await adminContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
      const admin = await adminContext.newPage();
      await admin.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await admin.goto(`http://127.0.0.1:${adminServer.address().port}/orders?find_order=${input.orderNo}`, { waitUntil: 'domcontentloaded' });
      const card = admin.locator('.od-mobile-card').filter({ hasText: input.orderNo });
      await card.getByRole('button', { name: '查看详情' }).click({ timeout: 20000 });
      const drawer = admin.locator('.el-drawer');
      await drawer.getByText(/时间冲突/).waitFor();
      await drawer.getByRole('button', { name: '指派陪玩' }).click();
      const assign = admin.locator('.el-dialog:visible').filter({ hasText: '指派待接单订单' });
      const search = assign.locator('.el-select input');
      try { await search.fill(input.secondName, { timeout: 10000 }); }
      catch (error) { throw new Error(`${error.message}\nvisible dialogs: ${await admin.locator('.el-dialog:visible').allTextContents()}`); }
      await admin.getByRole('option').filter({ hasText: input.secondName }).first().click({ timeout: 15000 });
      await assign.getByRole('button', { name: '确认派单' }).click();
      await assign.waitFor({ state: 'hidden' });
      const accepted = await read(input.bossToken, `/api/orders/orders/${input.orderId}/`);
      assert.equal(accepted.status, 'GRABBED');
      await adminContext.close();
      const nextContext = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await nextContext.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩二', username: '隔离陪玩二', avatar: '' } }));
      }, { token: input.secondToken, id: input.secondId });
      const nextPage = await nextContext.newPage();
      await nextPage.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await nextPage.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
      await nextPage.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click();
      await nextPage.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo }).getByText('开始服务', { exact: true }).waitFor({ timeout: 15000 });
      await nextContext.close();
    }
    if (input.publicRoute) {
      assert.equal(boss.requires_boss_selection, !!input.selectionMode, 'public mode survives rejection');
      if (!input.selectionMode) checkNative('PENDING', '等待陪玩直接接单');
      const second = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await second.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩二', username: '隔离陪玩二', avatar: '' } }));
      }, { token: input.secondToken, id: input.secondId });
      const replacement = await second.newPage();
      await replacement.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await replacement.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
      const pool = replacement.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo });
      if (input.selectionMode) {
        await pool.getByText('报名抢单', { exact: true }).click({ timeout: 20000 });
        const application = replacement.getByRole('dialog', { name: '报名抢单' });
        const submitted = replacement.waitForResponse(response => new URL(response.url()).pathname === `/api/orders/orders/${input.orderId}/grab/` && response.request().method() === 'POST');
        await application.getByText('提交报名', { exact: true }).click();
        const applied = await (await submitted).json(); assert.equal(applied.code, 0, JSON.stringify(applied));
        const appId = applied.data.my_applications[0].id;
        const select = await fetch(input.base + `/api/orders/orders/${input.orderId}/applications/${appId}/select/`, { method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' }, body: '{}' });
        const selected = await select.json(); assert.equal(selected.code, 0, JSON.stringify(selected));
      } else await pool.getByText('抢单', { exact: true }).click({ timeout: 20000 });
      await replacement.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click();
      await replacement.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo }).getByText('开始服务', { exact: true }).waitFor({ timeout: 15000 });
      const accepted = await read(input.bossToken, `/api/orders/orders/${input.orderId}/`);
      assert.deepEqual([accepted.status, accepted.requires_boss_selection], ['GRABBED', !!input.selectionMode]);
      if (!input.selectionMode) checkNative('GRABBED', input.secondName);
      await second.close();
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Preset rejection confirmed at 375px and returned to real staff queue');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); adminServer.closeAllConnections(); adminServer.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); adminServer.close(); });
